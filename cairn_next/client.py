"""Logging to artifacts and finding them again."""

import contextlib
import contextvars
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import tomllib
import uuid
from collections.abc import Mapping
from pathlib import Path

from . import cairn_pb2 as pb
from . import closing, values
from .console import capture
from .errors import Conflict, Invalid, NotFound, Pending
from .outbox import FINISH, LEAVE, LIMIT, RECORD, Outbox, clone, digest_of
from .provenance import collect
from .remote import Remote
from .store import Repository
from .types import Directory, File, Ref, typed
from .values import encode

CONVENTIONS = {"_time", "_use", "_config", "_about", "_console", "_outcome", "_error"}
OUTCOMES = ("completed", "failed", "cancelled", "incomplete")
_current = contextvars.ContextVar("cairn_next_artifact", default=None)


def settings():
    """`.cairn.toml` files from here upward (the nearest wins), then the environment."""
    found = {}
    for folder in (Path.cwd(), *Path.cwd().parents):
        # A token belongs to the repository beside it, never to one named further inside.
        if (path := folder / ".cairn/token").is_file() and "repo" not in found:
            found.setdefault("token_file", str(path))
        if (path := folder / ".cairn.toml").is_file():
            for key, value in tomllib.loads(path.read_text()).items():
                local = (key == "repo" and "://" not in value) or key == "token_file"
                found.setdefault(key, str(folder / Path(value).expanduser()) if local else value)
    for key, name in (("repo", "CAIRN_REPO"), ("project", "CAIRN_PROJECT"), ("token", "CAIRN_TOKEN"), ("provenance", "CAIRN_PROVENANCE"), ("capture_source", "CAIRN_CAPTURE_SOURCE")):
        if name in os.environ:
            found[key] = os.environ[name]
    if "token" not in found and "token_file" in found:
        found["token"] = Path(found["token_file"]).read_text().strip()
    return found


def _flag(known, key):
    """A setting that turns something off everywhere, as `key = false` or an environment variable."""
    value = known.get(key, True)
    return value if isinstance(value, bool) else value.lower() not in ("0", "false", "no", "off")


class Client:
    def __init__(self, repo=None, *, project=None, token=None, state=None):
        known = settings()
        repo = str(repo or known.get("repo") or ".cairn/repo")
        self.project = project or known.get("project") or "default"
        self.remote = "://" in repo
        self.backend = Remote(repo, token or known.get("token")) if self.remote else Repository(repo)
        self.repo, self._outbox, self._lock, self._boxes = repo, None, threading.Lock(), None
        self._provenance, self._source = (_flag(known, key) for key in ("provenance", "capture_source"))
        self._limit = int(float(known.get("outbox_gb", LIMIT / 1e9)) * 1e9)
        if self.remote:
            base = state or os.environ.get("CAIRN_STATE_DIR") or Path.home() / ".local/state/cairn"
            self._boxes = Path(base) / "next" / hashlib.sha256(repo.encode()).hexdigest()[:24]

    @property
    def outbox(self):
        """This process's own queue; only one process ever writes to it."""
        with self._lock:
            if self._outbox is None and self.remote:
                folder = self._boxes / uuid.uuid4().hex
                folder.mkdir(parents=True, mode=0o700)
                self._held = open(folder / "lock", "w")
                fcntl.flock(self._held, fcntl.LOCK_EX)
                self._outbox = Outbox(self.backend, folder, self._limit)
            return self._outbox

    def recover(self, timeout=60):
        """Send what processes that ended left behind, and say what could not be sent."""
        left = []
        for folder in sorted(self._boxes.glob("*")) if self.remote and self._boxes.is_dir() else ():
            with open(folder / "lock", "w") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    continue  # its process still runs
                box = Outbox(self.backend, folder)
                try:
                    box.flush(timeout)
                except (Pending, Conflict) as error:
                    left.append((str(folder), str(error)))
                if box.close():
                    shutil.rmtree(folder)
        return left

    def flush(self, timeout=None):
        if self._outbox:
            self._outbox.flush(timeout)

    def close(self, outcome="completed"):
        """Close what is still open: artifacts opened here finish, attached ones are left."""
        closing.end(lambda made: made._client is self, outcome)
        if self._outbox:
            empty = self._outbox.close()
            self._held.close()
            if empty:
                shutil.rmtree(self._outbox.folder, ignore_errors=True)
            self._outbox = None
        self.backend.close()

    def __enter__(self):
        return self

    def __exit__(self, _kind, error, _traceback):
        self.close(closing.outcome(error))

    # Writing

    def artifact(self, namespace, name, config=None, about=None, *, console=False, provenance=False, capture_source=True):
        """Start an artifact and log its configuration, with `provenance` the code, packages
        and command that make it (the code unless `capture_source` is off), and with
        `console` what the program prints.

        It is finished by its `with`, by `finish`, or when the program ends."""
        message = pb.Open(project=self.project, namespace=namespace, name=name)
        if self.remote:
            local, opened = self.outbox.open(message)
        else:
            message.request_id = local = uuid.uuid4().hex
            opened = self.backend.open(message)
        made = Artifact(self, opened, local)
        start = {"_about": {**(collect(capture_source=capture_source and self._source) if provenance and self._provenance else {}), **(about or {})}}
        if config is not None:
            start["_config"] = config
        made._log({key: value for key, value in start.items() if value or key == "_config"})
        closing.track(made)
        if console:
            made._console = capture(made)
        return made

    def run(self, name, config=None, about=None, *, console=True, provenance=True, capture_source=True):
        """An artifact in runs."""
        return self.artifact("runs", name, config, about, console=console, provenance=provenance, capture_source=capture_source)

    def attach(self, identifier):
        """Log to a live artifact from another process. Its finish waits until this one leaves."""
        found = self._one(identifier)
        if found.finished:
            raise Conflict("The artifact is finished")
        made = Artifact(self, found, self.outbox.attach(found.id) if self.remote else uuid.uuid4().hex)
        made._attached = True
        closing.track(made)
        return made

    # Finding

    def _one(self, ref):
        if isinstance(ref, (Artifact, Link)):
            ref = ref.id
        return self.backend.resolve(pb.Resolve(project=self.project, refs=[ref])).items[0]

    def get(self, ref):
        return Artifact(self, self._one(ref))

    def find(self, name="", *, namespace=None, where=(), order="", limit=100, offset=0, text="", state="", latest=False, values=(), project=None):
        """Artifacts by name and by the last, smallest or largest values at their paths.

        `name` is a glob, with its namespace as in `runs/train/*`. Conditions are text such
        as "loss < 0.1", or (path, operator, value); a path alone asks whether it is there.
        Paths may be `min(path)` or `max(path)`, and `tag:key` and `alias:name` look at
        labels. `order` is a path too, descending with a leading `-`; the newest come first
        without."""
        if namespace is None and name:
            namespace, _, name = name.partition("/")
            namespace = "" if namespace in ("*", "**") else namespace
        conditions = [condition(c) for c in where]
        answer = self.backend.find(pb.Find(project=project or self.project, namespaces=[namespace] if namespace else [], name=name, text=text, where=conditions,
            order=order.removeprefix("-"), descending=order.startswith("-"), limit=limit, offset=offset, state=state, latest=latest, values=list(values)))
        found = Found(Artifact(self, item) for item in answer.items)
        found.total = answer.total
        return found

    # What is not in the log

    def _label(self, ref, set_=None, remove=(), **options):
        message = pb.Label(artifact=self._one(ref).id, remove=remove, **options)
        values.encode_map(set_ or {}, message.set)
        return Artifact(self, self.backend.label(message))

    def tag(self, ref, *tags, **valued):
        """Describe an artifact by tags, with or without a value; a tag set again is replaced."""
        if twice := sorted(set(tags) & valued.keys()):
            raise Invalid(f"Given twice: {', '.join(twice)}")
        tags = {**dict.fromkeys(tags), **valued}
        if not tags or any(tag.startswith("@") for tag in tags):
            raise Invalid("Give tags, which do not start with @")
        return self._label(ref, tags)

    def untag(self, ref, *tags):
        return self._label(ref, remove=[tag for tag in tags if not tag.startswith("@")])

    def alias(self, ref, alias, *, expected=...):
        """Point an alias at a finished version, away from any other version of its name.
        With `expected`, only if it still points there (None: at nothing)."""
        options = {} if expected is ... else {"compare": True, "expected": "" if expected is None else self._one(expected).id}
        return self._label(ref, {"@" + alias: None}, **options)

    def _name(self, name):
        if hasattr(name, "namespace"):
            return pb.Aliases(project=name.project, namespace=name.namespace, name=name.name)
        namespace, _, rest = name.partition("/")
        if not rest or "@" in rest:
            raise Invalid(f"Aliases belong to a name such as runs/a, not {name!r}")
        return pb.Aliases(project=self.project, namespace=namespace, name=rest)

    def aliases(self, name):
        """Where the aliases of a name point."""
        found = self.backend.aliases(self._name(name)).items
        return {alias: Artifact(self, item) for item in found for alias in item.aliases}

    def unalias(self, name, alias):
        if found := self.aliases(name).get(alias):
            self._label(found, remove=["@" + alias])

    def delete(self, ref):
        """Hide an artifact, until it is restored or collected."""
        self.backend.delete(pb.Delete(artifact=self._one(ref).id))

    def restore(self, deleted):
        """Show a deleted artifact again, given by its ID or as found (find(state="deleted"))."""
        return Artifact(self, self.backend.delete(pb.Delete(artifact=getattr(deleted, "id", deleted), restore=True)))

    def projects(self):
        """Projects by name, with how many artifacts each namespace has and when it last changed."""
        return {p.name: {"artifacts": dict(p.artifacts), "updated": p.updated} for p in self.backend.projects(pb.Projects()).items}

    def files(self, pattern="", *, text="", project=None, limit=100):
        """Current files of every artifact, by path, glob, or part of the path."""
        answer = self.backend.files(pb.Files(project=self.project if project is None else project, pattern=pattern, text=text, limit=limit))
        return [FileInfo(self, item) for item in answer.items]


OPERATOR = re.compile(r"(\S.*?)\s+(!=|<=|>=|=|<|>|contains)\s+(.+)", re.S)


def condition(given):
    """A condition as text, or as (path, operator, value)."""
    if isinstance(given, str):
        given = given.strip()
        if match := OPERATOR.fullmatch(given):
            path, op, value = match[1], match[2], literal(match[3])
        elif not given or re.search(r"[<>=]|\s", given):
            raise Invalid(f"Conditions read like 'loss < 0.1', with spaces, or are a path: {given!r}")
        else:
            path, op, value = given, "", None
    else:
        path, op, value = given
        if op not in ("=", "!=", "<", "<=", ">", ">=", "contains"):
            raise Invalid(f"Unknown operator: {op}")
    match = re.fullmatch(r"(min|max)\((.+)\)", path)
    made = pb.Condition(path=match[2] if match else path, stat=match[1] if match else "", op=op)
    encode(value, made.value)
    return made


def literal(text):
    """A number, true, false, or text; quotes make anything text."""
    text = text.strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    if text.lower() in ("nan", "inf", "-inf"):
        return float(text)
    try:
        value = json.loads(text)
    except ValueError:
        return text
    return value if isinstance(value, (bool, int, float)) else text


class Found(list):
    total = 0


class FileInfo:
    def __init__(self, client, item):
        self.path, self.size, self.media_type = item.path, item.blob.size, item.blob.media_type
        self.digest, self.reference, self.artifact, self.ordinal = item.blob.digest.hex(), item.reference, item.artifact, item.ordinal
        self._client, self._blob = client, item.blob

    def __repr__(self):
        return f"<File {self.reference or self.path} {self.size} bytes>"

    def open(self):
        return _content(self._client, self._blob)

    def read(self):
        with self.open() as stream:
            return stream.read()


class Artifact:
    """An artifact: what it says is current, also while it is live.

    One that was opened to write has `log`, the only way to add to it; the rest is shorthand."""

    _owned = _attached = False
    _console = None

    def __init__(self, client, found, writer=None):
        self._client, self._found = client, found
        self.id, self.project, self.namespace, self.name = found.id, found.project, found.namespace, found.name
        self.created_at, self.values = found.created_at, values.decode_map(found.values)
        self._writer, self._sequence, self._closed = writer, 0, False
        if writer is not None:
            self._lock, self._beat = threading.RLock(), threading.Event()
            threading.Thread(target=self._heartbeat, name="cairn-heartbeat", daemon=True).start()

    def __repr__(self):
        return f"<{self._name(self._found)}>"

    def _name(self, found):
        return f"{self.namespace}/{self.name}" + (f"@v{found.version}" if found.version else f"@live {self.id[:12]}")

    def _now(self, *, sealed=False):
        """What the repository says now; what finishing settled is not asked again."""
        if not (sealed and self._found.finished):
            # A deleted artifact that others refer to stays readable as it was.
            with contextlib.suppress(NotFound):
                self._found = self._client._one(self.id)
        return self._found

    @property
    def ref(self):
        return self._name(self._now(sealed=True))

    @property
    def finished(self):
        return self._now(sealed=True).finished

    @property
    def completed(self):
        return self._now(sealed=True).completed

    @property
    def version(self):
        return self._now(sealed=True).version or None

    def __len__(self):
        return self._now(sealed=True).records

    def __bool__(self):
        return True

    # Its keys and its records are two things to go through: keys() and records().
    __iter__ = None

    def __contains__(self, key):
        return self.get(key, ...) is not ...

    @property
    def finished_at(self):
        return self._now(sealed=True).finished_at or None

    @property
    def heard_at(self):
        return self._now(sealed=True).heard_at

    @property
    def aliases(self):
        return list(self._now().aliases)

    @property
    def tags(self):
        return values.decode_map(self._now().tags)

    def keys(self):
        return list(self._client.backend.keys(pb.Keys(artifact=self.id)).keys)

    def last(self, *keys):
        """Current values by key, of all keys when none is named."""
        answer = self._client.backend.last(pb.Last(artifact=self.id, keys=keys or self.keys()))
        return {entry.key: self._wrap(values.decode(entry.value)) for entry in answer.entries}

    def __getitem__(self, key):
        """The current value of a key, or of a path such as `_config/lr`."""
        head, *rest = [key] if (found := self.last(key)) else key.split("/")
        found = found or self.last(head)
        if head not in found:
            raise KeyError(key)
        value = found[head]
        if rest and "/".join(rest) in value:
            return value["/".join(rest)]
        for part in rest:
            value = value[part]
        return value

    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, TypeError):
            return default

    @property
    def config(self):
        return self.get("_config", {})

    @property
    def outcome(self):
        return self.get("_outcome")

    def records(self, *, after=0, having=""):
        """(ordinal, record) in order."""
        while True:
            page = self._client.backend.records(pb.Records(artifact=self.id, after=after, having=having))
            for ordinal, data in zip(page.ordinals, page.records, strict=True):
                yield ordinal, self._wrap(values.read(data))
                after = ordinal
            if not page.more:
                return

    def follow(self, *, after=0, having="", interval=0.5):
        """Records so far, then each as it arrives, until the artifact is finished."""
        while True:
            # Asked before reading: what arrives in between is read now or next time.
            finished = self.finished
            for ordinal, record in self.records(after=after, having=having):
                yield ordinal, record
                after = ordinal
            if finished:
                return
            time.sleep(interval)

    def series(self, *paths, x=None, after=0):
        """Numbers at a path, in the order they were logged; with `x`, pairs of the records
        that have both. Several paths give a dict."""
        wanted = [*paths, *([x] if x else [])]
        answer = self._client.backend.series(pb.Series(artifact=self.id, paths=wanted, after=after))
        columns = {c.path: (list(c.ordinals), list(c.values)) for c in answer.columns}
        if x:
            at = dict(zip(*columns[x], strict=True))
            pairs = {p: [(at[o], v) for o, v in zip(*columns[p], strict=True) if o in at] for p in paths}
            return pairs[paths[0]] if len(paths) == 1 else pairs
        return columns[paths[0]][1] if len(paths) == 1 else {p: columns[p][1] for p in paths}

    def files(self, pattern="", *, text=""):
        after = ""
        while True:
            answer = self._client.backend.files(pb.Files(artifact=self.id, pattern=pattern, text=text, after=after, limit=10_000))
            for item in answer.items:
                yield FileInfo(self._client, item)
            if not answer.more:
                return
            after = answer.items[-1].path

    def file(self, path):
        for found in self.files(path):
            return found
        for found in self.files(f"{path}/data"):  # an image's or table's content
            return found
        raise NotFound(f"{self.ref} has no file {path}")

    def open(self, path):
        return self.file(path).open()

    def read(self, path):
        return self.file(path).read()

    def download(self, to, pattern=""):
        """Write files below a folder; returns their paths."""
        root, written = Path(to).resolve(), []
        for found in self.files(pattern):
            target = root / found.path
            if not target.resolve().is_relative_to(root):
                raise Invalid(f"Not a file name: {found.path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(f".{uuid.uuid4().hex}")
            with found.open() as stream, open(part, "wb") as out:
                shutil.copyfileobj(stream, out, 1 << 20)
            os.replace(part, target)
            written.append(target)
        return written

    def _links(self, reverse):
        return [(link.path, Artifact(self._client, link.artifact)) for link in self._client.backend.links(pb.Links(artifact=self.id, reverse=reverse)).items]

    def uses(self):
        return self._links(False)

    def used_by(self):
        return self._links(True)

    def _wrap(self, value):
        if isinstance(value, dict):
            return typed({k: self._wrap(v) for k, v in value.items()})
        if isinstance(value, list):
            return [self._wrap(v) for v in value]
        if isinstance(value, pb.Blob):
            return Content(self._client, value)
        if isinstance(value, pb.Ref):
            return Link(self._client, value.artifact)
        return value

    # Writing

    def log(self, record=None, /, **fields):
        """Add a record of plain values, files and typed values."""
        if twice := sorted((record or {}).keys() & fields.keys()):
            raise Invalid(f"Given twice: {', '.join(twice)}")
        record = {**(record or {}), **fields}
        if unknown := [k for k in record if isinstance(k, str) and k.startswith("_") and k not in CONVENTIONS]:
            raise Invalid(f"Keys starting with _ belong to Cairn: {', '.join(unknown)}")
        self._log(record)

    def add(self, source, name=None):
        """log({name: source}), with a path as a File or Directory."""
        if isinstance(source, (str, os.PathLike)):
            name = name or Path(source).name
            source = Path(source)
        if not name:
            raise Invalid("Content needs a name")
        self.log({name: source})

    def use(self, ref, role="input", *, incomplete=False):
        """Read a completed artifact and log that this one used it; with `incomplete` also
        one that did not complete, such as a cancelled run whose checkpoint this one continues."""
        found = self._client.get(ref)
        self._settled(found._found, incomplete)
        self._log({"_use": {role: pb.Ref(artifact=found.id)}})
        return found

    def _log(self, record):
        if self._writer is None:
            raise Invalid("This artifact was opened for reading")
        with self._lock:
            if self._closed:
                raise Conflict("The artifact is closed")
            self._send(RECORD, {"_time": time.time(), **record})

    def _send(self, kind, record, completed=False):
        outbox, digests, files = self._client.outbox, [], []
        if outbox and kind == RECORD:
            outbox.room()
        with outbox.staging() if outbox else contextlib.nullcontext() as folder:
            data = values.record(self._prepare(record, digests, files))
            if outbox:
                for digest, size, path, stamp, link in self._stage(files, folder):
                    outbox.content(digest, size, path, stamp=stamp, link=link)
                outbox.add(self._writer, self._sequence + 1, kind, data, digests, completed)
            else:
                self._store(files)
                backend = self._client.backend
                if kind == RECORD:
                    backend.append(pb.Append(artifact=self.id, writer=self._writer, sequence=self._sequence + 1, records=[data]))
                else:
                    self._found = backend.finish(pb.Finish(artifact=self.id, writer=self._writer, sequence=self._sequence + 1, record=data, completed=completed))
        self._sequence += 1

    def _prepare(self, value, digests, files):
        """Plain values, with content as blobs and references as IDs."""
        if hasattr(value, "expand"):
            value = value.expand()
        if isinstance(value, Mapping):
            return {k: self._prepare(v, digests, files) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._prepare(v, digests, files) for v in value]
        if isinstance(value, Content):
            if value._client.repo == self._client.repo:
                digests.append(value._blob.digest)
                return value._blob  # it is there already
            value = File(value.read(), value.media_type)
        if isinstance(value, Link):
            value = Ref(value, incomplete=True)  # an artifact logged it already
        if isinstance(value, Ref):
            return pb.Ref(artifact=self._settled(self._client._one(value.artifact), value.incomplete))
        if isinstance(value, os.PathLike):
            if Path(value).is_dir():
                return self._prepare(Directory(value), digests, files)
            value = File(value)
        if isinstance(value, File):
            blob, entry = self._blob(value)
            digests.append(blob.digest)
            files.append(entry)
            return blob
        return value

    @staticmethod
    def _settled(found, incomplete):
        if not (found.finished if incomplete else found.completed):
            state = "did not complete" if found.finished else "is live"
            raise Invalid(f"{found.namespace}/{found.name} {state}; only completed artifacts can be used")
        return found.id

    def _blob(self, file):
        source = file.source
        if hasattr(source, "read"):
            source = source.read()
        if isinstance(source, (bytes, bytearray, memoryview)):
            data = bytes(source)
            digest = hashlib.sha256(data).digest()
            return pb.Blob(digest=digest, size=len(data), media_type=file.media()), (digest, len(data), data, None, False)
        path = Path(source).absolute()
        outbox, temporary = self._client.outbox, None
        if outbox and not (file.immutable or file.link):
            # The caller may change the file once log returns.
            temporary = outbox.folder / "content" / f"new-{uuid.uuid4().hex}"
            clone(path, temporary)
        read = temporary or path
        before = read.stat()
        digest = digest_of(read)
        after = read.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise Conflict(f"{path} changed while it was read")
        stamp = None if temporary else after.st_mtime_ns
        return pb.Blob(digest=digest, size=after.st_size, media_type=file.media()), (digest, after.st_size, temporary or path, stamp, file.link)

    @staticmethod
    def _stage(files, folder):
        for digest, size, source, stamp, link in files:
            if isinstance(source, bytes) or stamp is None:
                target = folder / digest.hex()
                if isinstance(source, bytes):
                    if not target.exists():
                        part = folder / f"new-{uuid.uuid4().hex}"
                        part.write_bytes(source)
                        os.replace(part, target)
                else:
                    os.replace(source, target)
                source = target
            yield digest, size, source, stamp, link

    def _store(self, files):
        backend = self._client.backend
        missing = set(backend.have(pb.Have(digests=[f[0] for f in files])).digests) if files else ()
        for digest, size, source, _, link in files:
            if digest not in missing:
                continue
            missing.discard(digest)
            if link:
                backend.link_blob(pb.LinkBlob(digest=digest, size=size, path=str(source)))
            else:
                with io.BytesIO(source) if isinstance(source, bytes) else open(source, "rb") as stream:
                    backend.put(digest, stream)

    def _heartbeat(self):
        while not self._beat.wait(10):
            with contextlib.suppress(Exception):
                if self._client.backend.heartbeat(pb.Heartbeat(artifact=self.id)).finished:
                    return

    def finish(self, outcome="completed", *, record=None, timeout=None, gone=False):
        """Seal the artifact and give it its version. Nothing can be added afterwards.

        `gone` says that the processes still writing to it have ended."""
        if outcome not in OUTCOMES:
            raise Invalid(f"The outcome is one of {', '.join(OUTCOMES)}")
        self._stop_console()
        last = {"_time": time.time(), **(record or {}), "_outcome": outcome}
        if self._writer is None or gone:
            self._found = self._client.backend.finish(pb.Finish(artifact=self.id, writer=uuid.uuid4().hex, sequence=1,
                record=values.record(self._prepare(last, [], [])), force=gone, completed=outcome == "completed"))
            return self
        with self._lock:
            if not self._closed:
                self._send(FINISH, last, outcome == "completed")
                self._close()
        if self._client.remote and not self._found.finished:
            self._client.outbox.flush(timeout, self._writer)
            self._found = self._client.outbox.result(self._writer)
        return self

    def leave(self):
        """Stop logging from here without finishing the artifact."""
        if self._writer is None:
            return
        self._stop_console()
        with self._lock:
            if self._closed:
                return
            if self._client.remote:
                self._client.outbox.add(self._writer, self._sequence + 1, LEAVE, b"")
            else:
                self._client.backend.leave(pb.Leave(artifact=self.id, writer=self._writer))
            self._close()

    def _close(self):
        self._closed = True
        self._beat.set()
        closing.forget(self)

    def _stop_console(self):
        if self._console:
            self._console.stop()

    def console(self):
        """What the program printed."""
        return b"".join(record["_console"] for _, record in self.records(having="_console")).decode(errors="replace")

    def flush(self, timeout=None):
        self._client.flush(timeout)

    def __enter__(self):
        self._token = _current.set(self)
        return self

    def __exit__(self, kind, error, _traceback):
        _current.reset(self._token)
        try:
            if self._closed or self._writer is None:
                return
            outcome = closing.outcome(error)
            if outcome == "completed":
                self.finish()
                return
            try:
                self.finish(outcome, record={"_error": {"type": kind.__name__, "message": str(error)}})
            except Exception as failed:  # noqa: BLE001 - the first error is the one to raise
                error.add_note(f"Cairn could not finish the artifact: {failed}")
        finally:
            if self._owned:
                self._client.close()


class Content:
    """Bytes in a record, fetched when read."""

    def __init__(self, client, blob):
        self._client, self._blob = client, blob
        self.digest, self.size, self.media_type = blob.digest.hex(), blob.size, blob.media_type

    def __repr__(self):
        return f"<Content {self.media_type} {self.size} bytes>"

    def open(self):
        return _content(self._client, self._blob)

    def read(self):
        with self.open() as stream:
            return stream.read()


class Link:
    def __init__(self, client, identifier):
        self._client, self.id = client, identifier

    def __repr__(self):
        return f"<Link {self.id[:12]}>"

    def get(self):
        return self._client.get(self.id)


def _content(client, blob):
    if not client.remote:
        return open(client.backend.locate(blob.digest), "rb")
    out, sha = tempfile.SpooledTemporaryFile(max_size=32 << 20), hashlib.sha256()
    for chunk in client.backend.stream(blob.digest):
        sha.update(chunk)
        out.write(chunk)
    if sha.digest() != blob.digest:
        raise Conflict("The received content does not match its digest")
    out.seek(0)
    return out


def artifact(namespace, name, config=None, about=None, *, repo=None, project=None, token=None, **options):
    """An artifact that owns its connection."""
    client = Client(repo, project=project, token=token)
    try:
        made = client.artifact(namespace, name, config, about, **options)
    except BaseException:
        client.close()
        raise
    made._owned = True
    return made


def run(name, config=None, about=None, **options):
    """An artifact in runs."""
    return artifact("runs", name, config, about, **{"console": True, "provenance": True, **options})


def current():
    found = _current.get()
    if found is None:
        raise Invalid("No artifact is open here; use `with cairn.run(...)`")
    return found


def log(record=None, /, **fields):
    current().log(record, **fields)


def add(source, name=None):
    current().add(source, name)


def use(ref, role="input", *, incomplete=False):
    return current().use(ref, role, incomplete=incomplete)


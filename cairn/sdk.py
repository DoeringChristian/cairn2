"""The common artifact lifecycle for local acknowledged and durable remote writes."""

from __future__ import annotations

import contextvars
import copy
import functools
import hashlib
import os
import sys
import threading
import time
import warnings
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from .contract import PUBLICATION_METADATA_LIMIT, PUBLICATION_VALUE_LIMIT
from .delivery import Delivery, _binary_source
from .media import (
    Directory,
    File,
    StoredValue,
    TypedValue,
    cache_root,
    decode,
    encode,
    references,
    save_blob,
)
from .model import (
    CapacityError,
    Conflict,
    NotFound,
    NotPublished,
    Unsupported,
    ValidationError,
    canonical,
    new_id,
    safe_path,
)

if TYPE_CHECKING:
    from .contract import RepositoryProtocol, StartResult

SECTIONS = ("provenance", "console")


class Client:
    def __init__(self, repo=None, *, project=None, token=None, outbox=None):
        from .config import resolve_project, resolve_repo

        self.repo = resolve_repo(repo)
        self.project = resolve_project(project)
        self.backend: RepositoryProtocol
        self.delivery = None
        self._outbox = None
        self._lifecycle_lock = threading.RLock()
        self._closed = False
        self._consoles = []
        if self.repo.startswith(("http://", "https://")):
            from .transport import HTTPRepository

            self.backend = HTTPRepository(self.repo, token=token)
            self.repo = self.backend.url
            self.token = self.backend.token
            if outbox is None:
                base = (
                    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "cairn"
                )
                if os.environ.get("CAIRN_STATE_DIR"):
                    base = Path(os.environ["CAIRN_STATE_DIR"]).expanduser()
                identity = hashlib.sha256(self.repo.encode()).hexdigest()[:24]
                outbox = base / "outbox" / identity
            self._outbox = outbox
        else:
            from .storage import Repository

            self.backend = Repository(self.repo, link_roots=[Path("/")])
            self.token = None

    def _ensure_delivery(self):
        """Activate the remote journal only for writes or explicit recovery."""
        with self._lifecycle_lock:
            if self._closed:
                raise Conflict("Client is closed")
            if self._outbox is not None and self.delivery is None:
                self.delivery = Delivery(self.backend, self._outbox, self.repo)
            return self.delivery

    def start(self, namespace, name):
        self._ensure_delivery()
        args = {"project": self.project, "namespace": namespace, "name": name}
        response = (
            self.delivery.start("start", args) if self.delivery else self.backend.start(**args)
        )
        return Artifact(self, response)

    def run(
        self,
        name=None,
        *,
        config=None,
        metadata=None,
        provenance=True,
        capture_source=True,
        capture_output=True,
    ):
        information = copy.deepcopy({} if metadata is None else metadata)
        if not isinstance(information, dict):
            raise ValidationError("Run metadata must be a dictionary")
        if config is not None:
            if "config" in information and information["config"] != config:
                raise Conflict("config and metadata['config'] disagree")
            information["config"] = copy.deepcopy(config)
        if {type(provenance), type(capture_source), type(capture_output)} != {bool}:
            raise ValidationError("provenance, capture_source and capture_output must be booleans")
        canonical(information)
        captures = {}
        if provenance:
            from .provenance import collect

            recorded, captures = collect(capture_source=capture_source)
            information = {**recorded, **information}
        run = self.start("runs", name or new_id("run"))
        run.update(information)
        for capture_name, content in captures.items():
            run._add(content, capture_name)
        if capture_output:
            from .console import capture

            run._console = capture(run)
            if run._console:
                self._consoles.append(run._console)
        return run

    def attach(self, artifact_id):
        """Register another producer on a live artifact."""
        self._ensure_delivery()
        if self.delivery:
            response = self.delivery.start("register_writer", {"artifact_id": artifact_id})
        else:
            writer = self.backend.register_writer(artifact_id)
            response = {"artifact": self.backend.resolve(artifact_id), "writer": writer}
        return Artifact(self, response)

    def recover(self):
        """Recover remote writers and their exact completed artifact results."""
        self._ensure_delivery()
        if not self.delivery:
            return []
        return [Artifact(self, response) for response in self.delivery.recover()]

    def __repr__(self):
        return f"<Client repo={self.repo!r} project={self.project!r}>"

    def get(self, ref):
        """A reader for one finished artifact; an explicit client never records reads."""
        ref = _identifier(ref)
        descriptor = self.backend.resolve(ref, project=self.project)
        if descriptor["state"] != "finished":
            raise NotPublished("Readers require a finished, immutable artifact")
        return Reader(self, descriptor)

    def find(self, pattern, *, where=None, order=None, limit=None):
        """Readers for the finished artifacts a reference pattern names.

        `*` matches within one level of a name and `**` across levels; without a
        selector each name's latest version matches. where and order run in the
        repository's database.
        """
        return self._find(pattern, where=where, order=order, limit=limit)

    def _find(self, pattern, *, where=None, order=None, limit=None, owner=None):
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValidationError("Artifact limit must be a nonnegative integer")
        readers, cursor = [], None
        while limit is None or len(readers) < limit:
            if self._closed:
                raise Conflict("Client is closed")
            page = self.backend.find(
                pattern,
                project=self.project,
                where=where,
                order=order,
                limit=1000 if limit is None else min(1000, limit - len(readers)),
                cursor=cursor,
            )
            readers += [Reader(self, item) for item in page["items"]]
            cursor = page["next_cursor"]
            if cursor is None:
                break
        if owner is not None:
            for reader in readers:
                owner._depend(reader.id, "input", pattern)
        return readers

    def remove(self, ref):
        ref = _identifier(ref)
        return self.backend.delete(self.backend.resolve(ref, project=self.project)["id"])

    def gc(self, *, apply=False, grace_seconds=3600):
        return self.backend.collect_garbage(grace_seconds=grace_seconds, dry_run=not apply)

    def annotate(self, ref, patch, *, expected_etag=None, target="artifact"):
        """Merge editable annotations; null deletes keys and arrays replace arrays.

        target="name" annotates every version of the name; a version's own keys win.
        """
        if self._closed:
            raise Conflict("Client is closed")
        ref = _identifier(ref)
        artifact = self.backend.resolve(ref, project=self.project)
        return self.backend.annotate(
            artifact["id"],
            patch,
            expected_etag=expected_etag,
            target=target,
            request_id=new_id("request"),
        )

    def comments(self, ref=None, *, scope="name", include_resolved=False):
        """Comments on every version of the name, or with scope="artifact" on this one.

        Without a reference, every comment in the project; a name pattern such as
        reports/** lists the comments on every name it matches.
        """
        ref = _identifier(ref)
        if ref is None or "*" in ref:
            if scope != "name":
                raise ValidationError("A comment listing covers every version of each name")
            comments, cursor = [], None
            while True:
                page = self.backend.list_comments(
                    ref,
                    project=self.project,
                    include_resolved=include_resolved,
                    limit=1000,
                    cursor=cursor,
                )
                comments += page["items"]
                cursor = page["next_cursor"]
                if cursor is None:
                    return comments
        artifact = self.backend.resolve(ref, project=self.project)
        return self.backend.comments(artifact["id"], scope=scope, include_resolved=include_resolved)

    def reply_comment(self, comment_id, body, *, by=None):
        return self.backend.reply_comment(comment_id, body, author=by, request_id=new_id("request"))

    def send_comment(self, comment_id):
        """Send a comment marked for an agent; an edit unsends it until it is sent again."""
        return self.backend.send_comment(comment_id, request_id=new_id("request"))

    def resolve_comment(self, comment_id, *, note=None, by=None):
        return self.backend.resolve_comment(
            comment_id, note=note, by=by, request_id=new_id("request")
        )

    def reopen_comment(self, comment_id):
        return self.backend.resolve_comment(
            comment_id, resolved=False, request_id=new_id("request")
        )

    def flush(self, *, timeout=None):
        return self.delivery.flush(timeout=timeout) if self.delivery else {"pending": 0}

    def close(self):
        with self._lifecycle_lock:
            if self._closed:
                return
            for console in self._consoles:
                console.stop()
            if self.delivery:
                self.delivery.close()
            self.backend.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


class Artifact:
    def __init__(self, client, response: StartResult):
        self.client = client
        self.id = response["artifact"]["id"]
        self.writer_id = response["writer"]["id"]
        self.namespace = response["artifact"]["namespace"]
        self.name = response["artifact"]["name"]
        self.project = response["artifact"]["project"]
        self.result = copy.deepcopy(response.get("result"))
        self._sequence = response["writer"].get("next_sequence", 1)
        self._closed = response["writer"].get("closed", False)
        self._finish_args = (
            canonical(response["finish_args"]) if "finish_args" in response else None
        )
        self._finish_request = new_id("request")
        self._lock = threading.RLock()
        self._owns_client = False
        self._console = None
        self._dependencies = set()

    def __repr__(self):
        ref = f"{self.namespace}/{self.name}"
        if self.version:
            ref += f"@v{self.version}"
        state = "finished" if self.result is not None else "closed" if self._closed else "live"
        return f"<Artifact {ref} id={self.id[:12]} {state} project={self.project!r}>"

    @property
    def version(self):
        return self.result.get("number") if self.result else None

    def _encode(self, value, *, describe=False, link=False):
        if describe:
            return encode(value, _describe_blob)
        put_blob = (
            self.client.delivery.stage_blob
            if self.client.delivery
            else self.client.backend.put_blob
        )
        return encode(value, self._linker(put_blob) if link else put_blob)

    def _linker(self, put_blob):
        def store(source):
            if isinstance(source, (str, os.PathLike)):
                try:
                    return self.client.backend.link_blob(str(Path(source).resolve()))
                except Unsupported as error:
                    warnings.warn(f"{error}; uploading a copy instead", stacklevel=2)
            return put_blob(source)

        return store

    def _append(self, kind, data, *, link=False):
        with self._lock:
            if self._closed:
                raise Conflict("Writer is closed")
            encoded = self._encode(data, link=link)
            if self.client.delivery:
                return self.client.delivery.append(self.writer_id, kind, encoded)
            result = self.client.backend.append_batch(
                self.id,
                self.writer_id,
                [{"sequence": self._sequence, "kind": kind, "data": encoded}],
            )
            self._sequence += 1
            return result

    def log(self, data=None, *, step=None, **fields):
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValidationError("Records must be dictionaries")
        overlap = data.keys() & fields.keys()
        if overlap:
            raise ValidationError(f"Duplicate record fields: {sorted(overlap)}")
        record = {**data, **fields}
        record.setdefault("_wall_time", time.time())
        if step is not None:
            if "step" in record and record["step"] != step:
                raise ValidationError("The record and step argument disagree")
            record["step"] = step
        return self._append("record", record)

    def track(self, value, metadata=None, *, key="value"):
        if not isinstance(key, str) or not key:
            raise ValidationError("track key must be a non-empty string")
        record = dict(metadata or {})
        if key in record:
            raise ValidationError("track metadata already contains the value key")
        record[key] = value
        return self.log(record)

    def update(self, metadata):
        if not isinstance(metadata, dict):
            raise ValidationError("Metadata updates must be dictionaries")
        return self._append("metadata", metadata)

    def add(self, source, *, name=None, link=False):
        """Add a file, folder, bytes or typed value under a name.

        link=True references files the repository can read where they are instead of
        copying them; the repository copies them when it cannot read them.
        """
        return self._add(source, name, reserved=False, link=link)

    def _add(self, source, name, *, reserved=True, link=False):
        if isinstance(source, (str, os.PathLike)):
            path = Path(source)
            value = Directory(path) if path.is_dir() else File(path)
            name = name or path.name
        elif isinstance(source, (bytes, bytearray)):
            name = name or "capture"
            value = File(source, name=name)
        elif isinstance(source, (TypedValue, StoredValue)):
            value = source
            name = name or (source.data.get("name") if isinstance(source.data, dict) else None)
        else:
            raise ValidationError("add() expects a path, bytes or a typed value")
        if not name:
            raise ValidationError("add() requires a name for this typed value")
        if not reserved and name.split("/", 1)[0] in SECTIONS:
            raise ValidationError(f'"{name.split("/", 1)[0]}/" is reserved for Cairn files')
        return self._append("capture", {"name": name, "value": value}, link=link)

    def annotate(self, patch, *, expected_etag=None, target="artifact"):
        return self.client.annotate(self.id, patch, expected_etag=expected_etag, target=target)

    def find(self, pattern, *, where=None, order=None, limit=None):
        """Readers for a pattern, each recorded as this artifact's input."""
        return self.client._find(pattern, where=where, order=order, limit=limit, owner=self)

    def use(self, ref, *, role="input"):
        """A reader recorded as this artifact's input."""
        reader = self.client.get(ref)
        self._depend(reader.id, role)
        return reader

    def _depend(self, artifact_id, role, requested=None):
        with self._lock:
            if (artifact_id, role) in self._dependencies:
                return
            if self.result is not None or self._closed:
                raise Conflict(
                    f"{self.namespace}/{self.name} has finished and can no longer record what it "
                    "reads; read inside a run, or without recording through cairn.Client()"
                )
            self._append("dependency", {"artifact_id": artifact_id, "role": role})
            self._dependencies.add((artifact_id, role))

    def flush(self, *, timeout=None):
        if self.client.delivery:
            result = self.client.delivery.flush(self.writer_id, timeout=timeout)
            self.result = self.client.delivery.completed_result(self.writer_id)
            return result
        return {"pending": 0}

    def finish(
        self,
        *,
        value=None,
        metadata=None,
        outcome="completed",
        timeout=None,
    ):
        if self._console:
            self._console.stop()
        with self._lock:
            if outcome not in {"completed", "failed", "cancelled", "incomplete"}:
                raise ValidationError("Invalid artifact outcome")
            args = {
                "value": self._encode(value, describe=self._finish_args is not None),
                "metadata": self._encode(metadata, describe=self._finish_args is not None),
                "outcome": outcome,
            }
            for label, limit in (
                ("value", PUBLICATION_VALUE_LIMIT),
                ("metadata", PUBLICATION_METADATA_LIMIT),
            ):
                if len(canonical(args[label])) > limit:
                    raise CapacityError(f"Publication {label} exceeds 1 MiB")
            canonical_args = canonical(args)
            if self._finish_args is not None and self._finish_args != canonical_args:
                raise Conflict("A different finalization intent was already requested")
            if self.result is not None:
                if self._owns_client:
                    self.client.close()
                return copy.deepcopy(self.result)
            if self._owns_client and self.client._closed:
                previous = self.client
                self.client = Client(
                    previous.repo,
                    project=previous.project,
                    token=previous.token,
                    outbox=previous._outbox,
                )
                self.client._ensure_delivery()
            try:
                if self.client.delivery:
                    operation = self.client.delivery.operation(self.writer_id, "finish", args)
                    self._finish_args = canonical_args
                    self._closed = True
                    self.result = self.client.delivery.flush(
                        self.writer_id, timeout=timeout, operation_id=operation
                    )
                else:
                    self._finish_args = canonical_args
                    self.close()
                    self.result = self.client.backend.finish(
                        self.id, **args, request_id=self._finish_request
                    )
            except CapacityError:
                # Only a definitive rejection before persisted server intent permits correction.
                rejected = False
                try:
                    if self.client.delivery:
                        rejected = self.client.delivery.discard_rejected_finish(self.writer_id)
                    else:
                        rejected = self.client.backend.resolve(self.id)["intent"] is None
                except Exception:  # noqa: BLE001 - preserve the original capacity rejection
                    rejected = False
                if rejected:
                    self._finish_args = None
                    self._finish_request = new_id("request")
                raise
            if self._owns_client:
                self.client.close()
            return copy.deepcopy(self.result)

    def close(self, *, timeout=None):
        """Close this producer without requesting artifact finalization."""
        if self._console:
            self._console.stop()
        with self._lock:
            if self.client.delivery:
                self.client.delivery.request_close(self.writer_id)
                self._closed = True
                return self.flush(timeout=timeout)
            if not self._closed:
                self.client.backend.close_writer(self.id, self.writer_id, self._sequence - 1)
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if self.result is None and exc_type is None:
                self.finish()
            elif self.result is None:
                self.finish(
                    outcome="failed",
                    metadata={"exception": {"type": exc_type.__name__, "message": str(exc)}},
                )
        except Exception as finish_error:
            if exc is None:
                raise
            # Keep the originating exception; a remote finish intent is already durable.
            exc.add_note(
                f"Cairn finalization also raised {type(finish_error).__name__}: {finish_error}"
            )
        finally:
            try:
                if self._owns_client:
                    self.client.close()
            except Exception as close_error:
                if exc is None:
                    raise
                exc.add_note(
                    f"Cairn cleanup also raised {type(close_error).__name__}: {close_error}"
                )


def _identifier(ref):
    return ref.id if isinstance(ref, (Artifact, Reader)) else ref


def _describe_blob(source):
    """Check a completed finish retry without reopening a repository connection."""
    digest, size = hashlib.sha256(), 0
    with _binary_source(source) as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {"digest": digest.hexdigest(), "size": size}


class Reader:
    """A finished artifact; files and records are fetched when asked for."""

    def __init__(self, client, descriptor):
        self.client = client
        self.id = descriptor["id"]
        self.project = descriptor["project"]
        self.namespace = descriptor["namespace"]
        self.name = descriptor["name"]
        self.version = descriptor["number"]
        self.outcome = descriptor["manifest"]["outcome"]
        self.created_at = datetime.fromisoformat(descriptor["created_at"])
        self.finished_at = datetime.fromisoformat(descriptor["finished_at"])
        self.annotations = copy.deepcopy(descriptor["annotations"])
        self._descriptor = copy.deepcopy(descriptor)

    def __repr__(self):
        return f"<Reader {self} id={self.id[:12]} project={self.project!r}>"

    def __str__(self):
        return f"{self.namespace}/{self.name}@v{self.version}"

    @property
    def _manifest(self):
        return self._descriptor["manifest"]

    @property
    def value(self):
        return decode(copy.deepcopy(self._manifest["value"]), self.client.backend)

    @property
    def metadata(self):
        return copy.deepcopy(self._manifest["metadata"])

    @property
    def config(self):
        return copy.deepcopy(self._manifest["metadata"].get("config"))

    @property
    def dependencies(self):
        """The artifact's own inputs, as readers."""
        return self._readers(item["artifact_id"] for item in self._manifest["dependencies"])

    def inputs(self, *, role):
        """The artifact's inputs recorded with this role, as readers."""
        return self._readers(
            item["artifact_id"] for item in self._manifest["dependencies"] if item["role"] == role
        )

    def _readers(self, ids):
        ids = list(dict.fromkeys(ids))
        if not ids:
            return []
        return [
            Reader(self.client, descriptor)
            for descriptor in self.client.backend.resolve_many(ids, project=self.project)
        ]

    @property
    def files(self):
        captures = self._manifest.get("captures", {})
        return [name for name in captures if name.split("/", 1)[0] not in SECTIONS]

    @property
    def provenance(self):
        return self._section("provenance")

    def annotate(self, patch, *, expected_etag=None, target="artifact"):
        descriptor = self.client.annotate(
            self.id, patch, expected_etag=expected_etag, target=target
        )
        self.annotations = copy.deepcopy(descriptor["annotations"])
        self._descriptor = copy.deepcopy(descriptor)
        return descriptor

    def records(self, *, where=None, fields=None, kind="record", limit=None):
        """Logged records, fetched page by page; each item is what was logged."""
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValidationError("Record limit must be a nonnegative integer")
        cursor, count = None, 0
        while limit is None or count < limit:
            page = self.client.backend.query(
                self.id,
                project=self.project,
                where=where,
                fields=fields,
                kind=kind,
                cursor=cursor,
                limit=1000 if limit is None else min(1000, limit - count),
            )
            for record in page["items"]:
                yield decode(record["data"], self.client.backend)
            count += len(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break

    def console(self):
        """The run's stdout and stderr as one text, in the order Cairn received it."""
        return b"".join(self.file(name).load() for name in sorted(self._section("console"))).decode(
            "utf-8", "replace"
        )

    def _section(self, section):
        captures = self._manifest.get("captures", {})
        return [name for name in captures if name.split("/", 1)[0] == section]

    def file(self, name):
        captures = self._manifest.get("captures", {})
        if name not in captures:
            raise NotFound(f"No added file or folder named {name!r}")
        return decode(captures[name], self.client.backend)

    def download(self, name=None, *, to=None):
        root = Path(to) if to is not None else cache_root() / "artifacts" / self.id
        manifest = self._manifest
        if name is None:
            added, value = self.files, manifest.get("value")
        elif name in manifest.get("captures", {}):
            added, value = [name], None
        elif name in SECTIONS:
            added, value = self._section(name), None
        else:
            added, value = [], manifest.get("value")
        files = {}

        def claim(path, blob):
            if files.setdefault(path, blob)["digest"] != blob["digest"]:
                raise Conflict(f"Download paths collide: {path}")

        for kind, blob in references(value):
            if kind == "blob" and (name is None or blob.get("name") == name):
                claim(safe_path(blob.get("name") or blob["digest"]), blob)
        for capture in added:
            stored = self.file(capture)
            blobs = stored.blobs if isinstance(stored, StoredValue) else []
            if len(blobs) == 1 and stored.format != "directory":
                claim(safe_path(capture), blobs[0])
            else:
                for blob in blobs:
                    claim(
                        f"{safe_path(capture)}/{safe_path(blob.get('name') or blob['digest'])}",
                        blob,
                    )
        if not files:
            raise NotFound("No matching downloadable file or folder")
        parents = {str(parent) for path in files for parent in PurePosixPath(path).parents}
        if parents & files.keys():
            raise Conflict("Download paths collide with folders")
        for path, blob in files.items():
            save_blob(self.client.backend, blob, root, path)
        return root if name is None else root / safe_path(name)

    def query(self, *, where=None, fields=None, limit=100, cursor=None, kind="record"):
        return self.client.backend.query(
            self.id,
            project=self.project,
            where=where,
            fields=fields,
            limit=limit,
            cursor=cursor,
            kind=kind,
        )


_default = contextvars.ContextVar("cairn_default_artifact", default=None)
_report = None


def init(
    name=None,
    *,
    project=None,
    repo=None,
    config=None,
    metadata=None,
    token=None,
    outbox=None,
    provenance=True,
    capture_source=True,
    capture_output=True,
):
    client = Client(repo, project=project, token=token, outbox=outbox)
    try:
        run = client.run(
            name,
            config=config,
            metadata=metadata,
            provenance=provenance,
            capture_source=capture_source,
            capture_output=capture_output,
        )
    except BaseException:
        client.close()
        raise
    run._owns_client = True
    _default.set(run)
    return run


def run(
    name=None,
    *,
    project=None,
    repo=None,
    config=None,
    metadata=None,
    token=None,
    outbox=None,
    provenance=True,
    capture_source=True,
    capture_output=True,
):
    """Decorate a function so that every call runs inside its own cairn.init() run.

    Without config=, a Hydra/OmegaConf DictConfig first argument is resolved into the config.
    """
    if callable(name):
        return run()(name)

    def decorate(function):
        def call(args, kwargs):
            recorded = config
            omegaconf = sys.modules.get("omegaconf")
            if (
                recorded is None
                and args
                and omegaconf
                and isinstance(args[0], omegaconf.DictConfig)
            ):
                recorded = omegaconf.OmegaConf.to_container(
                    args[0], resolve=True, throw_on_missing=True
                )
            with init(
                name,
                project=project,
                repo=repo,
                config=recorded,
                metadata=metadata,
                token=token,
                outbox=outbox,
                provenance=provenance,
                capture_source=capture_source,
                capture_output=capture_output,
            ):
                return function(*args, **kwargs)

        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            return contextvars.copy_context().run(call, args, kwargs)

        return wrapper

    return decorate


def _implicit_client(repo=None, *, project=None, token=None, outbox=None):
    """Open an independently owned client using the current context's defaults."""
    current = _default.get()
    previous = current.client if current is not None else None
    if repo is None and previous is not None:
        repo = previous.repo
        if token is None:
            token = previous.token
        if outbox is None:
            outbox = previous._outbox
    return Client(
        repo,
        project=project if project is not None or previous is None else previous.project,
        token=token,
        outbox=outbox,
    )


class _ContextArtifact(Artifact):
    def __enter__(self):
        if getattr(self, "_context_token", None) is not None:
            raise Conflict("This artifact context is already entered")
        self._context_token = _default.set(self)
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            super().__exit__(exc_type, exc, traceback)
        finally:
            try:
                self.client.close()
            finally:
                _default.reset(self._context_token)
                self._context_token = None


def artifact(namespace, name, *, repo=None, project=None, token=None, outbox=None):
    """Start an artifact; a with block temporarily targets top-level SDK calls at it.

    Inherit the current run's repository/project, or use normal discovery without
    a run. The returned handle owns its client and finishes on context exit.
    """
    client = _implicit_client(repo, project=project, token=token, outbox=outbox)
    try:
        client._ensure_delivery()
        args = {"project": client.project, "namespace": namespace, "name": name}
        response = (
            client.delivery.start("start", args)
            if client.delivery
            else client.backend.start(**args)
        )
        handle = _ContextArtifact(client, response)
        handle._owns_client = True
        return handle
    except BaseException:
        client.close()
        raise


def _current():
    artifact = _default.get()
    if artifact is None:
        raise ValidationError("Call cairn.init() first or use an explicit Client handle")
    return artifact


def log(data=None, *, step=None, **fields):
    return _current().log(data, step=step, **fields)


def track(value, metadata=None, *, key="value"):
    return _current().track(value, metadata, key=key)


def update(metadata):
    return _current().update(metadata)


def add(source, *, name=None):
    return _current().add(source, name=name)


def remove(ref, *, repo=None, project=None):
    with _implicit_client(repo, project=project) as client:
        return client.remove(ref)


def _reading():
    """The run, artifact or report that records what is read."""
    target = _default.get() or _report
    if target is None:
        raise ValidationError(
            "Call cairn.init() first, or read without recording with cairn.Client()"
        )
    return target


def find(pattern, *, where=None, order=None, limit=None):
    """Readers for a reference pattern, each recorded as an input of the current run."""
    return _reading().find(pattern, where=where, order=order, limit=limit)


def use(ref, *, role="input"):
    """A reader recorded as an input of the current run or report."""
    return _reading().use(ref, role=role)


def finish(**kwargs):
    return _current().finish(**kwargs)


def flush(*, timeout=None):
    return _current().flush(timeout=timeout)

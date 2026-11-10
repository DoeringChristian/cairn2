"""Paged datasets built from ordinary retained artifacts and bounded content chunks.

No server schema extension: internal parts use the existing immutable dependency DAG.
The upload checkpoint contains metadata, never another copy of the dataset.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import itertools
import json
import mimetypes
import os
import sqlite3
import tempfile
import threading
import uuid
from collections import OrderedDict
from pathlib import Path

from .model import Conflict, IntegrityError, NotFound, ValidationError, canonical, safe_path
from .objects import ensure_directory, sync_directory

CHUNK_BYTES = 8 * 1024**2
INLINE_BYTES = 32 * 1024**2
PAGE_ITEMS = 128
PART_NAMESPACE = "cairn_parts"
_PAGE_LOCK = threading.RLock()


def walk(root):
    """Component-ordered paths, without following symlinks or listing a whole tree."""
    root = Path(root)
    if root.is_symlink():
        raise ValidationError(f"Dataset sources cannot be symlinks: {root}")
    if root.is_file():
        yield root.name, root
        return
    for path in sorted(root.iterdir(), key=lambda p: p.name):
        if path.is_symlink():
            raise ValidationError(f"Directory captures do not follow symlinks: {path}")
        if path.is_dir():
            for name, child in walk(path):
                yield safe_path(f"{path.name}/{name}"), child
        elif path.is_file():
            yield safe_path(path.name), path
        else:
            raise ValidationError(f"Dataset sources must be regular files: {path}")


def stamp(path):
    s = path.stat()
    return [str(s.st_dev), str(s.st_ino), s.st_size, str(s.st_mtime_ns), str(s.st_ctime_ns)]


def fingerprint(source):
    h, count, size = hashlib.sha256(), 0, 0
    for name, path in walk(source):
        state = stamp(path)
        h.update(canonical([name, state]))
        count += 1
        size += state[2]
    return h.hexdigest(), count, size


def envelope(format, data, *, blobs=(), refs=(), version=1):
    return {
        "$cairn": 1,
        "format": format,
        "version": version,
        "data": data,
        "blobs": list(blobs),
        "refs": list(refs),
    }


def ref(node):
    return {"artifact_id": node, "role": "dataset-part"}


def state_root():
    return Path(
        os.environ.get(
            "CAIRN_STATE_DIR",
            Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "cairn",
        )
    ).expanduser()


class Upload:
    def __init__(self, backend, project, source, identity):
        self.backend, self.project, self.source = backend, project, source
        repo = backend.info()["repository_id"]
        key = hashlib.sha256(
            canonical([repo, project, str(source), identity, CHUNK_BYTES, PAGE_ITEMS])
        ).hexdigest()
        self.path = state_root() / "uploads" / key
        ensure_directory(self.path)
        self.path.chmod(0o700)
        # A separate SQLite lock survives checkpoint commits and is released on crash.
        self.lock = sqlite3.connect(self.path / "lock.sqlite3", timeout=0)
        try:
            self.lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            self.lock.close()
            raise Conflict("This dataset upload is already active on this machine") from exc
        try:
            self.db = sqlite3.connect(self.path / "checkpoint.sqlite3")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS checkpoints (key TEXT PRIMARY KEY, value TEXT)"
            )
            self.db.commit()
            (self.path / "checkpoint.sqlite3").chmod(0o600)
            self.seed = self.get("seed") or self.put("seed", uuid.uuid4().hex)
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self.lock.close()
            raise

    def close(self):
        self.db.close()
        self.lock.close()

    def get(self, key):
        row = self.db.execute("SELECT value FROM checkpoints WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO checkpoints VALUES (?,?)", (key, canonical(value).decode())
            )
        return value

    def request(self, key):
        return hashlib.sha256(f"{self.seed}:{key}".encode()).hexdigest()

    def start(self, key):
        return self.get(key + ":start") or self.put(
            key + ":start",
            self.backend.start(
                self.project,
                PART_NAMESPACE,
                self.request(key),
                request_id=self.request(key + ":start"),
            ),
        )

    def publish(self, key, entries, kind):
        """Retain each uploaded entry before requesting another; retry exact sequences."""
        previous = self.get(key + ":done")
        started = self.start(key)
        artifact, writer = started["artifact"]["id"], started["writer"]["id"]
        values = []
        for seq, item in enumerate(entries, 1):
            data = item.pop("_bytes", None)
            payload = {"sequence": seq, "kind": "record", "data": {"entry": item}}
            checksum = hashlib.sha256(canonical(payload)).hexdigest()
            ack = f"{key}:ack:{seq}"
            old = self.get(ack)
            if old is not None and old != checksum:
                raise Conflict("Dataset source differs from its upload checkpoint")
            if old is None:
                if data is not None:
                    descriptor = item["value"]["blobs"][0]
                    if not self.backend.has_blobs([descriptor["digest"]])[descriptor["digest"]]:
                        self.backend.put_blob(data, **descriptor)
                try:
                    self.backend.append_batch(artifact, writer, [payload])
                except IntegrityError:
                    # GC may reclaim an unreferenced deduplication hit before append.
                    if data is None:
                        raise
                    self.backend.put_blob(data, **descriptor)
                    self.backend.append_batch(artifact, writer, [payload])
                self.put(ack, checksum)
            values.append(item)
        value = envelope(kind, {"entries": values})
        if previous:
            self.retire_children(value)
            return previous
        self.backend.close_writer(artifact, writer, len(values))
        self.backend.finish(artifact, value=value, request_id=self.request(key + ":finish"))
        node = {
            "id": artifact,
            "first": values[0]["first"] if values else "",
            "last": values[-1]["last"] if values else "",
        }
        self.put(key + ":done", node)
        self.retire_children(value)
        return node

    def retire_children(self, value):
        # Finished parents retain their children, so deleting internal names is safe.
        from .media import references

        for dependency in {r["artifact_id"] for k, r in references(value) if k == "ref"}:
            self.backend.delete(dependency)

    def tree(self, entries, key, kind, level=0):
        def pages():
            pending = next(entries, None)
            index = 0
            while pending is not None:

                def take():
                    nonlocal pending
                    size, count = 0, 0
                    while pending is not None and count < PAGE_ITEMS:
                        item = pending
                        length = len(canonical({k: v for k, v in item.items() if k != "_bytes"}))
                        if count and size + length > 256 * 1024:
                            break
                        size += length
                        count += 1
                        yield item
                        pending = next(entries, None)

                yield self.publish(f"{key}:{level}:{index}", take(), kind)
                index += 1

        nodes = iter(pages())
        first, second = next(nodes, None), next(nodes, None)
        if first is None:
            return self.publish(f"{key}:{level}:empty", [], kind)
        if second is None:
            return first

        def branches():
            for node in itertools.chain([first, second], nodes):
                yield {
                    "first": node["first"],
                    "last": node["last"],
                    "child": envelope(
                        "cairn/index-ref", {"id": node["id"]}, refs=[ref(node["id"])]
                    ),
                }

        return self.tree(iter(branches()), key, "cairn/index", level + 1)

    def file(self, name, path):
        key = "file:" + name
        before = stamp(path)
        previous = self.get(key)
        if previous:
            if previous["stamp"] != before:
                raise Conflict(f"Dataset source changed during upload: {path}")
            return previous["value"]
        hasher = hashlib.sha256()
        with path.open("rb") as source:

            def chunks():
                offset = 0
                while data := source.read(CHUNK_BYTES):
                    hasher.update(data)
                    blob = {"digest": hashlib.sha256(data).hexdigest(), "size": len(data)}
                    yield {
                        "first": offset,
                        "last": offset + len(data) - 1,
                        "value": envelope("file", {"name": name}, blobs=[blob]),
                        "_bytes": data,
                    }
                    offset += len(data)

            node = self.tree(iter(chunks()), key, "cairn/chunks")
        if stamp(path) != before:
            raise Conflict(f"Dataset source changed during upload: {path}")
        value = envelope(
            "file",
            {
                "name": name,
                "size": before[2],
                "sha256": hasher.hexdigest(),
                "node": node["id"],
                "media_type": mimetypes.guess_type(name)[0],
            },
            refs=[ref(node["id"])],
            version=2,
        )
        self.put(key, {"stamp": before, "value": value})
        return value


def upload(source, backend, project, *, directory, name):
    if require := getattr(backend, "_require", None):
        require("paged_datasets")
    source = Path(source).absolute()
    identity, count, size = fingerprint(source)
    job = Upload(backend, project, source, [identity, directory, name])
    try:
        completed = job.get("result")
        if completed:
            try:
                backend.resolve(completed["data"]["node"], project=project)
            except NotFound:
                # The old owning artifacts and their parts were explicitly collected.
                with job.db:
                    job.db.execute("DELETE FROM checkpoints")
                job.seed = job.put("seed", uuid.uuid4().hex)
            else:
                return completed
        if directory:

            def files():
                for filename, path in walk(source):
                    before = stamp(path)
                    if before[2] <= CHUNK_BYTES:
                        data = path.read_bytes()
                        if stamp(path) != before:
                            raise Conflict(f"Dataset source changed during upload: {path}")
                        blob = {"digest": hashlib.sha256(data).hexdigest(), "size": len(data)}
                        value = envelope(
                            "file",
                            {
                                "name": filename,
                                "size": len(data),
                                "sha256": blob["digest"],
                                "media_type": mimetypes.guess_type(filename)[0],
                            },
                            blobs=[blob],
                            version=2,
                        )
                        yield {"first": filename, "last": filename, "value": value, "_bytes": data}
                    else:
                        yield {
                            "first": filename,
                            "last": filename,
                            "value": job.file(filename, path),
                        }

            node = job.tree(iter(files()), "directory", "cairn/files")
            result = envelope(
                "directory",
                {"name": name, "node": node["id"], "count": count, "size": size},
                refs=[ref(node["id"])],
                version=2,
            )
        else:
            result = job.file(name, source)
        if fingerprint(source)[0] != identity:
            raise Conflict("Dataset source changed during upload; retry from a stable source")
        return job.put("result", result)
    finally:
        job.close()


def page(backend, node):
    with _PAGE_LOCK:
        return _page(backend, node)


def _page(backend, node):
    cache = getattr(backend, "_dataset_pages", None)
    if cache is None:
        cache = backend._dataset_pages = OrderedDict()
    if node not in cache:
        artifact = backend.resolve(node)
        if artifact["state"] != "finished":
            raise IntegrityError("Dataset index part is not finished")
        value = artifact["manifest"]["value"]
        if value.get("format") not in {"cairn/index", "cairn/files", "cairn/chunks"}:
            raise IntegrityError("Invalid dataset index part")
        cache[node] = value
        if len(cache) > 32:
            cache.popitem(last=False)
    cache.move_to_end(node)
    return cache[node]


def entries(backend, node, key=None, _depth=0):
    if _depth > 32:
        raise IntegrityError("Dataset index exceeds the maximum depth")
    value = page(backend, node)
    compare = lambda v: tuple(v.split("/")) if isinstance(v, str) else v
    for entry in value["data"]["entries"]:
        if key is not None and not compare(entry["first"]) <= compare(key) <= compare(
            entry["last"]
        ):
            continue
        if value["format"] == "cairn/index":
            yield from entries(backend, entry["child"]["data"]["id"], key, _depth + 1)
        else:
            yield entry


def cache_chunk(backend, blob):
    """Verified chunks in a separate, evictable cache; never evict upload journals."""
    from .media import _sha256, cache_root

    root = cache_root() / "dataset-chunks"
    ensure_directory(root)
    path = root / blob["digest"]
    budget = int(os.environ.get("CAIRN_DATASET_CACHE_BYTES", str(256 * 1024**2)))
    if budget < 0:
        raise ValidationError("CAIRN_DATASET_CACHE_BYTES must be nonnegative")
    if not path.is_file() or path.stat().st_size != blob["size"] or _sha256(path) != blob["digest"]:
        temporary = root / (".download-" + uuid.uuid4().hex)
        try:
            hasher, length = hashlib.sha256(), 0
            with backend.open_blob(blob["digest"]) as source, temporary.open("wb") as out:
                while data := source.read(1024**2):
                    hasher.update(data)
                    length += len(data)
                    if length > blob["size"]:
                        raise IntegrityError("Dataset chunk exceeds its declared size")
                    out.write(data)
                out.flush()
                os.fsync(out.fileno())
            if length != blob["size"] or hasher.hexdigest() != blob["digest"]:
                raise IntegrityError("Dataset chunk failed verification")
            temporary.chmod(0o444)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    os.utime(path, None)
    files = []
    for candidate in root.iterdir():
        if not candidate.name.startswith("."):
            try:
                state = candidate.stat()
                files.append((state.st_mtime_ns, state.st_size, candidate))
            except FileNotFoundError:
                pass  # Another reader may evict a cache entry concurrently.
    used = sum(size for _, size, _ in files)
    for _, size, old in sorted(files):
        if used <= max(budget, blob["size"]):
            break
        if old != path:
            used -= size
            old.unlink(missing_ok=True)
    return path


class ChunkStream(io.RawIOBase):
    def __init__(self, value):
        super().__init__()
        self.value, self.position = value, 0
        self._chunk = None
        self._first, self._last = 0, -1

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        position = offset + (
            0 if whence == 0 else self.position if whence == 1 else self.value.data["size"]
        )
        if whence not in (0, 1, 2) or position < 0:
            raise ValueError("Invalid seek")
        self.position = position
        return position

    def readinto(self, buffer):
        if self.closed:
            raise ValueError("Read from closed dataset stream")
        if self.position >= self.value.data["size"]:
            return 0
        if self._chunk is None or not self._first <= self.position <= self._last:
            entry = next(file_chunks(self.value, self.position), None)
            if entry is None:
                raise IntegrityError("Dataset file has a missing chunk")
            if self._chunk is not None:
                self._chunk.close()
            self._chunk = open_chunk(self.value.backend, entry["value"]["blobs"][0])
            self._first, self._last = entry["first"], entry["last"]
        self._chunk.seek(self.position - self._first)
        count = self._chunk.readinto(buffer)
        self.position += count
        return count

    def close(self):
        if self._chunk is not None:
            self._chunk.close()
        super().close()


def file_value(value, name):
    from .media import decode

    name = safe_path(name)
    entry = next(entries(value.backend, value.data["node"], name), None)
    if entry is None or entry["first"] != name:
        raise NotFound(f"No dataset file named {name!r}")
    return decode(entry["value"], value.backend)


def download_file(value, root, name):
    from .media import _sha256

    root = Path(root)
    path = root / safe_path(name)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValidationError("Download path escapes the destination")
    if path.is_file() and _sha256(path) == value.data["sha256"]:
        return path
    ensure_directory(path.parent)
    identity = value.data.get("node", value.data["sha256"])
    partial = path.with_name(f".{path.name}.{identity}.partial")
    if partial.is_symlink():
        raise ValidationError("Dataset partial download cannot be a symlink")
    if partial.exists():
        partial.chmod(0o600)  # Recover an interruption between chmod and final rename.
    hasher = hashlib.sha256()
    with partial.open("r+b" if partial.exists() else "w+b") as out:
        offset = 0
        for entry in file_chunks(value):
            blob = entry["value"]["blobs"][0]
            if entry["first"] != offset:
                raise IntegrityError("Dataset file chunk offsets are not contiguous")
            out.seek(offset)
            data = out.read(blob["size"])
            if len(data) != blob["size"] or hashlib.sha256(data).hexdigest() != blob["digest"]:
                with open_chunk(value.backend, blob) as source:
                    data = source.read()
                out.seek(offset)
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            hasher.update(data)
            offset += len(data)
        if offset != value.data["size"] or hasher.hexdigest() != value.data["sha256"]:
            raise IntegrityError("Dataset file failed verification")
        out.truncate(offset)
        out.flush()
        os.fsync(out.fileno())
    partial.chmod(0o444)
    os.replace(partial, path)
    sync_directory(path.parent)
    return path


def file_chunks(value, key=None):
    if value.blobs:
        blob = value.blobs[0]
        yield {"first": 0, "last": blob["size"] - 1, "value": {"blobs": [blob]}}
    else:
        yield from entries(value.backend, value.data["node"], key)


def roots(value):
    if isinstance(value, dict):
        if value.get("$cairn") == 1 and value.get("format") == "json/literal":
            for item in value["data"].values():
                yield from roots(item)
            return
        if (
            value.get("$cairn") == 1
            and value.get("version") == 2
            and value.get("format") in {"directory", "file"}
        ) and (node := value.get("data", {}).get("node")):
            yield node
        for item in value.values():
            yield from roots(item)
    elif isinstance(value, list):
        for item in value:
            yield from roots(item)


def retire(backend, value):
    """Called only after an owner has durably retained the dataset's root."""
    for node in set(roots(value)):
        if backend.resolve(node)["namespace"] == PART_NAMESPACE:
            backend.delete(node)


def open_chunk(backend, blob):
    # An opened file remains usable if another process evicts its cache pathname.
    while True:
        try:
            return cache_chunk(backend, blob).open("rb")
        except FileNotFoundError:
            continue


def download_reader(reader, name, root):
    """Preflight filenames on disk, then stream outputs without a giant path map."""
    from .media import StoredValue, decode, save_blob

    manifest = reader._manifest
    if not any(roots([manifest.get("value"), manifest.get("captures", {})])):
        return None

    def named(value, target):
        if isinstance(value, StoredValue) and value.version == 2:
            if value.format == "directory":
                for filename in value.iter_files():
                    yield str(Path(target) / filename), value.file(filename)
            else:
                yield target or value.data["name"], value
        elif isinstance(value, StoredValue):
            for blob in value.blobs:
                path = (
                    target
                    if len(value.blobs) == 1 and value.format != "directory"
                    else str(Path(target) / blob.get("name", blob["digest"]))
                )
                yield path or blob.get("name", blob["digest"]), blob

    def final(value):
        if isinstance(value, dict):
            if value.get("$cairn") == 1 and value.get("format") == "json/literal":
                for child in value["data"].values():
                    yield from final(child)
                return
            if value.get("$cairn") == 1 and value.get("version") == 2:
                yield from named(decode(value, reader.client.backend), "")
                return
            if value.get("$cairn") == 1:
                for blob in value.get("blobs", []):
                    yield blob.get("name", blob["digest"]), blob
                yield from final(value.get("data"))
                return
            for child in value.values():
                yield from final(child)
        elif isinstance(value, list):
            for child in value:
                yield from final(child)

    def outputs():
        if name is not None:
            if name in {"console", "provenance"}:
                for capture in reader._section(name):
                    yield from named(reader.file(capture), capture)
            else:
                yield from named(reader.file(name), name)
        else:
            yield from final(manifest.get("value"))
            for capture in reader.files:
                yield from named(reader.file(capture), capture)

    # A disk-backed path set bounds memory even for millions of tiny images.
    with (
        tempfile.TemporaryDirectory(prefix="cairn-download-") as temporary,
        contextlib.closing(sqlite3.connect(Path(temporary) / "paths.sqlite3")) as db,
    ):
        db.execute("CREATE TABLE paths (name TEXT PRIMARY KEY, digest TEXT)")
        for filename, item in outputs():
            filename = safe_path(filename)
            digest = item.data["sha256"] if isinstance(item, StoredValue) else item["digest"]
            previous = db.execute("SELECT digest FROM paths WHERE name=?", (filename,)).fetchone()
            if previous:
                if previous[0] != digest:
                    raise Conflict(f"Download paths collide: {filename}")
                continue
            for parent in Path(filename).parents:
                if db.execute("SELECT 1 FROM paths WHERE name=?", (str(parent),)).fetchone():
                    raise Conflict(f"Download paths collide with folders: {filename}")
            if db.execute(
                "SELECT 1 FROM paths WHERE name>=? AND name<? LIMIT 1",
                (filename + "/", filename + "0"),
            ).fetchone():
                raise Conflict(f"Download paths collide with folders: {filename}")
            db.execute("INSERT INTO paths VALUES (?,?)", (filename, digest))
    for filename, item in outputs():
        if isinstance(item, StoredValue):
            download_file(item, root, filename)
        else:
            save_blob(reader.client.backend, item, root, filename)
    Path(root).mkdir(parents=True, exist_ok=True)
    return root if name is None else root / safe_path(name)

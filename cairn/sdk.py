"""The common artifact lifecycle for local acknowledged and durable remote writes."""

from __future__ import annotations

import contextvars
import copy
import hashlib
import os
import threading
import time
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
    ValidationError,
    canonical,
    new_id,
    safe_path,
)

if TYPE_CHECKING:
    from .contract import RepositoryProtocol, StartResult

SECTIONS = ("provenance",)


class Client:
    def __init__(self, repo=None, *, project="default", token=None, outbox=None):
        from .config import resolve_repo

        self.repo = resolve_repo(repo)
        self.project = project
        self.backend: RepositoryProtocol
        self.delivery = None
        self._outbox = None
        self._lifecycle_lock = threading.RLock()
        self._closed = False
        if self.repo.startswith(("http://", "https://")):
            from .transport import HTTPRepository

            self.backend = HTTPRepository(self.repo, token=token)
            self.repo = self.backend.url
            self.token = self.backend.token
            if outbox is None:
                base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
                identity = hashlib.sha256(self.repo.encode()).hexdigest()[:24]
                outbox = base / "cairn/outbox" / identity
            self._outbox = outbox
        else:
            from .storage import Repository

            self.backend = Repository(self.repo)
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

    def run(self, name=None, *, config=None, metadata=None, provenance=True, capture_source=True):
        information = copy.deepcopy({} if metadata is None else metadata)
        if not isinstance(information, dict):
            raise ValidationError("Run metadata must be a dictionary")
        if config is not None:
            if "config" in information and information["config"] != config:
                raise Conflict("config and metadata['config'] disagree")
            information["config"] = copy.deepcopy(config)
        if type(provenance) is not bool or type(capture_source) is not bool:
            raise ValidationError("provenance and capture_source must be booleans")
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

    def resolve(self, ref):
        if isinstance(ref, (Artifact, Reader)):
            ref = ref.id
        descriptor = self.backend.resolve(ref, project=self.project)
        if descriptor["state"] != "finished":
            raise NotPublished("Readers require a finished, immutable artifact")
        return Reader(self, descriptor)

    def find(self, *, namespace=None, where=None, state=None, limit=None):
        """Iterate descriptors using /metadata and /annotations JSON predicates.

        Discovery includes live and finished artifacts unless state is specified.
        """
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValidationError("Artifact limit must be a nonnegative integer")
        cursor, count = None, 0
        while limit is None or count < limit:
            if self._closed:
                raise Conflict("Client is closed")
            page = self.backend.list_artifacts(
                project=self.project,
                namespace=namespace,
                where=where,
                state=state,
                limit=1000 if limit is None else min(1000, limit - count),
                cursor=cursor,
            )
            yield from page["items"]
            count += len(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break

    def remove(self, ref):
        if isinstance(ref, (Artifact, Reader)):
            ref = ref.id
        return self.backend.delete(self.backend.resolve(ref, project=self.project)["id"])

    def gc(self, *, apply=False, grace_seconds=3600):
        return self.backend.collect_garbage(grace_seconds=grace_seconds, dry_run=not apply)

    def annotate(self, ref, patch, *, expected_etag=None):
        """Merge editable annotations; null deletes keys and arrays replace arrays."""
        if self._closed:
            raise Conflict("Client is closed")
        if isinstance(ref, (Artifact, Reader)):
            ref = ref.id
        artifact = self.backend.resolve(ref, project=self.project)
        return self.backend.annotate(
            artifact["id"], patch, expected_etag=expected_etag, request_id=new_id("request")
        )

    def flush(self, *, timeout=None):
        return self.delivery.flush(timeout=timeout) if self.delivery else {"pending": 0}

    def close(self):
        with self._lifecycle_lock:
            if self._closed:
                return
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

    def __repr__(self):
        ref = f"{self.namespace}/{self.name}"
        if self.version:
            ref += f"@v{self.version}"
        state = "finished" if self.result is not None else "closed" if self._closed else "live"
        return f"<Artifact {ref} id={self.id[:12]} {state} project={self.project!r}>"

    @property
    def version(self):
        return self.result.get("number") if self.result else None

    def _encode(self, value, *, describe=False):
        if describe:
            return encode(value, _describe_blob)
        put_blob = (
            self.client.delivery.stage_blob
            if self.client.delivery
            else self.client.backend.put_blob
        )
        return encode(value, put_blob)

    def _append(self, kind, data):
        with self._lock:
            if self._closed:
                raise Conflict("Writer is closed")
            encoded = self._encode(data)
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

    def add(self, source, *, name=None):
        return self._add(source, name, reserved=False)

    def _add(self, source, name, *, reserved=True):
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
        return self._append("capture", {"name": name, "value": value})

    def annotate(self, patch, *, expected_etag=None):
        return self.client.annotate(self.id, patch, expected_etag=expected_etag)

    def use(self, ref, *, role="input"):
        reader = self.client.resolve(ref)
        self._append("dependency", {"artifact_id": reader.id, "role": role})
        return reader

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
            if exc_type is None:
                self.finish()
            else:
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


def _describe_blob(source):
    """Check a completed finish retry without reopening a repository connection."""
    digest, size = hashlib.sha256(), 0
    with _binary_source(source) as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {"digest": digest.hexdigest(), "size": size}


class Reader:
    def __init__(self, client, descriptor):
        self.client = client
        self._descriptor = copy.deepcopy(descriptor)
        self.id = descriptor["id"]

    def __repr__(self):
        info = self._descriptor
        ref = f"{info['namespace']}/{info['name']}@v{info['number']}"
        return f"<Reader {ref} id={self.id[:12]} project={info['project']!r}>"

    @property
    def version(self):
        return self._descriptor["number"]

    @property
    def descriptor(self):
        return copy.deepcopy(self._descriptor)

    def annotate(self, patch, *, expected_etag=None):
        self._descriptor = self.client.annotate(self.id, patch, expected_etag=expected_etag)
        return self.descriptor

    def read(self):
        return decode(copy.deepcopy(self._descriptor["manifest"]["value"]), self.client.backend)

    def query(self, *, where=None, fields=None, limit=100, cursor=None, kind="record"):
        return self.client.backend.query(
            self.id,
            project=self.client.project,
            where=where,
            fields=fields,
            limit=limit,
            cursor=cursor,
            kind=kind,
        )

    def records(self, *, where=None, fields=None, limit=None, kind="record"):
        if limit is not None and limit < 0:
            raise ValidationError("Record limit must be nonnegative")
        result = []
        cursor = None
        while limit is None or len(result) < limit:
            page = self.query(
                where=where,
                fields=fields,
                kind=kind,
                cursor=cursor,
                limit=1000 if limit is None else min(1000, limit - len(result)),
            )
            for record in page["items"]:
                record = dict(record)
                record["data"] = decode(record["data"], self.client.backend)
                result.append(record)
            cursor = page["next_cursor"]
            if cursor is None:
                break
        return result

    @property
    def files(self):
        captures = self._descriptor["manifest"].get("captures", {})
        return [name for name in captures if name.split("/", 1)[0] not in SECTIONS]

    @property
    def provenance(self):
        return self._section("provenance")

    def _section(self, section):
        captures = self._descriptor["manifest"].get("captures", {})
        return [name for name in captures if name.split("/", 1)[0] == section]

    def file(self, name):
        captures = self._descriptor["manifest"].get("captures", {})
        if name not in captures:
            raise NotFound(f"No added file or folder named {name!r}")
        return decode(captures[name], self.client.backend)

    def download(self, name=None, *, to=None):
        root = Path(to) if to is not None else cache_root() / "artifacts" / self.id
        manifest = self._descriptor["manifest"]
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


_default = contextvars.ContextVar("cairn_default_artifact", default=None)


def init(
    project="default",
    name=None,
    *,
    repo=None,
    config=None,
    metadata=None,
    token=None,
    outbox=None,
    provenance=True,
    capture_source=True,
):
    client = Client(repo, project=project, token=token, outbox=outbox)
    try:
        run = client.run(
            name,
            config=config,
            metadata=metadata,
            provenance=provenance,
            capture_source=capture_source,
        )
    except BaseException:
        client.close()
        raise
    run._owns_client = True
    _default.set(run)
    return run


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
        project=project if project is not None else previous.project if previous else "default",
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
            # Explicit finish(value=...) is allowed inside this convenience scope.
            if self.result is None:
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


def use(ref, *, role="input"):
    return _current().use(ref, role=role)


def finish(**kwargs):
    return _current().finish(**kwargs)


def flush(*, timeout=None):
    return _current().flush(timeout=timeout)

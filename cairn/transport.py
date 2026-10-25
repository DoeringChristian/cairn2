"""The repository contract over named HTTP resources; no domain decisions live here."""

from __future__ import annotations

import contextlib
import contextvars
import io
import os
import tempfile
from urllib.parse import quote, urlsplit

import httpx

from . import model
from .config import resolve_token
from .contract import (
    ArtifactDescriptor,
    ArtifactPage,
    BlobDescriptor,
    CommentPage,
    JSONValue,
    QueryPage,
    Receipt,
    SearchPage,
    StartResult,
    WriterDescriptor,
)

CHUNK_SIZE = 1024 * 1024
ERRORS = {
    cls.code: cls
    for cls in (
        model.ValidationError,
        model.NotFound,
        model.Conflict,
        model.NotPublished,
        model.Pending,
        model.Unauthorized,
        model.IntegrityError,
        model.Unsupported,
        model.CapacityError,
    )
}


class HTTPRepository:
    def __init__(self, url, *, token=None, timeout=30):
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise model.ValidationError(
                "Repository URL must be an HTTP(S) base URL without credentials"
            )
        self.url = url.rstrip("/")
        self.token = resolve_token(token)
        self.timeout = timeout
        self._timeout = contextvars.ContextVar(f"cairn_timeout_{id(self)}", default=None)
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        self._client = httpx.Client(base_url=self.url + "/", headers=headers, timeout=timeout)
        self._features = None

    def close(self) -> None:
        self._client.close()

    @contextlib.contextmanager
    def request_timeout(self, seconds):
        if seconds <= 0:
            raise model.ValidationError("Request timeout must be positive")
        marker = self._timeout.set(seconds)
        try:
            yield self
        finally:
            self._timeout.reset(marker)

    def _check(self, response):
        if response.is_success:
            return
        try:
            error = response.json()["error"]
            cls = ERRORS.get(error["code"])
            message = error["message"]
            details = error.get("details") or {}
        except (ValueError, KeyError, TypeError):
            cls = None
        if cls:
            raise cls(message, details=details)
        response.raise_for_status()

    def _request(self, method, path, *, json=None, params=None, content=None, headers=None):
        if json is not None:
            model.canonical(json)
        response = self._client.request(
            method,
            path,
            json=json,
            params=params,
            content=content,
            headers=headers,
            timeout=self._timeout.get() or self.timeout,
        )
        self._check(response)
        return response.json() if response.content else None

    @staticmethod
    def _id(value):
        if not isinstance(value, str):
            raise model.ValidationError("Resource identifiers must be strings")
        return quote(value, safe="")

    def info(self) -> dict[str, JSONValue]:
        result = self._request("GET", "v1/info")
        protocol = result.get("protocol_version") if isinstance(result, dict) else None
        if type(protocol) is not int or protocol != model.PROTOCOL_VERSION:
            raise model.Unsupported(
                f"Server protocol {protocol!r}; this client requires {model.PROTOCOL_VERSION}"
            )
        return result

    def capabilities(self):
        return self._request("GET", "v1/capabilities")

    def _require(self, feature):
        if self._features is None:
            self._features = self.capabilities().get("features", [])
        if feature not in self._features:
            raise model.Unsupported(f"The server does not support {feature}; upgrade it")

    def start(self, project, namespace, name, *, request_id=None) -> StartResult:
        return self._request(
            "POST",
            "v1/artifacts",
            json={
                "project": project,
                "namespace": namespace,
                "name": name,
                "request_id": request_id,
            },
        )

    def register_writer(self, artifact_id, *, request_id=None) -> WriterDescriptor:
        return self._request(
            "POST", f"v1/artifacts/{self._id(artifact_id)}/writers", json={"request_id": request_id}
        )

    def writer(self, artifact_id, writer_id) -> WriterDescriptor:
        return self._request("GET", self._writer_path(artifact_id, writer_id))

    def _writer_path(self, artifact_id, writer_id):
        return f"v1/artifacts/{self._id(artifact_id)}/writers/{self._id(writer_id)}"

    def append_batch(self, artifact_id, writer_id, entries) -> Receipt:
        return self._request(
            "POST",
            self._writer_path(artifact_id, writer_id) + "/entries",
            json={"entries": entries},
        )

    def close_writer(self, artifact_id, writer_id, final_sequence) -> WriterDescriptor:
        return self._request(
            "POST",
            self._writer_path(artifact_id, writer_id) + "/close",
            json={"final_sequence": final_sequence},
        )

    def finish(
        self,
        artifact_id,
        *,
        value=None,
        metadata=None,
        outcome="completed",
        request_id=None,
    ) -> ArtifactDescriptor:
        return self._request(
            "POST",
            f"v1/artifacts/{self._id(artifact_id)}/finalization",
            json={
                "value": value,
                "metadata": metadata,
                "outcome": outcome,
                "request_id": request_id,
            },
        )

    def resolve(self, ref, *, project="default") -> ArtifactDescriptor:
        return self.resolve_many([ref], project=project)[0]

    def resolve_many(self, refs, *, project="default") -> list[ArtifactDescriptor]:
        return self._request("POST", "v1/resolutions", json={"refs": refs, "project": project})

    def list_artifacts(
        self,
        *,
        project=None,
        namespace=None,
        where=None,
        state=None,
        limit=100,
        cursor=None,
    ) -> ArtifactPage:
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or any(
                value is not None and not isinstance(value, str)
                for value in (project, namespace, state, cursor)
            )
        ):
            raise model.ValidationError("Invalid artifact discovery parameter types")
        if where is not None and not isinstance(where, dict):
            raise model.ValidationError("where must be a JSON predicate")
        params = {
            key: value
            for key, value in {
                "project": project,
                "namespace": namespace,
                "where": model.canonical(where).decode() if where is not None else None,
                "state": state,
                "limit": limit,
                "cursor": cursor,
            }.items()
            if value is not None
        }
        return self._request("GET", "v1/artifacts", params=params)

    def find(
        self, pattern, *, project="default", where=None, order=None, limit=100, cursor=None
    ) -> SearchPage:
        self._require("search")
        return self._request(
            "POST",
            "v1/searches",
            json={
                "pattern": pattern,
                "project": project,
                "where": where,
                "order": order,
                "limit": limit,
                "cursor": cursor,
            },
        )

    def versions(self, ref, *, project="default") -> list[ArtifactDescriptor]:
        return self._request("GET", "v1/history", params={"ref": ref, "project": project})

    def set_alias(self, artifact_id, alias, *, expected_id=None, request_id=None) -> dict[str, str]:
        return self._request(
            "PUT",
            f"v1/artifacts/{self._id(artifact_id)}/aliases",
            json={"alias": alias, "expected_id": expected_id, "request_id": request_id},
        )

    def annotate(
        self, artifact_id, patch, *, expected_etag=None, target="artifact", request_id=None
    ) -> ArtifactDescriptor:
        body = {"patch": patch, "expected_etag": expected_etag, "request_id": request_id}
        if target != "artifact":
            self._require("annotations")
            body["target"] = target
        return self._request(
            "PATCH", f"v1/artifacts/{self._id(artifact_id)}/annotations", json=body
        )

    def annotation_layers(self, artifact_id) -> dict[str, JSONValue]:
        self._require("annotations")
        return self._request("GET", f"v1/artifacts/{self._id(artifact_id)}/annotations")

    def query(
        self, ref, *, project="default", where=None, fields=None, limit=100, cursor=None, kind=None
    ) -> QueryPage:
        return self._request(
            "POST",
            "v1/records/query",
            json={
                "ref": ref,
                "project": project,
                "where": where,
                "fields": fields,
                "limit": limit,
                "cursor": cursor,
                "kind": kind,
            },
        )

    def put_blob(self, source, *, digest=None, size=None) -> BlobDescriptor:
        if (digest is not None and not isinstance(digest, str)) or (
            size is not None and type(size) is not int
        ):
            raise model.ValidationError("Blob digest must be a string and size must be an integer")
        params = {
            key: value
            for key, value in {"digest": digest, "size": size}.items()
            if value is not None
        }
        with contextlib.ExitStack() as stack:
            if isinstance(source, bytes):
                file = stack.enter_context(io.BytesIO(source))
            elif isinstance(source, (str, os.PathLike)):
                file = stack.enter_context(open(source, "rb"))
            elif hasattr(source, "read"):
                file = source
            else:
                raise model.ValidationError("Blob source must be bytes, a path, or a binary file")

            def chunks():
                while chunk := file.read(CHUNK_SIZE):
                    if not isinstance(chunk, bytes):
                        raise model.ValidationError("Blob source must be binary")
                    yield chunk

            return self._request(
                "PUT",
                "v1/blobs",
                params=params,
                content=chunks(),
                headers={"Content-Type": "application/octet-stream"},
            )

    def link_blob(self, path) -> BlobDescriptor:
        self._require("links")
        # The server hashes the file in place, which takes minutes for large files.
        with self.request_timeout(24 * 3600):
            return self._request("POST", "v1/links", json={"path": path})

    def has_blobs(self, digests) -> dict[str, bool]:
        return self._request("POST", "v1/blobs/availability", json={"digests": digests})

    @contextlib.contextmanager
    def open_blob(self, digest):
        with tempfile.TemporaryFile("w+b") as file:
            with self._client.stream(
                "GET", f"v1/blobs/{self._id(digest)}", timeout=self._timeout.get() or self.timeout
            ) as response:
                if not response.is_success:
                    response.read()
                    self._check(response)
                for chunk in response.iter_bytes(CHUNK_SIZE):
                    file.write(chunk)
            file.seek(0)
            yield file

    def delete(self, artifact_id, *, request_id=None) -> dict[str, JSONValue]:
        return self._request(
            "DELETE", f"v1/artifacts/{self._id(artifact_id)}", json={"request_id": request_id}
        )

    def collect_garbage(self, *, grace_seconds=3600, dry_run=True) -> dict[str, JSONValue]:
        return self._request(
            "POST", "v1/maintenance/gc", json={"grace_seconds": grace_seconds, "dry_run": dry_run}
        )

    def add_comment(
        self, artifact_id, anchor, body, *, author=None, for_agent=False, request_id=None
    ) -> dict[str, JSONValue]:
        payload = {"anchor": anchor, "body": body, "request_id": request_id}
        if author is not None:
            self._require("annotations")
            payload["author"] = author
        if for_agent:
            self._require("comment_threads")
            payload["for_agent"] = True
        return self._request("POST", f"v1/artifacts/{self._id(artifact_id)}/comments", json=payload)

    def reply_comment(
        self, comment_id, body, *, author=None, for_agent=False, request_id=None
    ) -> dict[str, JSONValue]:
        self._require("comment_threads")
        return self._request(
            "POST",
            f"v1/comments/{self._id(comment_id)}/replies",
            json={"body": body, "author": author, "for_agent": for_agent, "request_id": request_id},
        )

    def send_comment(self, comment_id, *, request_id=None) -> dict[str, JSONValue]:
        self._require("comment_threads")
        return self._request(
            "POST",
            f"v1/comments/{self._id(comment_id)}/sending",
            json={"request_id": request_id},
        )

    def comments(
        self, artifact_id, *, scope="artifact", include_resolved=True
    ) -> list[dict[str, JSONValue]]:
        params = {}
        if scope != "artifact" or not include_resolved:
            self._require("annotations")
            params = {"scope": scope, "include_resolved": str(include_resolved).lower()}
        return self._request("GET", f"v1/artifacts/{self._id(artifact_id)}/comments", params=params)

    def list_comments(
        self, pattern=None, *, project="default", include_resolved=True, limit=100, cursor=None
    ) -> CommentPage:
        self._require("comment_listing")
        params = {
            key: value
            for key, value in {
                "pattern": pattern,
                "project": project,
                "include_resolved": str(include_resolved).lower(),
                "limit": limit,
                "cursor": cursor,
            }.items()
            if value is not None
        }
        return self._request("GET", "v1/comments", params=params)

    def update_comment(
        self, comment_id, body, *, expected_revision=None, for_agent=None, request_id=None
    ) -> dict[str, JSONValue]:
        self._require("annotations")
        payload = {"body": body, "expected_revision": expected_revision, "request_id": request_id}
        if for_agent is not None:
            self._require("comment_threads")
            payload["for_agent"] = for_agent
        return self._request("PUT", f"v1/comments/{self._id(comment_id)}", json=payload)

    def resolve_comment(
        self, comment_id, *, resolved=True, note=None, by=None, request_id=None
    ) -> dict[str, JSONValue]:
        self._require("annotations")
        return self._request(
            "PUT",
            f"v1/comments/{self._id(comment_id)}/resolution",
            json={"resolved": resolved, "note": note, "by": by, "request_id": request_id},
        )

    def delete_comment(self, comment_id, *, request_id=None) -> dict[str, JSONValue]:
        self._request(
            "DELETE", f"v1/comments/{self._id(comment_id)}", json={"request_id": request_id}
        )
        return {"id": comment_id, "deleted": True}

"""Shared local/HTTP repository contract; payloads remain ordinary JSON objects.

Typed envelopes document lifecycle invariants without a parallel object model.
Transport validation and repository transactions remain the runtime authority.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from os import PathLike
from typing import Any, BinaryIO, Literal, NotRequired, Protocol, TypedDict, runtime_checkable

# Reserve framing space in the 8 MiB manifest after value and metadata.
PUBLICATION_COLLECTION_LIMIT = 6 * 1024 * 1024 - 1024
PUBLICATION_VALUE_LIMIT = 1024 * 1024
PUBLICATION_METADATA_LIMIT = 1024 * 1024

JSONValue = Any  # Arbitrary validated JSON, including typed-media envelopes.


class ArtifactDescriptor(TypedDict):
    id: str
    entity: Literal["artifact"]
    project: str
    namespace: str
    name: str
    state: Literal["live", "closing", "finished"]
    metadata: dict[str, JSONValue]
    annotations: dict[str, JSONValue]
    annotations_etag: str
    cutoff: int
    publication_bytes: int
    intent: dict[str, JSONValue] | None
    number: int | None
    manifest: dict[str, JSONValue] | None
    created_at: str
    finished_at: str | None
    deleted_at: str | None


class WriterDescriptor(TypedDict):
    id: str
    artifact_id: str
    acknowledged: int
    next_sequence: int
    closed: bool
    created_at: str


class StartResult(TypedDict):
    artifact: ArtifactDescriptor
    writer: WriterDescriptor
    result: NotRequired[ArtifactDescriptor]
    finish_args: NotRequired[dict[str, JSONValue]]


class Entry(TypedDict):
    sequence: int
    kind: Literal["record", "metadata", "capture", "dependency"]
    data: dict[str, JSONValue]


class Record(Entry):
    artifact_id: str
    writer_id: str
    ordinal: int
    created_at: str


class Receipt(TypedDict):
    acknowledged: int
    cutoff: int


class QueryPage(TypedDict):
    items: list[Record]
    next_cursor: str | None
    cutoff: int
    artifact_id: str


class ArtifactPage(TypedDict):
    items: list[ArtifactDescriptor]
    next_cursor: str | None


class SearchPage(TypedDict):
    items: list[ArtifactDescriptor]
    next_cursor: str | None


class CommentPage(TypedDict):
    items: list[dict[str, JSONValue]]
    next_cursor: str | None


class BlobDescriptor(TypedDict):
    digest: str
    size: int


@runtime_checkable
class RepositoryProtocol(Protocol):
    """The shared storage/transport surface; excludes backend administration.

    start is idempotent with request_id; append_batch is atomic and replay-safe
    by writer sequence. Query cursors fix a cutoff. Finished artifacts never
    change. finish preserves the artifact ID and assigns a version label.
    """

    def close(self) -> None: ...

    def info(self) -> dict[str, JSONValue]: ...

    def start(
        self, project: str, namespace: str, name: str, *, request_id: str | None = None
    ) -> StartResult: ...

    def register_writer(
        self, artifact_id: str, *, request_id: str | None = None
    ) -> WriterDescriptor: ...

    def writer(self, artifact_id: str, writer_id: str) -> WriterDescriptor: ...

    def append_batch(self, artifact_id: str, writer_id: str, entries: list[Entry]) -> Receipt: ...

    def close_writer(
        self, artifact_id: str, writer_id: str, final_sequence: int
    ) -> WriterDescriptor: ...

    def finish(
        self,
        artifact_id: str,
        *,
        value: JSONValue = None,
        metadata: dict[str, JSONValue] | None = None,
        outcome: str = "completed",
        request_id: str | None = None,
    ) -> ArtifactDescriptor: ...

    def resolve(self, ref: str, *, project: str = "default") -> ArtifactDescriptor: ...

    def resolve_many(
        self, refs: list[str], *, project: str = "default"
    ) -> list[ArtifactDescriptor]: ...

    def list_artifacts(
        self,
        *,
        project: str | None = None,
        namespace: str | None = None,
        where: JSONValue = None,
        state: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> ArtifactPage: ...

    def find(
        self,
        pattern: str,
        *,
        project: str = "default",
        where: JSONValue = None,
        order: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> SearchPage: ...

    def annotate(
        self,
        artifact_id: str,
        patch: dict[str, JSONValue],
        *,
        expected_etag: str | None = None,
        target: Literal["artifact", "name"] = "artifact",
        request_id: str | None = None,
    ) -> ArtifactDescriptor: ...

    def annotation_layers(self, artifact_id: str) -> dict[str, JSONValue]: ...

    def versions(self, ref: str, *, project: str = "default") -> list[ArtifactDescriptor]: ...

    def set_alias(
        self,
        artifact_id: str,
        alias: str,
        *,
        expected_id: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, str]: ...

    def query(
        self,
        ref: str,
        *,
        project: str = "default",
        where: JSONValue = None,
        fields: list[str] | None = None,
        limit: int = 100,
        cursor: str | None = None,
        kind: str | None = None,
    ) -> QueryPage: ...

    def put_blob(
        self,
        source: bytes | str | PathLike | BinaryIO,
        *,
        digest: str | None = None,
        size: int | None = None,
    ) -> BlobDescriptor: ...

    def link_blob(self, path: str) -> BlobDescriptor: ...

    def has_blobs(self, digests: list[str]) -> dict[str, bool]: ...

    def open_blob(self, digest: str) -> AbstractContextManager[BinaryIO]: ...

    def delete(
        self, artifact_id: str, *, request_id: str | None = None
    ) -> dict[str, JSONValue]: ...

    def add_comment(
        self,
        artifact_id: str,
        anchor: JSONValue,
        body: str,
        *,
        author: str | None = None,
        for_agent: bool = False,
        request_id: str | None = None,
    ) -> dict[str, JSONValue]: ...

    def reply_comment(
        self,
        comment_id: str,
        body: str,
        *,
        author: str | None = None,
        for_agent: bool = False,
        request_id: str | None = None,
    ) -> dict[str, JSONValue]: ...

    def send_comment(
        self, comment_id: str, *, request_id: str | None = None
    ) -> dict[str, JSONValue]: ...

    def comments(
        self,
        artifact_id: str,
        *,
        scope: Literal["artifact", "name"] = "artifact",
        include_resolved: bool = True,
    ) -> list[dict[str, JSONValue]]: ...

    def list_comments(
        self,
        pattern: str | None = None,
        *,
        project: str = "default",
        include_resolved: bool = True,
        limit: int = 100,
        cursor: str | None = None,
    ) -> CommentPage: ...

    def update_comment(
        self,
        comment_id: str,
        body: str,
        *,
        expected_revision: int | None = None,
        for_agent: bool | None = None,
        request_id: str | None = None,
    ) -> dict[str, JSONValue]: ...

    def resolve_comment(
        self,
        comment_id: str,
        *,
        resolved: bool = True,
        note: str | None = None,
        by: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, JSONValue]: ...

    def delete_comment(
        self, comment_id: str, *, request_id: str | None = None
    ) -> dict[str, JSONValue]: ...

    def collect_garbage(
        self, *, grace_seconds: int = 3600, dry_run: bool = True
    ) -> dict[str, JSONValue]: ...

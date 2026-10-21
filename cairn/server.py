"""Thin authenticated REST routes around the common repository contract."""

import contextlib
import json
import secrets
import tempfile
from typing import Any, Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from . import model
from .config import _token
from .contract import RepositoryProtocol

CHUNK_SIZE = 1024 * 1024
STATUS = {
    model.ValidationError.code: 400,
    model.NotFound.code: 404,
    model.Conflict.code: 409,
    model.NotPublished.code: 409,
    model.Pending.code: 409,
    model.Unauthorized.code: 401,
    model.IntegrityError.code: 422,
    model.Unsupported.code: 501,
    model.CapacityError.code: 413,
}


def _error(error):
    headers = {"WWW-Authenticate": "Bearer"} if error.code == model.Unauthorized.code else None
    return JSONResponse(
        {
            "error": {
                "code": error.code,
                "message": str(error),
                "details": error.details,
            }
        },
        status_code=STATUS.get(error.code, 400),
        headers=headers,
    )


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def finite_json(self):
        model.canonical(self.model_dump())
        return self


class RequestID(StrictBody):
    request_id: str | None = None


class Start(RequestID):
    project: str
    namespace: str
    name: str


class Entry(StrictBody):
    sequence: int = Field(ge=1)
    kind: Literal["record", "capture", "dependency", "metadata"]
    data: dict[str, Any]


class Batch(StrictBody):
    entries: list[Entry]


class CloseWriter(StrictBody):
    final_sequence: int = Field(ge=0)


class Finish(RequestID):
    value: Any = None
    metadata: dict[str, Any] | None = None

    outcome: Literal["completed", "failed", "cancelled", "incomplete"] = "completed"


class Resolve(StrictBody):
    refs: list[str]
    project: str = "default"


class Search(StrictBody):
    pattern: str
    project: str = "default"
    where: dict[str, Any] | None = None
    order: str | None = None
    limit: int = Field(default=100, ge=1)
    cursor: str | None = None


class Alias(RequestID):
    alias: str
    expected_id: str | None = None


class Annotations(RequestID):
    patch: dict[str, Any]
    expected_etag: str | None = None
    target: Literal["artifact", "name"] = "artifact"


class Records(StrictBody):
    ref: str
    project: str = "default"
    where: dict[str, Any] | None = None
    fields: list[str] | None = None
    limit: int = Field(default=100, ge=1)
    cursor: str | None = None
    kind: Literal["record", "capture", "dependency", "metadata"] | None = None


class Digests(StrictBody):
    digests: list[str]


class Comment(RequestID):
    anchor: dict[str, Any]
    body: str
    author: str | None = None


class CommentEdit(RequestID):
    body: str
    expected_revision: int | None = None


class Resolution(RequestID):
    resolved: bool = True
    note: str | None = None
    by: str | None = None


class GarbageCollection(StrictBody):
    grace_seconds: int = 3600
    dry_run: bool = True


def _route_path(scope):
    path, root = scope["path"], scope.get("root_path", "")
    return path[len(root) :] if root and path.startswith(root) else path


class Boundaries:
    """Authenticate before reading and cap JSON buffering before FastAPI parses it."""

    def __init__(self, app, *, token, max_json_bytes, max_blob_bytes, no_auth=False):
        self.app = app
        self.no_auth = no_auth
        self.token = token.encode("ascii")
        self.max_json_bytes = max_json_bytes
        self.max_blob_bytes = max_blob_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        path = _route_path(scope)
        public = self.no_auth or (path == "/health" and scope["method"] == "GET")
        authorization = headers.get(b"authorization", b"")
        if not public and not secrets.compare_digest(authorization, b"Bearer " + self.token):
            return await _error(model.Unauthorized("A valid bearer token is required"))(
                scope, receive, send
            )
        binary = path == "/v1/blobs" and scope["method"] == "PUT"
        limit = self.max_blob_bytes if binary else self.max_json_bytes
        try:
            length = int(headers.get(b"content-length", b"0"))
            if length < 0:
                raise ValueError
        except ValueError:
            return await _error(model.ValidationError("Invalid Content-Length"))(
                scope, receive, send
            )
        if length > limit:
            return await _error(model.CapacityError("Request body exceeds the configured limit"))(
                scope, receive, send
            )
        if binary:
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                return await _error(
                    model.CapacityError("Request body exceeds the configured limit")
                )(scope, receive, send)
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)


def _queries(request: Request):
    allowed = ()
    path = _route_path(request.scope)
    if not path.startswith("/v1/"):
        return
    if path == "/v1/history" and request.method == "GET":
        allowed = ("ref", "project")
    elif path == "/v1/artifacts" and request.method == "GET":
        allowed = ("project", "namespace", "where", "state", "limit", "cursor")
    elif path == "/v1/blobs" and request.method == "PUT":
        allowed = ("digest", "size")
    elif path.endswith("/comments") and request.method == "GET":
        allowed = ("scope", "include_resolved")
    keys = list(request.query_params.keys())
    repeated = any(len(request.query_params.getlist(key)) > 1 for key in keys)
    if set(keys) - set(allowed) or repeated:
        raise model.ValidationError("Unknown or repeated query parameters")


def create_app(
    repository: RepositoryProtocol,
    *,
    token=None,
    max_json_bytes=8 * 1024 * 1024,
    max_blob_bytes=64 * 1024**3,
    ui=False,
    no_auth=False,
):
    """The caller owns repository lifetime. no_auth is an explicit development opt-out."""
    token = (
        _token(token)
        if token is not None
        else (_token(secrets.token_urlsafe(32)) if no_auth else _token(token))
    )
    if max_json_bytes <= 0 or max_blob_bytes <= 0:
        raise model.ValidationError("Body limits must be positive")
    app = FastAPI(
        title="Cairn",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(_queries)],
    )
    app.add_middleware(
        Boundaries,
        token=token,
        max_json_bytes=max_json_bytes,
        max_blob_bytes=max_blob_bytes,
        no_auth=no_auth,
    )

    @app.exception_handler(model.CairnError)
    async def repository_error(request, error):
        return _error(error)

    @app.exception_handler(RequestValidationError)
    async def request_error(request, error):
        details = {
            "fields": [".".join(str(part) for part in item["loc"]) for item in error.errors()]
        }
        return _error(model.ValidationError("Invalid request fields", details=details))

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        cls = {404: model.NotFound, 405: model.Unsupported}.get(
            error.status_code, model.ValidationError
        )
        response = _error(cls(str(error.detail)))
        response.status_code = error.status_code
        if error.headers:
            response.headers.update(error.headers)
        return response

    @app.get("/health")
    def health(request: Request):
        return {"status": "ok", "protocol_version": model.PROTOCOL_VERSION}

    @app.get("/v1/info")
    def info(request: Request):
        return repository.info()

    @app.get("/v1/capabilities")
    def capabilities(request: Request):
        return {
            "protocol_version": model.PROTOCOL_VERSION,
            "max_json_bytes": max_json_bytes,
            "max_blob_bytes": max_blob_bytes,
            "authentication": "none" if no_auth else "bearer",
            "features": [
                "writers",
                "aliases",
                "comments",
                "annotations",
                "streaming_blobs",
                "search",
            ],
        }

    @app.post("/v1/artifacts")
    def start(body: Start):
        return repository.start(**body.model_dump())

    @app.post("/v1/artifacts/{artifact_id}/writers")
    def register_writer(artifact_id: str, body: RequestID):
        return repository.register_writer(artifact_id, **body.model_dump())

    @app.get("/v1/artifacts/{artifact_id}/writers/{writer_id}")
    def writer(artifact_id: str, writer_id: str, request: Request):
        return repository.writer(artifact_id, writer_id)

    @app.post("/v1/artifacts/{artifact_id}/writers/{writer_id}/entries")
    def append_batch(artifact_id: str, writer_id: str, body: Batch):
        return repository.append_batch(artifact_id, writer_id, body.model_dump()["entries"])

    @app.post("/v1/artifacts/{artifact_id}/writers/{writer_id}/close")
    def close_writer(artifact_id: str, writer_id: str, body: CloseWriter):
        return repository.close_writer(artifact_id, writer_id, body.final_sequence)

    @app.post("/v1/artifacts/{artifact_id}/finalization")
    def finish(artifact_id: str, body: Finish):
        return repository.finish(artifact_id, **body.model_dump())

    @app.post("/v1/resolutions")
    def resolve(body: Resolve):
        return repository.resolve_many(**body.model_dump())

    @app.post("/v1/searches")
    def search(body: Search):
        return repository.find(**body.model_dump())

    @app.get("/v1/artifacts")
    def artifacts(
        request: Request,
        project: str | None = None,
        namespace: str | None = None,
        where: str | None = None,
        state: Literal["live", "closing", "finished"] | None = None,
        limit: int = Query(default=100, ge=1),
        cursor: str | None = None,
    ):
        try:
            predicate = json.loads(where) if where is not None else None
        except (ValueError, RecursionError) as error:
            raise model.ValidationError("where must be a JSON predicate") from error
        return repository.list_artifacts(
            project=project,
            namespace=namespace,
            where=predicate,
            state=state,
            limit=limit,
            cursor=cursor,
        )

    @app.get("/v1/history")
    def versions(ref: str, request: Request, project: str = "default"):
        return repository.versions(ref, project=project)

    @app.put("/v1/artifacts/{artifact_id}/aliases")
    def set_alias(artifact_id: str, body: Alias):
        return repository.set_alias(artifact_id, **body.model_dump())

    @app.patch("/v1/artifacts/{artifact_id}/annotations")
    def annotate(artifact_id: str, body: Annotations):
        return repository.annotate(artifact_id, **body.model_dump())

    @app.get("/v1/artifacts/{artifact_id}/annotations")
    def annotation_layers(artifact_id: str, request: Request):
        return repository.annotation_layers(artifact_id)

    @app.post("/v1/records/query")
    def query(body: Records):
        return repository.query(**body.model_dump())

    @app.put("/v1/blobs")
    async def put_blob(
        request: Request, digest: str | None = None, size: int | None = Query(default=None, ge=0)
    ):
        if size is not None and size > max_blob_bytes:
            raise model.CapacityError("Blob exceeds the configured limit")
        count = 0
        with tempfile.TemporaryFile("w+b") as file:
            async for chunk in request.stream():
                count += len(chunk)
                if count > max_blob_bytes:
                    raise model.CapacityError("Blob exceeds the configured limit")
                await run_in_threadpool(file.write, chunk)
            file.seek(0)
            return await run_in_threadpool(repository.put_blob, file, digest=digest, size=size)

    @app.post("/v1/blobs/availability")
    def has_blobs(body: Digests):
        return repository.has_blobs(body.digests)

    @app.get("/v1/blobs/{digest}")
    def open_blob(digest: str, request: Request):
        stack = contextlib.ExitStack()
        try:
            file = stack.enter_context(repository.open_blob(digest))
        except BaseException:
            stack.close()
            raise

        def chunks():
            try:
                while chunk := file.read(CHUNK_SIZE):
                    yield chunk
            finally:
                stack.close()

        return StreamingResponse(
            chunks(),
            media_type="application/octet-stream",
            headers={"ETag": f'"{digest}"'},
            background=BackgroundTask(stack.close),
        )

    @app.delete("/v1/artifacts/{artifact_id}")
    def delete(artifact_id: str, body: RequestID):
        return repository.delete(artifact_id, **body.model_dump())

    @app.post("/v1/maintenance/gc")
    def collect_garbage(body: GarbageCollection):
        return repository.collect_garbage(**body.model_dump())

    @app.post("/v1/artifacts/{artifact_id}/comments")
    def add_comment(artifact_id: str, body: Comment):
        return repository.add_comment(artifact_id, **body.model_dump())

    @app.get("/v1/artifacts/{artifact_id}/comments")
    def comments(
        artifact_id: str,
        request: Request,
        scope: Literal["artifact", "name"] = "artifact",
        include_resolved: bool = True,
    ):
        return repository.comments(artifact_id, scope=scope, include_resolved=include_resolved)

    @app.put("/v1/comments/{comment_id}")
    def update_comment(comment_id: str, body: CommentEdit):
        return repository.update_comment(comment_id, **body.model_dump())

    @app.put("/v1/comments/{comment_id}/resolution")
    def resolve_comment(comment_id: str, body: Resolution):
        return repository.resolve_comment(comment_id, **body.model_dump())

    @app.delete("/v1/comments/{comment_id}")
    def delete_comment(comment_id: str, request: Request, body: RequestID | None = None):
        repository.delete_comment(comment_id, request_id=body.request_id if body else None)
        return Response(status_code=204)

    if ui:
        from .webui import install

        install(app, repository, token, no_auth=no_auth)
    return app

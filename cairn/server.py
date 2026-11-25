"""HTTP around a repository: one route per operation with protocol messages as bodies,
and plain bytes for content."""

import hmac
import re

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from . import cairn_pb2 as pb
from .errors import CairnError, Invalid, NotFound, Unauthorized
from .operations import REQUESTS as OPERATIONS

STATUS = {"invalid": 400, "unauthorized": 401, "not_found": 404, "conflict": 409, "pending": 425, "corrupt": 500}
MEDIA = "application/x-protobuf"
MAX_BODY = 80 * 1024 * 1024


class _Pull:
    """A request body as a file, read from a worker thread."""

    def __init__(self, request, loop):
        self.chunks, self.loop, self.rest = request.stream().__aiter__(), loop, b""

    def read(self, size):
        import asyncio

        while not self.rest:
            try:
                self.rest = asyncio.run_coroutine_threadsafe(self.chunks.__anext__(), self.loop).result()
            except StopAsyncIteration:
                return b""
        part, self.rest = self.rest[:size], self.rest[size:]
        return part


def create_app(repository, *, token=None):
    def authorize(request):
        given = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token is not None and not hmac.compare_digest(given.encode(), token.encode()):
            raise Unauthorized("A valid token is required")

    def guarded(handler):
        async def route(request):
            try:
                authorize(request)
                return await handler(request)
            except CairnError as error:
                message = pb.Error(code=error.code, message=str(error), details={k: str(v) for k, v in error.details.items()})
                return Response(message.SerializeToString(), STATUS.get(error.code, 500), media_type=MEDIA)

        return route

    async def operate(request):
        name = request.path_params["operation"]
        if name not in OPERATIONS:
            raise NotFound(f"Unknown operation: {name}")
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY:
                raise Invalid("The request is too large")
        try:
            message = OPERATIONS[name].FromString(bytes(body))
        except Exception:
            raise Invalid("The request is not a valid message") from None
        answer = await run_in_threadpool(getattr(repository, name), message)
        return Response(answer.SerializeToString(), media_type=MEDIA)

    def digest(request):
        if not re.fullmatch("[0-9a-f]{64}", text := request.path_params["digest"]):
            raise Invalid("Content is named by its SHA-256")
        return bytes.fromhex(text)

    async def put(request):
        import asyncio

        stream = _Pull(request, asyncio.get_running_loop())
        if "offset" in request.query_params:
            try:
                offset, size = (int(request.query_params[key]) for key in ("offset", "size"))
            except (KeyError, ValueError):
                raise Invalid("A piece needs its offset and the size of the whole") from None
            await run_in_threadpool(repository.put_part, digest(request), stream, offset, size)
        else:
            await run_in_threadpool(repository.put, digest(request), stream)
        return Response(status_code=204)

    async def get(request):
        path = await run_in_threadpool(repository.locate, digest(request))
        # Never run what is stored: nothing here is served as a page of this site.
        return FileResponse(path, media_type="application/octet-stream", headers={
            "ETag": f'"{request.path_params["digest"]}"', "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})

    async def info(_request):
        return JSONResponse({"protocol": 1, "operations": sorted(OPERATIONS), "token": token is not None})

    return Starlette(routes=[
        Route("/v1", info),
        Route("/v1/content/{digest}", guarded(put), methods=["PUT"]),
        Route("/v1/content/{digest}", guarded(get), methods=["GET", "HEAD"]),
        Route("/v1/{operation}", guarded(operate), methods=["POST"]),
    ])

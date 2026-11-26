"""The pages, and what they ask for: Cairn's operations passed on, and workspaces."""

import contextlib
import hashlib
import hmac
import mimetypes
import re
import time
import zlib
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Route, WebSocketRoute

from cairn import CairnError, Invalid, NotFound, Unauthorized, values
from cairn import cairn_pb2 as pb
from cairn.operations import REQUESTS
from cairn.server import MEDIA, STATUS

from .sizes import size_of

STATIC = Path(__file__).with_name("static")
READS = {"resolve", "aliases", "projects", "records", "series", "keys", "last", "find", "files", "links"}
WRITES = {"label", "delete"}
COOKIE = "cairn_ui"
SESSION, RENEW, VIEWER = 7 * 86400, 3600, 12 * 3600
ARTIFACT, PAGE, DONE = range(3)
PAGES = {"default-src": "'self'", "script-src": "'self'", "style-src": "'self' 'unsafe-inline'", "img-src": "'self' data: blob:", "font-src": "'self'",
         "connect-src": "'self'", "frame-src": "'self'", "media-src": "'self' data: blob:", "object-src": "'none'", "base-uri": "'self'", "frame-ancestors": "'none'"}


def _gzip(chunks):
    compressor = zlib.compressobj(1, zlib.DEFLATED, 31)
    for chunk in chunks:
        yield compressor.compress(chunk) + compressor.flush(zlib.Z_SYNC_FLUSH)
    yield compressor.flush()


def create_app(client, workspaces, reports, *, token=None):
    """`client` reaches Cairn; `token` is what a browser logs in with, None for no login."""
    backend = client.backend
    secret = token or hashlib.sha256(client.repo.encode()).hexdigest()
    signer, viewers = (URLSafeTimedSerializer(secret, salt=f"cairn-ui-{use}") for use in ("session", "viewer"))
    # Cookies ignore ports, so other servers on this host have cookies of their own.
    cookie = f"{COOKIE}_{hashlib.sha256(secret.encode()).hexdigest()[:12]}"

    sizes = {}

    def issued(request):
        if token is None:
            return time.time()
        given = request.headers.get("authorization", "")
        if given:
            return time.time() if hmac.compare_digest(given.removeprefix("Bearer ").encode(), token.encode()) else None
        try:
            return signer.loads(request.cookies.get(cookie, ""), max_age=SESSION, return_timestamp=True)[1].timestamp()
        except BadSignature:
            return None

    def grant(response, request):
        response.set_cookie(cookie, signer.dumps("read"), max_age=SESSION, httponly=True, samesite="strict", secure=request.url.scheme == "https")

    def same_origin(request):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            raise Unauthorized("Changes must come from this server's pages")

    def route(handler, *, session=True, change=False):
        async def guarded(request):
            started = issued(request)
            try:
                if session and started is None:
                    raise Unauthorized("Log in with the token")
                if change:
                    same_origin(request)
                response = await handler(request)
            except CairnError as error:
                response = JSONResponse({"error": {"code": error.code, "message": str(error)}}, STATUS.get(error.code, 500))
            except httpx.TransportError as error:
                response = JSONResponse({"error": {"code": "unreachable", "message": f"Cairn does not answer: {error}"}}, 502)
            if session and token is not None and cookie in request.cookies and started is not None and time.time() - started > RENEW and "set-cookie" not in response.headers:
                grant(response, request)
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response

        return guarded

    async def body(request, *keys):
        # JSON alone never comes from a form of another site.
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise Invalid("Changes are JSON")
        try:
            found = await request.json()
            return [found[key] for key in keys]
        except (ValueError, KeyError, TypeError) as error:
            raise Invalid(f"Not the expected JSON: {error}") from None

    async def login(request):
        same_origin(request)
        [given] = await body(request, "token")
        if token is not None and not (isinstance(given, str) and hmac.compare_digest(given.encode(), token.encode())):
            raise Unauthorized("Invalid token")
        response = JSONResponse({})
        grant(response, request)
        return response

    async def logout(_request):
        response = JSONResponse({})
        response.delete_cookie(cookie, httponly=True, samesite="strict")
        return response

    async def session(_request):
        return JSONResponse({"no_auth": token is None, "repository_id": hashlib.sha256(client.repo.encode()).hexdigest()[:24], "now": time.time()})

    def zipped(request, chunks, size=None):
        if "gzip" not in request.headers.get("accept-encoding", "") or (size is not None and size < 4096):
            return chunks, {}
        return _gzip(chunks), {"Content-Encoding": "gzip", "Vary": "Accept-Encoding"}

    async def operate(request):
        name = request.path_params["operation"]
        if name not in READS | WRITES:
            raise NotFound(f"Not an operation of these pages: {name}")
        if name in WRITES:
            same_origin(request)
        if request.headers.get("content-type") != MEDIA:
            raise Invalid("Operations are protocol messages")
        try:
            message = REQUESTS[name].FromString(await request.body())
        except Exception:
            raise Invalid("The request is not a valid message") from None
        answer = (await run_in_threadpool(getattr(backend, name), message)).SerializeToString()
        chunks, headers = zipped(request, [answer], len(answer))
        return Response(b"".join(chunks), media_type=MEDIA, headers=headers)

    async def observations(request):
        """Records of several artifacts after an ordinal each, as one stream of messages."""
        [after] = await body(request, "after")
        if not isinstance(after, dict) or len(after) > 1000 or not all(isinstance(n, int) and n >= 0 for n in after.values()):
            raise Invalid("after gives an ordinal for up to 1000 artifacts")
        found = (await run_in_threadpool(backend.resolve, pb.Resolve(refs=list(after)))).items

        def frame(kind, data=b""):
            return bytes([kind]) + len(data).to_bytes(4, "big") + data

        def stream():
            for item in found:
                yield frame(ARTIFACT, item.SerializeToString())
                reached, more = after[item.id], item.records > after[item.id]
                while more:
                    page = backend.records(pb.Records(artifact=item.id, after=reached, limit=10_000))
                    yield frame(PAGE, page.SerializeToString())
                    reached, more = page.ordinals[-1] if page.ordinals else reached, page.more
            yield frame(DONE)

        chunks, headers = zipped(request, stream())
        return StreamingResponse(chunks, media_type="application/octet-stream", headers={**headers, "X-Accel-Buffering": "no"})

    async def serve(request, digest, headers):
        """Content in the file system, or passed on from the server with the range asked for."""
        if not client.remote:
            path = await run_in_threadpool(backend.locate, digest)
            return FileResponse(path, media_type=headers.pop("Content-Type"), headers=headers)
        asked = {key: request.headers[key] for key in ("range", "if-range") if key in request.headers}

        def ask():
            # A connection waits for the browser as long as it reads.
            return backend._http.send(backend._http.build_request("GET", f"/v1/content/{digest.hex()}", headers=asked, timeout=httpx.Timeout(60, read=600)), stream=True)

        answer = await run_in_threadpool(ask)
        if answer.status_code not in (200, 206, 416):
            content = await run_in_threadpool(answer.read)
            raise backend._error(answer.status_code, content)

        def chunks():
            try:
                yield from answer.iter_raw(1 << 18)
            finally:
                answer.close()

        kept = {key: answer.headers[key] for key in ("content-length", "content-range", "accept-ranges", "etag") if key in answer.headers}
        return StreamingResponse(chunks(), answer.status_code, headers={**kept, **headers})

    async def content(request):
        if not re.fullmatch("[0-9a-f]{64}", text := request.path_params["digest"]):
            raise Invalid("Content is named by its SHA-256")
        media = request.query_params.get("type", "")
        # Only what cannot run; anything else is bytes to the browser.
        media = media if re.fullmatch(r"(image|video|audio)/[\w.+-]+", media) and media != "image/svg+xml" else "application/octet-stream"
        return await serve(request, bytes.fromhex(text), {"Content-Type": media, "Cache-Control": "private, max-age=31536000, immutable",
                                                          "Content-Security-Policy": "sandbox; default-src 'none'"})

    async def file(request, identifier, headers):
        path = request.path_params["path"]
        found = (await run_in_threadpool(backend.files, pb.Files(artifact=identifier, pattern=re.sub(r"[*?]", "?", path), limit=100))).items
        for item in found:
            if item.path == path:
                media = mimetypes.guess_type(path)[0] or item.blob.media_type or "application/octet-stream"
                return await serve(request, item.blob.digest, {"Content-Type": media, "Cache-Control": "private, no-cache", **headers})
        raise NotFound(f"No file {path}")

    async def download(request):
        name = quote(request.path_params["path"].rsplit("/", 1)[-1], safe="")
        return await file(request, request.path_params["id"], {"Content-Security-Policy": "sandbox; default-src 'none'", "Content-Disposition": f"attachment; filename*=UTF-8''{name}"})

    async def viewer_token(request):
        return JSONResponse({"url": f"/ui-api/viewers/{viewers.dumps(request.path_params['id'])}/"})

    async def viewer(request):
        """An artifact's file for a viewer: its scripts run in a sandbox, and may load
        libraries over HTTPS and the artifact's other files by relative path."""
        try:
            identifier = viewers.loads(request.path_params["signed"], max_age=VIEWER)
        except BadSignature:
            raise Unauthorized("The viewer's link expired; reload the page") from None
        origin = f"{request.url.scheme}://{request.url.netloc}"
        policy = (f"sandbox allow-scripts; default-src 'none'; script-src {origin} 'unsafe-inline' 'wasm-unsafe-eval' https: blob:; style-src {origin} 'unsafe-inline' https:; "
                  f"img-src {origin} https: data: blob:; font-src {origin} https: data:; media-src {origin} https: data: blob:; connect-src {origin} https: data: blob:; "
                  f"worker-src {origin} blob:; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
        return await file(request, identifier, {"Content-Security-Policy": policy, "Access-Control-Allow-Origin": "*"})

    def lineage_of(root, depth):
        nodes, waiting, truncated = {}, [(root, 0)], False
        while waiting:
            identifier, distance = waiting.pop(0)
            if identifier in nodes:
                continue
            if len(nodes) >= 150:
                truncated = True
                break
            found, links = None, {False: None, True: None}
            for reverse in links if distance < depth else ():
                links[reverse] = list(dict.fromkeys(link.artifact.id for link in backend.links(pb.Links(artifact=identifier, reverse=reverse)).items))[:150]
            try:
                found = backend.resolve(pb.Resolve(refs=[identifier])).items[0]
            except NotFound:
                pass  # deleted, and known only by those that use it
            outcome = [values.decode(e.value) for e in backend.last(pb.Last(artifact=identifier, keys=["_outcome"])).entries]
            nodes[identifier] = {"name": f"{found.namespace}/{found.name}" if found else identifier[:12], "project": found.project if found else None,
                                 "version": found.version or None if found else None, "short_id": identifier[:12], "deleted": found is None,
                                 "kind": "run" if found and found.namespace == "runs" else "artifact", "status": outcome[0] if outcome else "live",
                                 "inputs": links[False], "consumers": links[True]}
            waiting += [(child, distance + 1) for child in (links[False] or []) + (links[True] or [])]
        return {"nodes": nodes, "truncated": truncated}

    async def lineage(request):
        if not 1 <= (depth := request.path_params["depth"]) <= 3:
            raise Invalid("Lineage is 1 to 3 steps deep")
        return JSONResponse(await run_in_threadpool(lineage_of, request.path_params["id"], depth))

    async def stop(request):
        await body(request)
        await run_in_threadpool(lambda: client.get(request.path_params["id"]).finish("incomplete", gone=True))
        return JSONResponse({})

    async def list_workspaces(request):
        return JSONResponse(await run_in_threadpool(workspaces.list, request.query_params.get("project", "default")))

    async def read_workspace(request):
        return JSONResponse(await run_in_threadpool(workspaces.get, request.path_params["id"]))

    async def delete_workspace(request):
        await run_in_threadpool(workspaces.delete, request.path_params["id"])
        return JSONResponse({})

    async def write_workspace(request):
        project, name, layout, expected = await body(request, "project", "name", "layout", "expected_revision")
        if not isinstance(project, str) or not isinstance(name, str) or not name or not (expected is None or isinstance(expected, int)):
            raise Invalid("A workspace has a project, a name and a revision")
        return JSONResponse(await run_in_threadpool(workspaces.write, project, name, layout, expected))

    async def list_reports(request):
        return JSONResponse(reports.list(request.query_params.get("project", "")))

    async def projects(request):
        """The projects of these pages, which Cairn knows only once something is logged."""
        if request.method == "POST":
            [name] = await body(request, "name")
            if not isinstance(name, str):
                raise Invalid("A project has a name")
            await run_in_threadpool(reports.create_project, name)
            return JSONResponse({"name": name})
        found = await run_in_threadpool(reports.projects)
        for name, updated in (await run_in_threadpool(workspaces.projects)).items():
            known = found.setdefault(name, {"reports": 0, "updated": 0})
            known["updated"] = max(known["updated"], updated)
        return JSONResponse(found)

    async def report(request):
        project, name = (request.query_params.get(key, "") for key in ("project", "name"))
        if request.method in ("GET", "HEAD"):
            return JSONResponse(reports.get(project, name))
        same_origin(request)
        if request.method == "DELETE":
            reports.delete(project, name)
            return JSONResponse({})
        [given] = [await request.json()] if request.headers.get("content-type", "").split(";")[0] == "application/json" else [None]
        if not isinstance(given, dict) or not all(isinstance(value, str) for value in given.values()):
            raise Invalid("Changes are JSON texts")
        if request.method == "POST" and given.keys() == {"old", "new"}:
            return JSONResponse(reports.edit(project, name, given["old"], given["new"]))
        if request.method == "PUT" and given.keys() in ({"text"}, {"text", "base"}):
            return JSONResponse(reports.write(project, name, given["text"], given.get("base")))
        if request.method == "POST" and given.keys() <= {"text"}:
            return JSONResponse(reports.create(project, name, given.get("text", "")))
        raise Invalid("A report takes text, text and base, or old and new")

    def reader(blob):
        """Reads parts of content, wherever it is."""
        if not client.remote:
            path = backend.locate(blob.digest)

            def read(at, count):
                with open(path, "rb") as stream:
                    stream.seek(at)
                    return stream.read(count)

            return read
        return lambda at, count: b"".join(backend.stream(blob.digest, at, min(at + count, blob.size))) if at < blob.size else b""

    def measured(blob):
        if blob.digest not in sizes:
            sizes[blob.digest] = size_of(reader(blob), blob.size, blob.media_type)
        return sizes[blob.digest]

    def files_of(project, text):
        """What a report shows that is a file, with its size, so that space is there before it."""
        found = {}
        for source in list(dict.fromkeys(re.findall(r"!\[(?:\\.|[^\]\\\n])*\]\(\s*cairn:([^)\s]+#[^)\s]+)\s*\)", text)))[:500]:
            ref, _, path = source.partition("#")
            try:
                artifact = backend.resolve(pb.Resolve(project=project, refs=[ref])).items[0].id
                for wanted in (path, f"{path}/data"):
                    item = next((f for f in backend.files(pb.Files(artifact=artifact, pattern=wanted)).items if f.path == wanted), None)
                    if item:
                        side = measured(item.blob)
                        found[source] = {"digest": item.blob.digest.hex(), "size": item.blob.size, "media_type": item.blob.media_type, "width": side and side[0], "height": side and side[1]}
                        break
            except (CairnError, OSError):
                continue  # the page says what is missing
        return found

    async def report_files(request):
        project, name = (request.query_params.get(key, "") for key in ("project", "name"))
        text = reports.get(project, name)["text"]
        return JSONResponse(await run_in_threadpool(files_of, project, text))

    async def live(socket):
        """A report's shared text, for a browser of these pages."""
        if issued(socket) is None or urlsplit(socket.headers.get("origin", "")).netloc != socket.headers.get("host"):
            await socket.close(4401)
            return
        await socket.accept()
        try:
            found = reports.live(*(socket.query_params.get(key, "") for key in ("project", "name")))
        except CairnError:
            await socket.close(4404)
            return
        if socket.query_params.get("epoch") != found.epoch:
            await socket.close(4409)
            return
        await found.join(socket)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        yield
        reports.close()

    async def asset(request):
        target = (STATIC / "assets" / request.path_params["path"]).resolve()
        if not target.is_relative_to((STATIC / "assets").resolve()) or not target.is_file():
            raise NotFound("No such page")
        return FileResponse(target, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    async def favicon(request):
        return RedirectResponse("/favicon.svg") if request.url.path.endswith(".ico") else FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")

    async def shell(request):
        path = request.path_params["path"]
        if path not in ("", "login", "palette") and not path.startswith("p/"):
            raise NotFound("No such page")
        return FileResponse(STATIC / "index.html", headers={"Content-Security-Policy": "; ".join(f"{k} {v}" for k, v in PAGES.items())})

    return Starlette(routes=[
        Route("/ui-api/login", route(login, session=False), methods=["POST"]),
        Route("/ui-api/logout", route(logout, change=True), methods=["POST"]),
        Route("/ui-api/session", route(session)),
        Route("/ui-api/observations", route(observations), methods=["POST"]),
        Route("/ui-api/content/{digest}", route(content)),
        Route("/ui-api/files/{id}/{path:path}", route(download)),
        Route("/ui-api/viewer-tokens/{id}", route(viewer_token)),
        Route("/ui-api/viewers/{signed}/{path:path}", route(viewer, session=False)),
        Route("/ui-api/lineage/{id}/{depth:int}", route(lineage)),
        Route("/ui-api/stop/{id}", route(stop, change=True), methods=["POST"]),
        Route("/ui-api/workspaces", route(list_workspaces)),
        Route("/ui-api/workspaces", route(write_workspace, change=True), methods=["PUT"]),
        Route("/ui-api/workspaces/{id}", route(read_workspace)),
        Route("/ui-api/workspaces/{id}", route(delete_workspace, change=True), methods=["DELETE"]),
        Route("/ui-api/reports", route(list_reports)),
        Route("/ui-api/projects", route(projects)),
        Route("/ui-api/projects", route(projects, change=True), methods=["POST"]),
        Route("/ui-api/report", route(report), methods=["GET", "PUT", "POST", "DELETE"]),
        Route("/ui-api/report/files", route(report_files)),
        WebSocketRoute("/ui-api/live/report", live),
        Route("/ui-api/do/{operation}", route(operate), methods=["POST"]),
        Route("/favicon.svg", route(favicon, session=False)),
        Route("/favicon.ico", route(favicon, session=False)),
        Route("/assets/{path:path}", route(asset, session=False)),
        Route("/{path:path}", route(shell, session=False)),
    ], lifespan=lifespan)

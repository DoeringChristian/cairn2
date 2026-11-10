"""Optional browser explorer: signed browser sessions and packaged React assets.

Browser sessions authorize reads, report text and comments, and tag edits. Other storage
mutations continue to require the server bearer token.
"""

import hashlib
import importlib.util
import mimetypes
import secrets
import time
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

import sqlalchemy as sa
from fastapi import Query, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

from .model import NotFound, Unauthorized, ValidationError, safe_path

STATIC = Path(__file__).with_name("ui_static")
COOKIE = "cairn_browser"
SESSION_SECONDS = 7 * 24 * 60 * 60
RENEW_SECONDS = 60 * 60
VIEWER_SECONDS = 12 * 60 * 60
READ_POSTS = {"/v1/resolutions", "/v1/records/query", "/v1/blobs/availability"}


def available():
    return (
        importlib.util.find_spec("itsdangerous") is not None and (STATIC / "index.html").is_file()
    )


class BrowserSessions:
    def __init__(self, token, *, no_auth=False):
        from itsdangerous import URLSafeTimedSerializer

        self.no_auth = no_auth
        self.signer = URLSafeTimedSerializer(token, salt="cairn-browser-read-v1")
        # Isolate cookies from other Cairn instances on the same hostname.
        self.cookie = COOKIE + "_" + hashlib.sha256(token.encode()).hexdigest()[:12]

    def _issued(self, request):
        from itsdangerous import BadSignature

        try:
            value, issued = self.signer.loads(
                request.cookies.get(self.cookie, ""), max_age=SESSION_SECONDS, return_timestamp=True
            )
        except BadSignature:
            return None
        return issued if value == "read" else None

    def valid(self, request):
        return self.no_auth or self._issued(request) is not None

    def stale(self, request):
        """Whether a valid session should be renewed, so sessions in use do not expire."""
        issued = None if self.no_auth else self._issued(request)
        return issued is not None and time.time() - issued.timestamp() > RENEW_SECONDS

    def grant(self, response, request):
        response.set_cookie(
            self.cookie,
            self.signer.dumps("read"),
            max_age=SESSION_SECONDS,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )

    def require(self, request):
        if not self.valid(request):
            raise Unauthorized("Log in with the server token")


class BrowserAccess:
    """Translate authenticated browser reads to the existing bearer boundary."""

    def __init__(self, app, *, sessions, token):
        self.app, self.sessions, self.token = app, sessions, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            from .server import _route_path

            request = Request(scope)
            path, method = _route_path(scope), scope["method"]
            shell = method in {"GET", "HEAD"} and (
                path in {"/", "/login", "/palette", "/favicon.svg", "/favicon.ico"}
                or path.startswith(("/p/", "/assets/"))
            )
            login = path == "/ui-api/login" and method == "POST"
            # Viewer files authorize themselves with a signed, artifact-scoped token.
            viewer = method in {"GET", "HEAD"} and path.startswith("/ui-api/viewers/")
            read = (method in {"GET", "HEAD"} and path.startswith(("/v1/", "/ui-api/"))) or (
                method == "POST" and path in READ_POSTS | {"/ui-api/logout"}
            )
            parts = path.split("/")
            comment = (
                path.startswith("/ui-api/reports/")
                and len(parts) in {5, 6, 7}
                and parts[4] == "comments"
                and (
                    (method == "POST" and len(parts) == 5)
                    or (method in {"PUT", "DELETE"} and len(parts) == 6)
                    or (method == "PUT" and len(parts) == 7 and parts[6] == "resolution")
                    or (method == "POST" and len(parts) == 7 and parts[6] in {"replies", "sending"})
                )
            )
            report_edit = parts[1:3] == ["ui-api", "reports"] and (
                (method == "POST" and len(parts) == 3)
                or (method == "PUT" and len(parts) == 5 and parts[4] == "body")
                or (method == "DELETE" and len(parts) == 4)
            )
            tag_edit = (
                method == "PATCH"
                and len(parts) == 5
                and parts[1:3] == ["ui-api", "artifacts"]
                and parts[4] == "tags"
            )
            removal = (
                method == "DELETE" and len(parts) == 4 and parts[1:3] == ["ui-api", "artifacts"]
            )
            explicit = b"authorization" in dict(scope["headers"])
            session = (
                read or comment or report_edit or tag_edit or removal
            ) and self.sessions.valid(request)
            renew = (
                session
                and not explicit
                and path != "/ui-api/logout"
                and self.sessions.stale(request)
            )
            # Explicit bearer headers take precedence, including invalid ones.
            if (shell or login or viewer or session) and not explicit:
                scope = {
                    **scope,
                    "headers": [
                        *scope["headers"],
                        (b"authorization", b"Bearer " + self.token.encode()),
                    ],
                }

            renewal = Response()
            if renew:
                self.sessions.grant(renewal, request)

            async def secure_send(message):
                if message["type"] == "http.response.start":
                    message = {
                        **message,
                        "headers": [
                            *message.get("headers", []),
                            (b"x-content-type-options", b"nosniff"),
                            (b"referrer-policy", b"no-referrer"),
                            *(
                                [
                                    (key, value)
                                    for key, value in renewal.raw_headers
                                    if key == b"set-cookie"
                                ]
                                if renew
                                else []
                            ),
                            *(
                                [(b"cache-control", b"no-store")]
                                if not any(
                                    key.lower() == b"cache-control"
                                    for key, _ in message.get("headers", [])
                                )
                                else []
                            ),
                        ],
                    }
                await send(message)

            return await self.app(scope, receive, secure_send)
        return await self.app(scope, receive, send)


def install(app, repository, token, *, no_auth=False):
    """Add browser routes without changing the repository protocol or bearer API."""
    from .storage import artifacts

    if not available():
        raise ValidationError("Install cairn2[ui] and build UI assets with npm run build in ui/")
    sessions = BrowserSessions(token, no_auth=no_auth)
    app.add_middleware(BrowserAccess, sessions=sessions, token=token)

    @app.patch("/ui-api/artifacts/{artifact_id}/tags")
    async def edit_tags(artifact_id: str, request: Request):
        sessions.require(request)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise Unauthorized("Tag edits must come from this server's origin")
        body = await request.json()
        tags = body.get("tags") if isinstance(body, dict) else None
        if not isinstance(tags, list) or any(
            not isinstance(tag, str) or not tag.strip() for tag in tags
        ):
            raise ValidationError("Tags must be an array of nonempty strings")
        if body.get("scope", "artifact") not in ("artifact", "name"):
            raise ValidationError("Tag scope must be artifact or name")
        return repository.annotate(
            artifact_id,
            # An empty list removes the key, so tags set for every version show again.
            patch={"tags": list(dict.fromkeys(tag.strip() for tag in tags)) or None},
            expected_etag=body.get("expected_etag"),
            target=body.get("scope", "artifact"),
        )

    @app.delete("/ui-api/artifacts/{artifact_id}")
    def remove_artifact(artifact_id: str, request: Request):
        sessions.require(request)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise Unauthorized("Removals must come from this server's origin")
        return repository.delete(artifact_id)

    @app.post("/ui-api/login")
    async def login(request: Request):
        # JSON + origin checking prevents form/login CSRF. Vite's proxy rewrites Host.
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise Unauthorized("Login must come from this server's origin")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise ValidationError("Login requires JSON")
        try:
            body = await request.json()
        except ValueError as exc:
            raise ValidationError("Invalid login JSON") from exc
        supplied = body.get("token", body.get("link_token")) if isinstance(body, dict) else None
        authenticated = no_auth or (
            isinstance(supplied, str) and secrets.compare_digest(supplied.encode(), token.encode())
        )
        if not authenticated:
            raise Unauthorized("Invalid token")
        response = JSONResponse({"authenticated": True})
        sessions.grant(response, request)
        return response

    @app.post("/ui-api/logout")
    def logout(request: Request):
        sessions.require(request)
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(sessions.cookie, httponly=True, samesite="strict")
        return response

    @app.get("/ui-api/session")
    def session(request: Request):
        sessions.require(request)
        return {
            "authenticated": True,
            "no_auth": no_auth,
            "repository_id": repository.info()["repository_id"],
        }

    @app.get("/ui-api/projects")
    def projects():
        # Projects are artifact groupings, not a new domain object or storage table.
        from .storage import reports

        counted = (
            sa.select(sa.func.count())
            .where(reports.c.project == artifacts.c.project)
            .correlate(artifacts)
            .scalar_subquery()
        )
        statement = (
            sa.select(
                artifacts.c.project.label("name"),
                sa.func.count().label("artifacts"),
                sa.func.sum(sa.case((artifacts.c.namespace == "runs", 1), else_=0)).label("runs"),
                counted.label("reports"),
                sa.func.max(
                    sa.func.coalesce(artifacts.c.finished_at, artifacts.c.created_at)
                ).label("updated"),
            )
            .where(artifacts.c.deleted_at.is_(None), artifacts.c.namespace != "cairn_parts")
            .group_by(artifacts.c.project)
            .order_by(sa.desc("updated"), artifacts.c.project)
        )
        with repository._transaction() as connection:
            return [dict(row) for row in connection.execute(statement).mappings()]

    @app.get("/ui-api/artifacts")
    def artifact_summaries(
        project: str,
        category: str = "runs",
        search: str = "",
        tag: Annotated[list[str] | None, Query()] = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        from .query import discovery_predicate
        from .storage import annotation_members

        if category not in {"runs", "artifacts", "all"}:
            raise ValidationError("Unknown artifact category")
        conditions = [
            artifacts.c.project == project,
            artifacts.c.deleted_at.is_(None),
            artifacts.c.namespace != "cairn_parts",
        ]
        if category == "runs":
            conditions.append(artifacts.c.namespace == "runs")
        elif category == "artifacts":
            conditions.append(artifacts.c.namespace != "runs")
        if search:
            conditions.append(
                (artifacts.c.namespace + "/" + artifacts.c.name).icontains(search, autoescape=True)
            )
        if tag:
            conditions.append(
                discovery_predicate(
                    {
                        "and": [
                            {"path": "/annotations/tags", "op": "contains", "value": value}
                            for value in tag
                        ]
                    },
                    artifacts.c.metadata,
                    artifacts.c.annotations,
                    artifacts.c.id,
                    repository.engine.dialect.name,
                    annotation_members,
                )
            )
        # Project scalar display fields in SQL: never load full manifests/configs into Python.
        columns = [
            artifacts.c[key]
            for key in (
                "id",
                "project",
                "namespace",
                "name",
                "state",
                "number",
                "created_at",
                "finished_at",
                "annotations_etag",
            )
        ]
        columns += [
            artifacts.c.annotations["tags"].label("tags"),
            artifacts.c.manifest["outcome"].as_string().label("outcome"),
        ]
        for key in ("title", "description"):
            columns.append(
                sa.func.coalesce(
                    artifacts.c.annotations[key].as_string(),
                    artifacts.c.manifest["metadata"][key].as_string(),
                    artifacts.c.metadata[key].as_string(),
                ).label(key)
            )
        with repository._transaction() as connection:
            total = connection.execute(
                sa.select(sa.func.count()).select_from(artifacts).where(*conditions)
            ).scalar_one()
            page_ids = (
                sa.select(artifacts.c.id)
                .where(*conditions)
                .order_by(artifacts.c.created_at.desc(), artifacts.c.id.desc())
                .offset(offset)
                .limit(limit)
            )
            rows = connection.execute(
                sa.select(*columns)
                .where(artifacts.c.id.in_(page_ids))
                .order_by(artifacts.c.created_at.desc(), artifacts.c.id.desc())
            ).mappings()
            items = []
            for row in rows:
                item = dict(row)
                item["annotations"] = {"tags": item.pop("tags") or []}
                items.append(item)
        return {"items": items, "total": total}

    @app.get("/ui-api/artifacts/{artifact_id}/history")
    def artifact_history(artifact_id: str):
        selected = repository.resolve(artifact_id)
        columns = [
            artifacts.c[key]
            for key in (
                "id",
                "project",
                "namespace",
                "name",
                "state",
                "number",
                "cutoff",
                "finished_at",
            )
        ]
        with repository._transaction() as connection:
            rows = connection.execute(
                sa.select(*columns, artifacts.c.manifest["outcome"].as_string().label("outcome"))
                .where(
                    artifacts.c.project == selected["project"],
                    artifacts.c.namespace == selected["namespace"],
                    artifacts.c.name == selected["name"],
                    artifacts.c.state == "finished",
                    artifacts.c.deleted_at.is_(None),
                )
                .order_by(artifacts.c.number)
            ).mappings()
            return [{**row, "manifest": {"outcome": row["outcome"]}} for row in rows]

    @app.get("/ui-api/lineage/{artifact_id}/{depth}")
    def lineage(artifact_id: str, depth: int):
        from .storage import artifact_refs

        if depth < 1 or depth > 3:
            raise ValidationError("Lineage depth must be between 1 and 3")
        nodes, pending, seen, truncated = {}, [(artifact_id, 0)], set(), False
        with repository._transaction() as connection:
            while pending:
                identity, distance = pending.pop(0)
                if identity in seen:
                    continue
                if len(seen) >= 150:
                    truncated = True
                    break
                seen.add(identity)
                artifact = repository._get(connection, artifacts, identity)
                node = {
                    "name": artifact["namespace"] + "/" + artifact["name"],
                    "project": artifact["project"],
                    "version": artifact["number"],
                    "short_id": identity[:12],
                    "deleted": bool(artifact["deleted_at"]),
                    "kind": "run" if artifact["namespace"] == "runs" else "artifact",
                    "status": (artifact["manifest"] or {}).get("outcome", artifact["state"]),
                    "inputs": None,
                    "consumers": None,
                }
                nodes[identity] = node
                if distance >= depth:
                    continue
                inputs = list(
                    connection.execute(
                        sa.select(artifact_refs.c.artifact_id)
                        .where(artifact_refs.c.owner == identity)
                        .distinct()
                        .order_by(artifact_refs.c.artifact_id)
                        .limit(151)
                    ).scalars()
                )
                consumers = list(
                    connection.execute(
                        sa.select(artifact_refs.c.owner)
                        .join(artifacts, artifacts.c.id == artifact_refs.c.owner)
                        .where(artifact_refs.c.artifact_id == identity)
                        .distinct()
                        .order_by(artifact_refs.c.owner)
                        .limit(151)
                    ).scalars()
                )
                truncated = truncated or len(inputs) > 150 or len(consumers) > 150
                node["inputs"], node["consumers"] = inputs[:150], consumers[:150]
                pending.extend((child, distance + 1) for child in [*inputs[:150], *consumers[:150]])
        return {"nodes": nodes, "truncated": truncated}

    from .server import Comment, NewReport, Reply, ReportPart, Resolution

    def check_write(request):
        sessions.require(request)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise Unauthorized("Report edits must come from this server's origin")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise ValidationError("Report edits require JSON")

    @app.post("/ui-api/reports")
    def create_report(body: NewReport, request: Request):
        check_write(request)
        return repository.create_report(**body.model_dump())

    @app.put("/ui-api/reports/{report_id}/body")
    def write_report_body(report_id: str, body: ReportPart, request: Request):
        # Browsers write the author's text; slots belong to agents.
        check_write(request)
        return repository.write_report(report_id, "body", **body.model_dump())

    @app.delete("/ui-api/reports/{report_id}")
    def delete_report(report_id: str, request: Request):
        sessions.require(request)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise Unauthorized("Removals must come from this server's origin")
        return repository.delete_report(report_id)

    @app.get("/ui-api/reports/{report_id}/comments")
    def report_comments(report_id: str):
        return repository.report_comments(report_id)

    @app.post("/ui-api/reports/{report_id}/comments")
    def add_report_comment(report_id: str, body: Comment, request: Request):
        check_write(request)
        return repository.add_report_comment(report_id, **body.model_dump())

    def check_comment_write(request, report_id, comment_id):
        sessions.require(request)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise Unauthorized("Comments must come from this server's origin")
        if all(comment["id"] != comment_id for comment in repository.report_comments(report_id)):
            raise NotFound("Comment not found in this report")

    @app.put("/ui-api/reports/{report_id}/comments/{comment_id}")
    def update_report_comment(report_id: str, comment_id: str, body: Comment, request: Request):
        check_comment_write(request, report_id, comment_id)
        agent = body.for_agent if "for_agent" in body.model_fields_set else None
        return repository.update_comment(comment_id, body.body, for_agent=agent)

    @app.post("/ui-api/reports/{report_id}/comments/{comment_id}/replies")
    def reply_report_comment(report_id: str, comment_id: str, body: Reply, request: Request):
        check_comment_write(request, report_id, comment_id)
        return repository.reply_comment(comment_id, **body.model_dump())

    @app.post("/ui-api/reports/{report_id}/comments/{comment_id}/sending")
    def send_report_comment(report_id: str, comment_id: str, request: Request):
        check_comment_write(request, report_id, comment_id)
        return repository.send_comment(comment_id)

    @app.put("/ui-api/reports/{report_id}/comments/{comment_id}/resolution")
    def resolve_report_comment(report_id: str, comment_id: str, body: Resolution, request: Request):
        check_comment_write(request, report_id, comment_id)
        return repository.resolve_comment(comment_id, **body.model_dump())

    @app.delete("/ui-api/reports/{report_id}/comments/{comment_id}")
    def remove_report_comment(report_id: str, comment_id: str, request: Request):
        check_comment_write(request, report_id, comment_id)
        return repository.delete_comment(comment_id)

    from itsdangerous import BadSignature, URLSafeTimedSerializer

    viewers = URLSafeTimedSerializer(token, salt="cairn-viewer-v1")

    @app.get("/ui-api/viewer-tokens/{artifact_id}")
    def viewer_token(artifact_id: str, request: Request):
        sessions.require(request)
        return {"url": f"/ui-api/viewers/{viewers.dumps(artifact_id)}/"}

    def file_blobs(descriptor, name):
        """A file's content in order, with its digest: one blob, or a large file's chunks."""
        from .datasets import entries

        manifest = descriptor["manifest"]
        value = manifest.get("captures", {}).get(name)
        if isinstance(value, dict) and value.get("format") == "file" and value.get("version") == 2:
            node = value["data"]["node"]
            return [entry["value"]["blobs"][0] for entry in entries(repository, node)], value[
                "data"
            ]["sha256"]
        matches = value.get("blobs", []) if isinstance(value, dict) else []
        if len(matches) != 1:
            matches = [blob for blob in manifest.get("blobs", []) if blob.get("name") == name]
        if len(matches) != 1:
            raise NotFound("File not found")
        return matches, matches[0]["digest"]

    def serve_file(artifact_id, path, request, headers):
        descriptor = repository.resolve(artifact_id)
        if descriptor.get("state") != "finished" or descriptor["id"] != artifact_id:
            raise NotFound("Files require an exact finished artifact ID")
        name = safe_path(path)
        blobs, digest = file_blobs(descriptor, name)
        headers = {
            "Cache-Control": "private, max-age=0, must-revalidate",
            "ETag": f'"{digest}"',
            **headers,
        }
        if request.headers.get("if-none-match") == headers["ETag"]:
            return Response(status_code=304, headers=headers)

        def content():
            for blob in blobs:
                with repository._open_blob(blob["digest"], lease=False) as stream:
                    while chunk := stream.read(1024 * 1024):
                        yield chunk

        return StreamingResponse(
            content(),
            media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
            headers={**headers, "Content-Length": str(sum(blob["size"] for blob in blobs))},
        )

    @app.get("/ui-api/artifacts/{artifact_id}/files/{path:path}")
    def artifact_file(artifact_id: str, path: str, request: Request):
        return serve_file(
            artifact_id,
            path,
            request,
            {
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.split('/')[-1])}",
            },
        )

    @app.get("/ui-api/viewers/{signed}/{path:path}")
    def viewer_file(signed: str, path: str, request: Request):
        """An artifact's file for a viewer: its scripts run in a sandbox, and may load
        libraries over HTTPS and the artifact's other files by relative path."""
        try:
            artifact_id = viewers.loads(signed, max_age=VIEWER_SECONDS)
        except BadSignature:
            raise Unauthorized("Viewer link expired; reload the report") from None
        origin = f"{request.url.scheme}://{request.url.netloc}"
        policy = (
            "sandbox allow-scripts; default-src 'none'; "
            f"script-src {origin} 'unsafe-inline' 'wasm-unsafe-eval' https: blob:; "
            f"style-src {origin} 'unsafe-inline' https:; img-src {origin} https: data: blob:; "
            f"font-src {origin} https: data:; media-src {origin} https: data: blob:; "
            f"connect-src {origin} https: data: blob:; worker-src {origin} blob:; "
            "frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'self'"
        )
        headers = {"Content-Security-Policy": policy, "Access-Control-Allow-Origin": "*"}
        return serve_file(artifact_id, path, request, headers)

    @app.api_route("/favicon.svg", methods=["GET", "HEAD"])
    def favicon():
        return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")

    @app.api_route("/favicon.ico", methods=["GET", "HEAD"])
    def legacy_favicon():
        return RedirectResponse("/favicon.svg")

    @app.get("/assets/{path:path}")
    def asset(path: str):
        target = (STATIC / "assets" / safe_path(path)).resolve()
        if not target.is_relative_to((STATIC / "assets").resolve()) or not target.is_file():
            raise NotFound("UI asset not found")
        return FileResponse(
            target, headers={"Cache-Control": "public, max-age=31536000, immutable"}
        )

    @app.get("/{path:path}")
    def shell(path: str):
        if path not in {"", "login", "palette"} and not path.startswith("p/"):
            raise NotFound("Page not found")
        return FileResponse(
            STATIC / "index.html",
            headers={
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-src 'self'; media-src 'self' data: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
            },
        )

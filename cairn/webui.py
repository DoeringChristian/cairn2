"""Optional browser explorer: signed browser sessions and packaged React assets.

Browser sessions authorize reads, report comments, and tag edits. Other storage mutations
continue to require the server bearer token; report content never executes scripts.
"""

import hashlib
import importlib.util
import json
import mimetypes
import secrets
import time
from pathlib import Path
from typing import Annotated

import sqlalchemy as sa
from fastapi import Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.background import BackgroundTask

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
            # Viewer files authorize themselves with a signed, report-scoped token.
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
            session = (read or comment or tag_edit or removal) and self.sessions.valid(request)
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
        statement = (
            sa.select(
                artifacts.c.project.label("name"),
                sa.func.count().label("artifacts"),
                sa.func.sum(sa.case((artifacts.c.namespace == "runs", 1), else_=0)).label("runs"),
                sa.func.sum(sa.case((artifacts.c.namespace == "reports", 1), else_=0)).label(
                    "reports"
                ),
                sa.func.max(
                    sa.func.coalesce(artifacts.c.finished_at, artifacts.c.created_at)
                ).label("updated"),
            )
            .where(artifacts.c.deleted_at.is_(None))
            .group_by(artifacts.c.project)
            .order_by(sa.desc("updated"), artifacts.c.project)
        )
        with repository._transaction() as connection:
            return [dict(row) for row in connection.execute(statement).mappings()]

    @app.get("/ui-api/artifacts")
    def artifact_summaries(
        project: str,
        category: str = "reports",
        search: str = "",
        tag: Annotated[list[str] | None, Query()] = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        from .query import discovery_predicate
        from .storage import annotation_members, names

        if category not in {"reports", "runs", "artifacts", "all"}:
            raise ValidationError("Unknown artifact category")
        conditions = [artifacts.c.project == project, artifacts.c.deleted_at.is_(None)]
        if category in {"reports", "runs"}:
            conditions.append(artifacts.c.namespace == category)
        elif category == "artifacts":
            conditions.append(artifacts.c.namespace.not_in(["reports", "runs"]))
        if category == "reports":
            # Use the same head as @latest. Before first publication, show one live draft.
            head = (
                sa.select(names.c.head)
                .where(
                    names.c.project == artifacts.c.project,
                    names.c.namespace == artifacts.c.namespace,
                    names.c.name == artifacts.c.name,
                )
                .correlate(artifacts)
                .scalar_subquery()
            )
            sibling = artifacts.alias("sibling")
            draft = (
                sa.select(sibling.c.id)
                .where(
                    sibling.c.project == artifacts.c.project,
                    sibling.c.namespace == artifacts.c.namespace,
                    sibling.c.name == artifacts.c.name,
                    sibling.c.deleted_at.is_(None),
                )
                .order_by(sibling.c.created_at.desc(), sibling.c.id.desc())
                .limit(1)
                .correlate(artifacts)
                .scalar_subquery()
            )
            conditions.append(artifacts.c.id == sa.func.coalesce(head, draft))
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
        sort_time = (
            sa.func.coalesce(artifacts.c.finished_at, artifacts.c.created_at)
            if category == "reports"
            else artifacts.c.created_at
        )
        with repository._transaction() as connection:
            total = connection.execute(
                sa.select(sa.func.count()).select_from(artifacts).where(*conditions)
            ).scalar_one()
            page_ids = (
                sa.select(artifacts.c.id)
                .where(*conditions)
                .order_by(sort_time.desc(), artifacts.c.id.desc())
                .offset(offset)
                .limit(limit)
            )
            rows = connection.execute(
                sa.select(*columns)
                .where(artifacts.c.id.in_(page_ids))
                .order_by(sort_time.desc(), artifacts.c.id.desc())
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

    def report_target(artifact_id):
        target = repository.resolve(artifact_id)
        value = (target.get("manifest") or {}).get("value")
        if (
            target["id"] != artifact_id
            or target["state"] != "finished"
            or not isinstance(value, dict)
            or value.get("format") != "report/bundle"
        ):
            raise ValidationError("Comments require an exact finished report artifact")
        return target

    @app.get("/ui-api/reports/{artifact_id}/comments")
    def report_comments(artifact_id: str):
        # Comments keep their original version and anchor and show across the report's name.
        return repository.comments(report_target(artifact_id)["id"], scope="name")

    from .server import Comment, Reply, Resolution

    @app.post("/ui-api/reports/{artifact_id}/comments")
    def add_report_comment(artifact_id: str, body: Comment, request: Request):
        sessions.require(request)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise Unauthorized("Comments must come from this server's origin")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise ValidationError("Comments require JSON")
        report_target(artifact_id)
        return repository.add_comment(artifact_id, **body.model_dump())

    def check_comment_write(request, artifact_id, comment_id=None):
        sessions.require(request)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise Unauthorized("Comments must come from this server's origin")
        family = repository.comments(report_target(artifact_id)["id"], scope="name")
        if comment_id and all(comment["id"] != comment_id for comment in family):
            raise NotFound("Comment not found in this report")

    @app.put("/ui-api/reports/{artifact_id}/comments/{comment_id}")
    def update_report_comment(artifact_id: str, comment_id: str, body: Comment, request: Request):
        check_comment_write(request, artifact_id, comment_id)
        agent = body.for_agent if "for_agent" in body.model_fields_set else None
        return repository.update_comment(comment_id, body.body, for_agent=agent)

    @app.post("/ui-api/reports/{artifact_id}/comments/{comment_id}/replies")
    def reply_report_comment(artifact_id: str, comment_id: str, body: Reply, request: Request):
        check_comment_write(request, artifact_id, comment_id)
        return repository.reply_comment(comment_id, **body.model_dump())

    @app.post("/ui-api/reports/{artifact_id}/comments/{comment_id}/sending")
    def send_report_comment(artifact_id: str, comment_id: str, request: Request):
        check_comment_write(request, artifact_id, comment_id)
        return repository.send_comment(comment_id)

    @app.put("/ui-api/reports/{artifact_id}/comments/{comment_id}/resolution")
    def resolve_report_comment(
        artifact_id: str, comment_id: str, body: Resolution, request: Request
    ):
        check_comment_write(request, artifact_id, comment_id)
        return repository.resolve_comment(comment_id, **body.model_dump())

    @app.delete("/ui-api/reports/{artifact_id}/comments/{comment_id}")
    def remove_report_comment(artifact_id: str, comment_id: str, request: Request):
        check_comment_write(request, artifact_id, comment_id)
        return repository.delete_comment(comment_id)

    from .ui_preview import ReportPreviews

    previews = ReportPreviews(repository)

    def preview_digest(artifact_id):
        target = report_target(artifact_id)
        value = target["manifest"]["value"]
        document = next(
            (b for b in value.get("blobs", []) if b.get("name") == "document.json"), None
        )
        if not document:
            raise NotFound("Report document not found")
        return document["digest"], value.get("blobs", [])

    def preview_response(payload, mime, etag, request):
        headers = {
            "Cache-Control": "private, max-age=0, must-revalidate",
            "ETag": etag,
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Accept-Ranges": "bytes",
        }
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        span = request.headers.get("range")
        if span and request.headers.get("if-range", etag) == etag:
            import re

            match = re.fullmatch(r"bytes=(\d*)-(\d*)", span)
            if not match or not any(match.groups()):
                return Response(
                    status_code=416, headers={**headers, "Content-Range": f"bytes */{len(payload)}"}
                )
            first, last = match.groups()
            start = int(first) if first else max(0, len(payload) - int(last))
            end = min(int(last), len(payload) - 1) if first and last else len(payload) - 1
            if start > end or start >= len(payload):
                return Response(
                    status_code=416, headers={**headers, "Content-Range": f"bytes */{len(payload)}"}
                )
            headers["Content-Range"] = f"bytes {start}-{end}/{len(payload)}"
            return Response(
                payload[start : end + 1], status_code=206, media_type=mime, headers=headers
            )
        return Response(payload, media_type=mime, headers=headers)

    @app.get("/ui-api/reports/{artifact_id}/preview.json")
    def report_preview(artifact_id: str, request: Request):
        digest, blobs = preview_digest(artifact_id)
        etag = f'"ui-preview-v2-{digest}"'
        # Authenticate and resolve first, but skip document parsing on a cache hit.
        if request.headers.get("if-none-match") == etag:
            return preview_response(b"", "application/json", etag, request)
        document, _ = previews.get(digest, blobs)
        return preview_response(document, "application/json", etag, request)

    @app.get("/ui-api/reports/{artifact_id}/output/{block_index}/{output_index}")
    def report_output(artifact_id: str, block_index: int, output_index: int, request: Request):
        digest, blobs = preview_digest(artifact_id)
        _, outputs = previews.get(digest, blobs)
        key = f"{block_index}/{output_index}"
        if key not in outputs:
            raise NotFound("Report output not found")
        payload, mime = outputs[key]
        return preview_response(
            payload, mime, f'"ui-output-v1-{digest}-{block_index}-{output_index}"', request
        )

    @app.get("/ui-api/reports/{artifact_id}/html/{block_index}/{output_index}")
    def interactive_html(artifact_id: str, block_index: int, output_index: int):
        target = report_target(artifact_id)
        value = target["manifest"]["value"]
        document = next(
            (b for b in value.get("blobs", []) if b.get("name") == "document.json"), None
        )
        if not document or block_index < 0 or output_index < 0:
            raise NotFound("HTML output not found")
        if value.get("data", {}).get("document_version") in {2, 3, 4}:
            with repository._open_blob(document["digest"], lease=False) as stream:
                data = json.load(stream)
        else:
            data = json.loads(previews.get(document["digest"], value.get("blobs", []))[0])
        try:
            content = data["blocks"][block_index]["outputs"][output_index]["data"]["text/html"]
        except (KeyError, IndexError, TypeError):
            raise NotFound("HTML output not found") from None
        if data.get("version") in {2, 3, 4}:
            if (
                not isinstance(content, dict)
                or content.get("mime") != "text/html"
                or content.get("encoding") != "utf-8"
            ):
                raise ValidationError("Invalid HTML asset reference")
            name = safe_path(content.get("asset", ""))
            matches = [blob for blob in value.get("blobs", []) if blob.get("name") == name]
            if len(matches) != 1:
                raise NotFound("HTML asset not found in report")
            with repository._open_blob(matches[0]["digest"], lease=False) as stream:
                content = stream.read().decode("utf-8")
        if isinstance(content, list):
            content = "".join(content)
        if not isinstance(content, str):
            raise ValidationError("Invalid HTML output")
        bootstrap = """<script>
        addEventListener('DOMContentLoaded',()=>{
          const send=()=>parent.postMessage({type:'cairn-html-size',height:Math.ceil(document.body.getBoundingClientRect().height)+24},'*');
          new ResizeObserver(send).observe(document.body);send();
        });</script>"""
        return HTMLResponse(
            '<!doctype html><meta charset="utf-8"><style>html{overflow:hidden}body{margin:12px;font:14px system-ui;color:#202621;background:#fffef9}canvas{max-width:100%}</style>'
            + bootstrap
            + content,
            headers={
                "Content-Security-Policy": "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline' 'wasm-unsafe-eval' https: blob:; style-src 'unsafe-inline' https:; img-src https: data: blob:; font-src https: data:; media-src https: data: blob:; connect-src https: data: blob:; worker-src blob:; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
            },
        )

    from itsdangerous import BadSignature, URLSafeTimedSerializer

    viewers = URLSafeTimedSerializer(token, salt="cairn-viewer-v1")

    @app.get("/ui-api/viewer-tokens/{artifact_id}")
    def viewer_token(artifact_id: str, request: Request):
        sessions.require(request)
        return {"url": f"/ui-api/viewers/{viewers.dumps(artifact_id)}/"}

    @app.get("/ui-api/viewers/{signed}/{path:path}")
    def viewer_file(signed: str, path: str, request: Request):
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
        return serve_report_file(
            artifact_id, path, request, policy=policy, headers={"Access-Control-Allow-Origin": "*"}
        )

    @app.get("/ui-api/reports/{artifact_id}/{path:path}")
    def report_file(artifact_id: str, path: str, request: Request):
        return serve_report_file(artifact_id, path, request)

    def serve_report_file(artifact_id, path, request, *, policy=None, headers=None):
        descriptor = repository.resolve(artifact_id)
        if descriptor.get("state") != "finished" or descriptor["id"] != artifact_id:
            raise NotFound("Report previews require an exact finished artifact ID")
        value = descriptor["manifest"].get("value")
        if (
            not isinstance(value, dict)
            or value.get("format") != "report/bundle"
            or value.get("$cairn") != 1
        ):
            raise NotFound("This artifact does not contain a report")
        name = safe_path(path)
        matches = [blob for blob in value.get("blobs", []) if blob.get("name") == name]
        if len(matches) != 1:
            raise NotFound("Report file not found")
        blob = matches[0]
        cache_headers = {
            "Cache-Control": "private, max-age=0, must-revalidate",
            "ETag": f'"{blob["digest"]}"',
        }
        if request.headers.get("if-none-match") == cache_headers["ETag"]:
            return Response(status_code=304, headers=cache_headers)
        cache_headers["Accept-Ranges"] = "bytes"
        start, end, status = 0, blob["size"] - 1, 200
        span = request.headers.get("range")
        if span and request.headers.get("if-range", cache_headers["ETag"]) == cache_headers["ETag"]:
            import re

            match = re.fullmatch(r"bytes=(\d*)-(\d*)", span)
            if not match or not any(match.groups()):
                return Response(
                    status_code=416,
                    headers={**cache_headers, "Content-Range": f"bytes */{blob['size']}"},
                )
            first, last = match.groups()
            start = int(first) if first else max(0, blob["size"] - int(last))
            end = min(int(last), end) if first and last else end
            if start > end or start >= blob["size"]:
                return Response(
                    status_code=416,
                    headers={**cache_headers, "Content-Range": f"bytes */{blob['size']}"},
                )
            status = 206
            cache_headers["Content-Range"] = f"bytes {start}-{end}/{blob['size']}"
        cache_headers["Content-Length"] = str(max(0, end - start + 1))
        manager = repository._open_blob(blob["digest"], lease=False)
        stream = manager.__enter__()

        def content():
            try:
                if start:
                    if hasattr(stream, "seekable") and stream.seekable():
                        stream.seek(start)
                    else:
                        remaining = start
                        while remaining:
                            chunk = stream.read(min(remaining, 1024 * 1024))
                            if not chunk:
                                return
                            remaining -= len(chunk)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = stream.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
            finally:
                manager.__exit__(None, None, None)

        media = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return StreamingResponse(
            content(),
            status_code=status,
            media_type=media,
            headers={
                **cache_headers,
                "Content-Security-Policy": policy
                or "sandbox allow-same-origin; default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; frame-src 'self'; media-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
                **(headers or {}),
            },
            background=BackgroundTask(manager.__exit__, None, None, None),
        )

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

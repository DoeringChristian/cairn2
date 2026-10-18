"""Optional browser explorer: signed browser sessions and packaged React assets.

Browser sessions authorize reads, report comments, and tag edits. Other storage mutations
continue to require the server bearer token; report content never executes scripts.
"""

import hashlib
import importlib.util
import json
import mimetypes
import secrets
from pathlib import Path
from typing import Annotated

import sqlalchemy as sa
from fastapi import Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from starlette.background import BackgroundTask

from .model import NotFound, Unauthorized, ValidationError, safe_path

STATIC = Path(__file__).with_name("ui_static")
COOKIE = "cairn_browser"
SESSION_SECONDS = 12 * 60 * 60
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

    def valid(self, request):
        from itsdangerous import BadSignature

        if self.no_auth:
            return True
        try:
            return (
                self.signer.loads(request.cookies.get(self.cookie, ""), max_age=SESSION_SECONDS)
                == "read"
            )
        except BadSignature:
            return False

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
            read = (method in {"GET", "HEAD"} and path.startswith(("/v1/", "/ui-api/"))) or (
                method == "POST" and path in READ_POSTS | {"/ui-api/logout"}
            )
            parts = path.split("/")
            comment = (
                path.startswith("/ui-api/reports/")
                and len(parts) in {5, 6}
                and parts[4] == "comments"
                and (
                    (method == "POST" and len(parts) == 5)
                    or (method in {"PUT", "DELETE"} and len(parts) == 6)
                )
            )
            tag_edit = (
                method == "PATCH"
                and len(parts) == 5
                and parts[1:3] == ["ui-api", "artifacts"]
                and parts[4] == "tags"
            )
            # Explicit bearer headers take precedence, including invalid ones.
            if (
                shell or login or ((read or comment or tag_edit) and self.sessions.valid(request))
            ) and (b"authorization" not in dict(scope["headers"])):
                scope = {
                    **scope,
                    "headers": [
                        *scope["headers"],
                        (b"authorization", b"Bearer " + self.token.encode()),
                    ],
                }

            async def secure_send(message):
                if message["type"] == "http.response.start":
                    message = {
                        **message,
                        "headers": [
                            *message.get("headers", []),
                            (b"x-content-type-options", b"nosniff"),
                            (b"referrer-policy", b"no-referrer"),
                            (b"cache-control", b"no-store"),
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
        return repository.annotate(
            artifact_id,
            patch={"tags": list(dict.fromkeys(tag.strip() for tag in tags))},
            expected_etag=body.get("expected_etag"),
        )

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
        response.set_cookie(
            sessions.cookie,
            sessions.signer.dumps("read"),
            max_age=SESSION_SECONDS,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
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
        from .storage import comments_table

        target = report_target(artifact_id)
        # Keep each comment's original anchor/identity while showing the discussion
        # across the report family. Publishing does not copy or mutate comments.
        statement = (
            sa.select(comments_table, artifacts.c.number.label("number"))
            .join(artifacts, artifacts.c.id == comments_table.c.artifact_id)
            .where(
                artifacts.c.project == target["project"],
                artifacts.c.namespace == target["namespace"],
                artifacts.c.name == target["name"],
            )
            .order_by(comments_table.c.created_at, comments_table.c.id)
        )
        with repository._transaction() as connection:
            return [dict(row) for row in connection.execute(statement).mappings()]

    from .server import Comment

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

    def check_comment_write(request):
        sessions.require(request)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise Unauthorized("Comments must come from this server's origin")

    def family_comment(connection, artifact_id, comment_id):
        from .storage import comments_table

        target = report_target(artifact_id)
        row = (
            connection.execute(
                sa.select(comments_table)
                .join(artifacts, artifacts.c.id == comments_table.c.artifact_id)
                .where(
                    comments_table.c.id == comment_id,
                    artifacts.c.project == target["project"],
                    artifacts.c.namespace == target["namespace"],
                    artifacts.c.name == target["name"],
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("Comment not found in this report")
        return dict(row)

    @app.put("/ui-api/reports/{artifact_id}/comments/{comment_id}")
    def update_report_comment(artifact_id: str, comment_id: str, body: Comment, request: Request):
        from .storage import comments_table

        check_comment_write(request)
        if not body.body.strip() or len(body.body) > 65536:
            raise ValidationError("Comment body must contain 1–65536 characters")
        with repository._transaction(write=True) as connection:
            comment = family_comment(connection, artifact_id, comment_id)
            connection.execute(
                comments_table.update()
                .where(comments_table.c.id == comment_id)
                .values(body=body.body)
            )
            return {**comment, "body": body.body}

    @app.delete("/ui-api/reports/{artifact_id}/comments/{comment_id}")
    def remove_report_comment(artifact_id: str, comment_id: str, request: Request):
        from .storage import comments_table

        check_comment_write(request)
        with repository._transaction(write=True) as connection:
            family_comment(connection, artifact_id, comment_id)
            connection.execute(comments_table.delete().where(comments_table.c.id == comment_id))
        return {"deleted": True}

    @app.get("/ui-api/reports/{artifact_id}/html/{block_index}/{output_index}")
    def interactive_html(artifact_id: str, block_index: int, output_index: int):
        target = report_target(artifact_id)
        value = target["manifest"]["value"]
        document = next(
            (b for b in value.get("blobs", []) if b.get("name") == "document.json"), None
        )
        if not document or block_index < 0 or output_index < 0:
            raise NotFound("HTML output not found")
        with repository.open_blob(document["digest"]) as stream:
            data = json.load(stream)
        try:
            content = data["blocks"][block_index]["outputs"][output_index]["data"]["text/html"]
        except (KeyError, IndexError, TypeError):
            raise NotFound("HTML output not found") from None
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

    @app.get("/ui-api/reports/{artifact_id}/{path:path}")
    def report_file(artifact_id: str, path: str):
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
        manager = repository.open_blob(blob["digest"])
        stream = manager.__enter__()

        def content():
            try:
                while chunk := stream.read(1024 * 1024):
                    yield chunk
            finally:
                manager.__exit__(None, None, None)

        media = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return StreamingResponse(
            content(),
            media_type=media,
            headers={
                "Content-Security-Policy": "sandbox allow-same-origin; default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; frame-src 'self'; media-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
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
        return FileResponse(target)

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

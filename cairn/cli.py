"""Small command-line interface over the same repository and client contracts."""

from __future__ import annotations

import argparse
import errno
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

from .model import CairnError, Unsupported, ValidationError


def _json(value: str):
    """Accept inline JSON, @path, or - for standard input."""
    try:
        if value == "-":
            value = sys.stdin.read()
        elif value.startswith("@"):
            value = Path(value[1:]).read_text("utf-8")
        return json.loads(
            value, parse_constant=lambda constant: (_ for _ in ()).throw(ValueError(constant))
        )
    except (ValueError, OSError) as exc:
        raise argparse.ArgumentTypeError(f"Invalid JSON: {exc}") from exc


def _watch_comments(client, reference, interval, as_json, once=False):
    """Open threads with comments sent to an agent, printed again whenever one is (re)sent.

    With ``once``, wait for threads with a comment sent after their last reply, print them and stop.
    """
    import time

    import httpx

    from .model import Pending

    seen = set()
    while True:
        try:
            found = client.comments(reference, include_resolved=False)
        except (Pending, httpx.TransportError) as exc:
            print(f"cairn: {exc}; retrying", file=sys.stderr)
            found = []
        threads = {}
        for comment in found:
            threads.setdefault(comment["reply_to"] or comment["id"], []).append(comment)
        batch = [
            thread
            for thread in threads.values()
            if any(c["sent_at"] and (c["id"], c["sent_at"]) not in seen for c in thread)
            and (not once or _unanswered(thread))
        ]
        seen.update((c["id"], c["sent_at"]) for thread in batch for c in thread if c["sent_at"])
        if batch:
            if as_json:
                print(json.dumps(batch, ensure_ascii=False), flush=True)
            else:
                print("\n".join(_comment_line(c) for thread in batch for c in thread), flush=True)
            if once:
                return 0
        time.sleep(interval)


def _watch_report(client, name, after, settle, interval):
    """Wait for body edits past revision ``after`` that rested ``settle`` seconds, or for threads
    with a comment sent after their last reply; print the report with them and stop."""
    import time
    from datetime import UTC, datetime

    import httpx

    from .model import Pending

    report_id = client._report_id(name)
    while True:
        try:
            report = client.report(name)
            found = client.backend.report_comments(report_id, include_resolved=False)
        except (Pending, httpx.TransportError) as exc:
            print(f"cairn: {exc}; retrying", file=sys.stderr)
            time.sleep(interval)
            continue
        body = report["parts"]["body"]
        after = body["revision"] if after is None else after
        rested = datetime.now(UTC) - datetime.fromisoformat(body["updated_at"])
        edited = body["revision"] > after and rested.total_seconds() >= settle
        threads = {}
        for comment in found:
            threads.setdefault(comment["reply_to"] or comment["id"], []).append(comment)
        waiting = [thread for thread in threads.values() if _unanswered(thread)]
        if edited or waiting:
            print(
                json.dumps({"report": report, "edited": edited, "threads": waiting}),
                flush=True,
            )
            return 0
        time.sleep(interval)


def _unanswered(thread):
    answered = max((c["created_at"] for c in thread if not c["for_agent"]), default="")
    return any(c["sent_at"] and c["sent_at"] > answered for c in thread)


def _source(path):
    return sys.stdin.read() if path == "-" else Path(path).read_text("utf-8")


def _print(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, default=str))


def _comment_line(comment):
    anchor, where = comment["anchor"], comment["anchor"].get("path", "-")
    if "line_start" in anchor:
        end = anchor.get("line_end", anchor["line_start"])
        where += f":{anchor['line_start']}" + (f"-{end}" if end != anchor["line_start"] else "")
    target = f"{comment['namespace']}/{comment['name']}"
    parts = [comment["id"], target + (f"@v{comment['number']}" if comment["number"] else ""), where]
    if comment.get("reply_to"):
        parts[1:] = [f"↳ {comment['reply_to']}"]
    if comment.get("author"):
        parts.append(f"({comment['author']})")
    if "time" in anchor.get("media", {}):
        parts.append(f"@{anchor['media']['time']:.1f}s")
    if anchor.get("quote"):
        parts.append(json.dumps(anchor["quote"], ensure_ascii=False))
    if comment["resolved_at"]:
        parts.append("[resolved]")
    return "  ".join(parts) + "  → " + comment["body"].replace("\n", "\n    ")


def _start_arguments(parser, namespace="artifacts"):
    parser.add_argument("name")
    parser.add_argument("--namespace", default=namespace)
    parser.add_argument("--metadata", type=_json)
    parser.add_argument("--config", type=_json, help="Store configuration in metadata.config")
    parser.add_argument("--request-id")
    parser.set_defaults(action="start")


def _log_arguments(parser):
    parser.add_argument("artifact_id")
    parser.add_argument("writer")
    parser.add_argument("data", type=_json, help="JSON object, @file, or - for standard input")
    parser.add_argument(
        "--sequence", type=int, help="Defaults to the writer's next acknowledged sequence"
    )
    parser.add_argument(
        "--kind", choices=["record", "metadata", "capture", "dependency"], default="record"
    )
    parser.set_defaults(action="log")


def _query_arguments(parser):
    parser.add_argument("reference")
    parser.add_argument("--where", type=_json)
    parser.add_argument(
        "--field", action="append", dest="fields", help="JSON pointer to project; repeatable"
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--cursor")
    parser.add_argument("--kind", choices=["record", "metadata", "capture", "dependency"])
    parser.set_defaults(action="query")


def _inspect_arguments(parser):
    parser.add_argument("reference")
    parser.set_defaults(action="inspect")


def _storage_arguments(parser):
    parser.add_argument("--database-url", help="Explicit PostgreSQL database URL")
    store = parser.add_mutually_exclusive_group()
    store.add_argument("--objects", help="Filesystem object directory with --database-url")
    store.add_argument("--s3-bucket")
    parser.add_argument("--s3-prefix", default="cairn/")
    parser.add_argument("--s3-endpoint")
    parser.add_argument("--s3-region")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="cairn", description="Versioned artifacts, Python runs, and reports."
    )
    root.add_argument(
        "--repo", metavar="PATH|URL", help="Repository path or server URL (otherwise discovery)"
    )
    root.add_argument("--project", help="Project namespace (otherwise CAIRN_PROJECT, .cairn.toml)")
    root.add_argument("--token", help="Bearer token; environment or token file is preferable")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("info", help="Show repository identity and protocol version").set_defaults(
        action="info"
    )
    _start_arguments(
        commands.add_parser("init", help="Start a run and print its artifact and writer IDs"),
        namespace="runs",
    )
    _log_arguments(commands.add_parser("log", help="Append a record to an existing writer"))
    _inspect_arguments(commands.add_parser("inspect", help="Inspect a live or finished artifact"))
    _query_arguments(commands.add_parser("query", help="Query stable, paginated records"))
    add = commands.add_parser(
        "add", help="Publish files and folders as the next version of namespace/name"
    )
    add.add_argument("name", metavar="namespace/name")
    add.add_argument("paths", nargs="+", metavar="path")
    add.add_argument("--metadata", type=_json)
    add.add_argument(
        "--use", action="append", default=[], help="Record an input artifact; repeatable"
    )
    add.add_argument(
        "--link",
        action="store_true",
        help="Reference the files where they are if the repository can read them, else copy them",
    )
    add.set_defaults(action="add")
    remove = commands.add_parser("remove", help="Remove one exact artifact; gc reclaims storage")
    remove.add_argument("reference")
    remove.set_defaults(action="remove")
    gc = commands.add_parser("gc", help="Purge removed artifacts and unreferenced content")
    gc.add_argument("--grace-seconds", type=int, default=3600)
    gc.add_argument("--apply", action="store_true", help="Delete; otherwise print a dry run")
    gc.set_defaults(action="gc")
    comments = commands.add_parser(
        "comments", help="List and resolve comments made in the UI"
    ).add_subparsers(dest="comments_command", required=True)
    listing = comments.add_parser(
        "list", help="Open comments on every version of a name, or in the whole project"
    )
    listing.add_argument("reference", nargs="?", help="A reference or name pattern; default all")
    listing.add_argument("--all", action="store_true", help="Include resolved comments")
    listing.add_argument("--exact", action="store_true", help="Only the referenced version")
    listing.add_argument("--json", action="store_true", help="Print full comment objects")
    listing.set_defaults(action="comments")
    reply = comments.add_parser("reply", help="Answer a comment in its thread")
    reply.add_argument("comment_id")
    reply.add_argument("body")
    reply.add_argument("--by", help="Who answers, e.g. an agent name")
    reply.set_defaults(action="comment-reply")
    watch = comments.add_parser(
        "watch", help="Print comment threads as they are sent to an agent, until interrupted"
    )
    watch.add_argument("reference", nargs="?", help="A reference or name pattern; default all")
    watch.add_argument("--interval", type=float, default=5.0, help="Seconds between checks")
    watch.add_argument("--json", action="store_true", help="Print each batch as one JSON line")
    watch.add_argument(
        "--once", action="store_true", help="Stop after the first batch of unanswered threads"
    )
    watch.set_defaults(action="comment-watch")
    resolve = comments.add_parser("resolve", help="Mark a comment as addressed")
    resolve.add_argument("comment_id")
    resolve.add_argument("--note", help="What was done, shown beside the comment")
    resolve.add_argument("--by", help="Who resolved it, e.g. an agent name")
    resolve.set_defaults(action="comment-resolve")
    reopen = comments.add_parser("reopen", help="Mark a resolved comment as open again")
    reopen.add_argument("comment_id")
    reopen.set_defaults(action="comment-reopen")

    artifacts = commands.add_parser(
        "artifact", help="Artifact lifecycle and records"
    ).add_subparsers(dest="artifact_command", required=True)
    _start_arguments(artifacts.add_parser("start"))
    _log_arguments(artifacts.add_parser("log"))
    _inspect_arguments(artifacts.add_parser("inspect"))
    _query_arguments(artifacts.add_parser("query"))
    listing = artifacts.add_parser("list")
    listing.add_argument("--namespace")
    listing.add_argument(
        "--where", type=_json, help="JSON predicate using /metadata and /annotations paths"
    )
    listing.add_argument("--state", choices=["live", "closing", "finished"])
    listing.add_argument("--limit", type=int, default=100)
    listing.add_argument("--cursor")
    listing.set_defaults(action="list")
    annotation = artifacts.add_parser("annotate", help="Merge editable artifact annotations")
    annotation.add_argument("reference")
    annotation.add_argument("patch", type=_json)
    annotation.add_argument("--expected-etag")
    annotation.add_argument("--request-id")
    annotation.add_argument(
        "--name", action="store_true", help="Annotate every version of the referenced name"
    )
    annotation.set_defaults(action="annotate")
    versions = artifacts.add_parser("versions")
    versions.add_argument("artifact_id")
    versions.set_defaults(action="versions")
    register = artifacts.add_parser("writer")
    register.add_argument("artifact_id")
    register.set_defaults(action="writer")
    close = artifacts.add_parser("close-writer")
    close.add_argument("artifact_id")
    close.add_argument("writer")
    close.add_argument("--final-sequence", type=int)
    close.set_defaults(action="close-writer")
    sub = artifacts.add_parser("finish")
    sub.add_argument("artifact_id")
    sub.add_argument("--value", type=_json)
    sub.add_argument("--metadata", type=_json)
    sub.add_argument("--request-id")
    sub.add_argument(
        "--writer", help="Close this writer at its acknowledged sequence before finishing"
    )
    sub.add_argument(
        "--outcome",
        choices=["completed", "failed", "cancelled", "incomplete"],
        default="completed",
    )
    sub.set_defaults(action="finish")
    update = artifacts.add_parser("update")
    update.add_argument("artifact_id")
    update.add_argument("writer")
    update.add_argument("data", type=_json)
    update.set_defaults(action="log", kind="metadata", sequence=None)

    reports = commands.add_parser(
        "report", help="Reports: the author's text with slots that agents fill"
    ).add_subparsers(dest="report_command", required=True)
    reports.add_parser("list", help="Reports of the project").set_defaults(action="report-list")
    for operation, text in (
        ("show", "Print a report's parts with their revisions"),
        ("delete", "Remove a report with its revisions and comments"),
    ):
        sub = reports.add_parser(operation, help=text)
        sub.add_argument("name")
        sub.set_defaults(action="report-" + operation)
    new = reports.add_parser("new", help="Create a report")
    new.add_argument("name")
    new.add_argument(
        "source", nargs="?", help="Markdown file with the body, or - for standard input"
    )
    new.add_argument("--by", help="Who writes it")
    new.set_defaults(action="report-new")
    write = reports.add_parser("write", help="Save a new revision of one part")
    write.add_argument("name")
    write.add_argument("part", help="An agent slot, or body for the author's Markdown")
    write.add_argument("source", nargs="?", default="-", help="File, or - for standard input")
    write.add_argument("--revision", type=int, help="Fail unless the part is at this revision")
    write.add_argument("--by", help="Who writes it, e.g. an agent name")
    write.set_defaults(action="report-write")
    comment = reports.add_parser("comment", help="Comment on a part, e.g. to flag a problem")
    comment.add_argument("name")
    comment.add_argument("body")
    comment.add_argument("--quote", help="The passage the comment is about")
    comment.add_argument("--part", default="body")
    comment.add_argument("--by", help="Who comments, e.g. an agent name")
    comment.set_defaults(action="report-comment")
    watch = reports.add_parser(
        "watch", help="Wait for edits to the body or comments sent to an agent, print them, stop"
    )
    watch.add_argument("name")
    watch.add_argument(
        "--after", type=int, help="Body revision already handled; default the current one"
    )
    watch.add_argument(
        "--settle", type=float, default=30.0, help="Seconds an edit rests before it counts"
    )
    watch.add_argument("--interval", type=float, default=5.0, help="Seconds between checks")
    watch.set_defaults(action="report-watch")

    repository_parser = commands.add_parser(
        "repo", help="Repository transfer, integrity checks and garbage collection"
    )
    _storage_arguments(repository_parser)
    repositories = repository_parser.add_subparsers(dest="repo_command", required=True)
    for operation, argument in (("export", "destination"), ("import", "source")):
        sub = repositories.add_parser(operation)
        sub.add_argument(argument)
        sub.set_defaults(action="repo-" + operation)
    repositories.add_parser(
        "migrate", help="Upgrade a schema 6 or 7 repository in place; export a backup first"
    ).set_defaults(action="repo-migrate")
    check = repositories.add_parser("check")
    check.add_argument("--verify-blobs", action="store_true")
    check.set_defaults(action="repo-check")
    gc = repositories.add_parser("gc")
    gc.add_argument("--grace-seconds", type=int, default=3600)
    gc.add_argument(
        "--apply",
        action="store_true",
        help="Delete unreferenced objects; otherwise print a dry run",
    )
    gc.set_defaults(action="repo-gc")

    server = commands.add_parser("server", help="Serve the repository with one bearer token")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument(
        "--port", type=int, default=None, help="Port (default: 8765, or a free port if occupied)"
    )
    server.add_argument(
        "--no-auth", action="store_true", help="Development only: disable authentication"
    )
    server.add_argument("--no-ui", action="store_true", help="Disable the optional browser UI")
    server.add_argument(
        "--link-root",
        action="append",
        default=[],
        metavar="FOLDER",
        help="Let clients link files under this folder instead of uploading them; repeatable",
    )
    _storage_arguments(server)
    server.set_defaults(action="server")
    return root


def _close_writer(backend, artifact_id, writer, final_sequence=None):
    if final_sequence is None:
        final_sequence = backend.writer(artifact_id, writer)["next_sequence"] - 1
    return backend.close_writer(artifact_id, writer, final_sequence)


def _repository(args, *, migrate=False):
    from .objects import S3, Files
    from .storage import Repository

    if args.repo and args.repo.startswith(("http://", "https://")):
        raise ValidationError("Direct storage access requires a local --repo or --database-url")
    object_store = None
    if args.s3_bucket:
        object_store = S3(
            args.s3_bucket,
            prefix=args.s3_prefix,
            endpoint_url=args.s3_endpoint,
            region=args.s3_region,
        )
    elif args.objects:
        object_store = Files(args.objects)
    if args.database_url:
        if object_store is None:
            raise ValidationError("--database-url requires --objects or --s3-bucket")
        return Repository(
            database_url=args.database_url, object_store=object_store, migrate=migrate
        )
    return Repository(args.repo or ".cairn/repo", object_store=object_store, migrate=migrate)


def _server_socket(host, port):
    """Reserve the listening socket before advertising its URL."""
    requested = 8765 if port is None else port
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((host, requested))
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE or port is not None:
                raise
            listener.bind((host, 0))
            print(
                f"Warning: port {requested} is occupied; using port {listener.getsockname()[1]}.",
                file=sys.stderr,
            )
        listener.listen(2048)
        listener.setblocking(False)
        return listener
    except BaseException:
        listener.close()
        raise


def _serve(args):
    from .config import ensure_token, resolve_repo, startup_urls

    try:
        import uvicorn

        from .server import create_app
    except ModuleNotFoundError as exc:
        if exc.name not in {"uvicorn", "fastapi"}:
            raise
        raise ValidationError(
            "Server dependencies are missing. Run 'uv run --extra all cairn server' "
            "(or --extra ui), or install 'cairn2[all]' with pip."
        ) from exc
    if not args.database_url:
        repo = resolve_repo(args.repo)
        if repo.startswith(("http://", "https://")):
            raise ValidationError(
                "The repo is a remote server. "
                "Use --repo PATH to select the local repository to serve."
            )
        args.repo = repo
    try:
        repository = _repository(args)
        repository.link_roots = [Path(root).resolve() for root in args.link_root]
    except Unsupported as exc:
        if exc.details.get("schema_version") not in (6, 7, 8) or not sys.stdin.isatty():
            raise
        print(
            f"{exc}.\nMigrate this repository to the current schema now? [y/N] ",
            end="",
            file=sys.stderr,
        )
        if input().strip().lower() not in {"y", "yes"}:
            raise
        repository = _repository(args, migrate=True)
        print("Repository migrated.", file=sys.stderr)
    listener = None
    try:
        listener = _server_socket(args.host, args.port)
        port = listener.getsockname()[1]
        # Creating a nested .cairn/token would shadow the parent repository on
        # the next SDK discovery. Keep the token in the existing project.
        cwd = Path.cwd()
        token_root = next(
            (
                parent
                for parent in (cwd, *cwd.parents)
                if (parent / ".cairn").exists() or (parent / ".cairn.toml").is_file()
            ),
            cwd,
        )
        token = ensure_token(args.token, cwd=token_root) if not args.no_auth else None
        if repository.path is not None:
            print(f"Cairn repository: {repository.path}", file=sys.stderr)
        from .webui import available

        ui = not args.no_ui and available()
        app = create_app(repository, token=token, ui=ui, no_auth=args.no_auth)
        for url in startup_urls(args.host, port):
            print(f"Cairn {'UI + server' if ui else 'server'}: {url}", file=sys.stderr)
        if args.no_auth:
            print("Development mode: authentication disabled", file=sys.stderr)
        elif ui:
            for url in startup_urls(args.host, port, token=token):
                print(f"Cairn login: {url}", file=sys.stderr)
        try:
            uvicorn.Server(uvicorn.Config(app, host=args.host, port=port)).run(sockets=[listener])
        except KeyboardInterrupt:
            # Uvicorn has already unwound lifespan shutdown before re-raising SIGINT.
            pass
    finally:
        if listener is not None:
            listener.close()
        repository.close()


def _dispatch(args, client):
    backend, action = client.backend, args.action
    if action == "info":
        return backend.info()
    if action == "start":
        metadata = args.metadata if args.metadata is not None else {}
        if not isinstance(metadata, dict):
            raise ValidationError("Initial metadata must be a JSON object")
        if args.config is not None:
            metadata = {**metadata, "config": args.config}
        result = backend.start(
            client.project, args.namespace, args.name, request_id=args.request_id
        )
        if metadata:
            artifact_id, writer = result["artifact"]["id"], result["writer"]["id"]
            backend.append_batch(
                artifact_id, writer, [{"sequence": 1, "kind": "metadata", "data": metadata}]
            )
            result["writer"] = backend.writer(artifact_id, writer)
        return result
    if action == "log":
        if not isinstance(args.data, dict):
            raise ValidationError("Record or metadata data must be a JSON object")
        sequence = args.sequence
        if sequence is None:
            sequence = backend.writer(args.artifact_id, args.writer)["next_sequence"]
        return backend.append_batch(
            args.artifact_id,
            args.writer,
            [{"sequence": sequence, "kind": args.kind, "data": args.data}],
        )
    if action == "inspect":
        return backend.resolve(args.reference, project=client.project)
    if action == "add":
        namespace, separator, name = args.name.partition("/")
        if not separator or "@" in args.name:
            raise ValidationError("add expects namespace/name without a version")
        if args.metadata is not None and not isinstance(args.metadata, dict):
            raise ValidationError("Metadata must be a JSON object")
        for path in args.paths:
            if not Path(path).exists():
                raise FileNotFoundError(f"No such file or folder: {path}")
        with client.start(namespace, name) as artifact:
            if args.metadata:
                artifact.update(args.metadata)
            for reference in args.use:
                artifact.use(reference)
            for path in args.paths:
                artifact.add(path, link=args.link)
        return artifact.result
    if action == "remove":
        return client.remove(args.reference)
    if action == "gc":
        return client.gc(apply=args.apply, grace_seconds=args.grace_seconds)
    if action == "query":
        return backend.query(
            args.reference,
            project=client.project,
            where=args.where,
            fields=args.fields,
            limit=args.limit,
            cursor=args.cursor,
            kind=args.kind,
        )
    if action == "list":
        return backend.list_artifacts(
            project=client.project,
            namespace=args.namespace,
            where=args.where,
            state=args.state,
            limit=args.limit,
            cursor=args.cursor,
        )
    if action == "annotate":
        artifact = backend.resolve(args.reference, project=client.project)
        return backend.annotate(
            artifact["id"],
            args.patch,
            expected_etag=args.expected_etag,
            target="name" if args.name else "artifact",
            request_id=args.request_id,
        )
    if action == "comments":
        found = client.comments(
            args.reference,
            scope="artifact" if args.exact else "name",
            include_resolved=args.all,
        )
        if args.json:
            return found
        for comment in found:
            print(_comment_line(comment))
        return 0
    if action == "comment-reply":
        return client.reply_comment(args.comment_id, args.body, by=args.by)
    if action == "comment-watch":
        return _watch_comments(client, args.reference, args.interval, args.json, args.once)
    if action == "comment-resolve":
        return client.resolve_comment(args.comment_id, note=args.note, by=args.by)
    if action == "comment-reopen":
        return client.reopen_comment(args.comment_id)
    if action == "versions":
        return backend.versions(args.artifact_id, project=client.project)
    if action == "writer":
        return backend.register_writer(args.artifact_id)
    if action == "close-writer":
        return _close_writer(backend, args.artifact_id, args.writer, args.final_sequence)
    if action == "finish":
        kwargs = {"value": args.value, "metadata": args.metadata, "request_id": args.request_id}
        if action == "finish":
            if args.writer:
                _close_writer(backend, args.artifact_id, args.writer)
            kwargs.update(outcome=args.outcome)
        return getattr(backend, action)(args.artifact_id, **kwargs)
    if action == "report-list":
        return client.reports()
    if action == "report-show":
        return client.report(args.name)
    if action == "report-delete":
        return client.delete_report(args.name)
    if action == "report-new":
        body = _source(args.source) if args.source else ""
        return client.create_report(args.name, body, by=args.by)
    if action == "report-write":
        return client.write_report(
            args.name, args.part, _source(args.source), expected_revision=args.revision, by=args.by
        )
    if action == "report-comment":
        return client.comment_report(
            args.name, args.body, quote=args.quote, part=args.part, by=args.by
        )
    if action == "report-watch":
        return _watch_report(client, args.name, args.after, args.settle, args.interval)
    if action.startswith("repo-"):
        from .ops import check_repository, export_repository, import_repository
        from .storage import Repository

        if not isinstance(backend, Repository):
            raise ValidationError(
                "Repository maintenance uses a direct local connection; choose --repo"
            )
        if action == "repo-export":
            return {"path": str(export_repository(backend, args.destination))}
        if action == "repo-import":
            return import_repository(backend, args.source)
        if action == "repo-migrate":
            return {"schema_version": backend.info()["schema_version"]}
        if action == "repo-check":
            result = check_repository(backend, verify_blobs=args.verify_blobs)
            if not result.get("ok"):
                _print(result)
                return 1
            return result
        return backend.collect_garbage(grace_seconds=args.grace_seconds, dry_run=not args.apply)
    raise ValidationError(f"Unknown CLI action: {action}")


def main(argv=None) -> int:
    """Return an exit code; errors are machine-readable JSON on stderr."""
    command = parser()
    args = command.parse_args(argv)
    try:
        if args.action == "server":
            _serve(args)
            return 0
        from .config import resolve_project
        from .sdk import Client

        if args.action.startswith("repo-"):
            client = SimpleNamespace(
                backend=_repository(args, migrate=args.action == "repo-migrate"),
                project=resolve_project(args.project),
            )
        else:
            client = Client(args.repo, project=args.project, token=args.token)
        try:
            result = _dispatch(args, client)
            if isinstance(result, int):
                return result
            _print(result)
            return 0
        finally:
            if isinstance(client, Client):
                client.close()
            else:
                client.backend.close()
    except CairnError as exc:
        print(
            json.dumps(
                {"error": exc.code, "message": str(exc), "details": exc.details}, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError) as exc:
        print(
            json.dumps({"error": "invalid_input", "message": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

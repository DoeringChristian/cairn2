"""Small command-line interface over the same repository and client contracts."""

from __future__ import annotations

import argparse
import errno
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

from .model import CairnError, ValidationError


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


def _print(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, default=str))


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
        prog="cairn", description="Versioned artifacts, Python runs, and local executable reports."
    )
    root.add_argument(
        "--repo", metavar="PATH|URL", help="Repository path or server URL (otherwise discovery)"
    )
    root.add_argument("--project", default="default")
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
    remove = commands.add_parser("remove", help="Remove one exact artifact; gc reclaims storage")
    remove.add_argument("reference")
    remove.set_defaults(action="remove")
    gc = commands.add_parser("gc", help="Purge removed artifacts and unreferenced content")
    gc.add_argument("--grace-seconds", type=int, default=3600)
    gc.add_argument("--apply", action="store_true", help="Delete; otherwise print a dry run")
    gc.set_defaults(action="gc")

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
        "report", help="Build a supported QMD/Markdown subset locally"
    ).add_subparsers(dest="report_command", required=True)
    new = reports.add_parser("new", help="Create a report source; default NewReport.qmd")
    new.add_argument("destination", nargs="?", default="NewReport.qmd")
    new.add_argument("--title", default="New report")
    new.set_defaults(action="report-new")
    build = reports.add_parser(
        "build", help="Pin inputs, execute a fresh Python kernel, and create a portable bundle"
    )
    build.add_argument("source")
    build.add_argument("--output", "-o")
    build.add_argument("--timeout", type=int, default=120)
    build.add_argument("--kernel", default="python3")
    build.add_argument("--publish", action="store_true")
    build.add_argument("--name", help="Artifact name when --publish is set")
    build.set_defaults(action="report-build")
    publish = reports.add_parser(
        "publish", help="Publish a verified complete build as a report artifact"
    )
    publish.add_argument("directory")
    publish.add_argument("--name")
    publish.set_defaults(action="report-publish")
    for operation in ("copy", "export"):
        sub = reports.add_parser(
            operation,
            help="Copy source with exact input IDs"
            if operation == "copy"
            else "Export all portable report files",
        )
        sub.add_argument("reference")
        sub.add_argument("destination")
        sub.set_defaults(action="report-" + operation)

    repository_parser = commands.add_parser(
        "repo", help="Repository transfer, integrity checks and garbage collection"
    )
    _storage_arguments(repository_parser)
    repositories = repository_parser.add_subparsers(dest="repo_command", required=True)
    for operation, argument in (("export", "destination"), ("import", "source")):
        sub = repositories.add_parser(operation)
        sub.add_argument(argument)
        sub.set_defaults(action="repo-" + operation)
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
    _storage_arguments(server)
    server.set_defaults(action="server")
    return root


def _close_writer(backend, artifact_id, writer, final_sequence=None):
    if final_sequence is None:
        final_sequence = backend.writer(artifact_id, writer)["next_sequence"] - 1
    return backend.close_writer(artifact_id, writer, final_sequence)


def _repository(args):
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
        return Repository(database_url=args.database_url, object_store=object_store)
    return Repository(args.repo or ".cairn/repo", object_store=object_store)


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
    repository = _repository(args)
    listener = None
    try:
        listener = _server_socket(args.host, args.port)
        port = listener.getsockname()[1]
        # Creating a nested .cairn/token would shadow the parent repository on
        # the next SDK discovery. Keep the token in the existing project.
        cwd = Path.cwd()
        token_root = next(
            (parent for parent in (cwd, *cwd.parents) if (parent / ".cairn").exists()),
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
            request_id=args.request_id,
        )
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
    if action.startswith("report-"):
        from . import reports

        if action == "report-build":
            result = reports.build(
                args.source,
                args.output,
                client=client,
                timeout=args.timeout,
                kernel_name=args.kernel,
            )
            output = {"directory": str(result.directory), "manifest": result.manifest}
            if args.publish:
                output["publication"] = reports.publish(
                    result.directory, client=client, name=args.name
                )
            return output
        if action == "report-publish":
            return reports.publish(args.directory, client=client, name=args.name)
        operation = reports.copy if action == "report-copy" else reports.export
        return {"path": str(operation(args.reference, args.destination, client=client))}
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
    args = parser().parse_args(argv)
    try:
        if args.action == "server":
            _serve(args)
            return 0
        if args.action == "report-new":
            from .reports import new

            _print({"path": str(new(args.destination, title=args.title))})
            return 0
        from .sdk import Client

        if args.action.startswith("repo-"):
            client = SimpleNamespace(backend=_repository(args), project=args.project)
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

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

from cairn import CairnError, Client
from cairn.__main__ import new_token
from cairn.client import settings


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cairn-ui", description="Show what Cairn holds, live; keep workspaces and reports.")
    parser.add_argument("--repo", help="A repository, or the URL of a Cairn server")
    parser.add_argument("--state", default=".cairn", help="The folder that keeps workspaces and reports")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-auth", action="store_true")
    commands = parser.add_subparsers(dest="command")
    sub = commands.add_parser("workspace", help="Workspaces of a running cairn-ui, as JSON")
    sub.add_argument("action", choices=["list", "show", "write", "delete"])
    sub.add_argument("name", nargs="?", default="")
    sub.add_argument("--file", help="What to write; standard input when not given")
    sub.add_argument("--expected", type=int, metavar="REVISION", help="Only if it still is at this revision; 0: not there")
    other = commands.add_parser("report", help="Reports of a running cairn-ui, as Markdown")
    other.add_argument("action", choices=["list", "show", "write", "edit", "delete"])
    other.add_argument("name", nargs="?", default="")
    other.add_argument("--file", help="What to write; standard input when not given")
    other.add_argument("--base", help="The text you changed, as a file: what others wrote since is kept")
    other.add_argument("--old", help="The passage to replace, which the report holds once")
    other.add_argument("--new")
    for command in (sub, other):
        command.add_argument("--project")
        command.add_argument("--url", help="The cairn-ui to ask (CAIRN_UI; http://127.0.0.1:8766 without)")
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            return pages(args)
        if (args.action == "list") == bool(args.name):
            raise CairnError(f"usage: cairn-ui {args.command} {args.action}{'' if args.action == 'list' else ' NAME'}")
        known = settings()
        url = args.url or os.environ.get("CAIRN_UI") or known.get("ui") or "http://127.0.0.1:8766"
        headers = {"Authorization": f"Bearer {known['token']}"} if known.get("token") else {}
        with httpx.Client(base_url=url, headers=headers, timeout=60) as http:
            return (workspace if args.command == "workspace" else report)(args, Asking(http), args.project or known.get("project") or "default")
    except (CairnError, OSError, ValueError, httpx.TransportError) as error:
        print(f"cairn-ui: {error}", file=sys.stderr)
        return 1


class Asking:
    def __init__(self, http):
        self.http = http

    def __call__(self, method, path, **options):
        response = self.http.request(method, path, **options)
        if response.is_error:
            try:
                message = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = f"The server answered {response.status_code}"
            raise CairnError(message)
        return response.json()


def given(args):
    return Path(args.file).read_text() if args.file else sys.stdin.read()


def workspace(args, ask, project):
    found = ask("GET", "/ui-api/workspaces", params={"project": project})
    if args.action == "write":
        body = {"project": project, "name": args.name, "layout": json.loads(given(args)), "expected_revision": args.expected}
        found = ask("PUT", "/ui-api/workspaces", json=body)
    elif args.action != "list":
        identifier = next((item["id"] for item in found if item["name"] == args.name), None)
        if identifier is None:
            raise CairnError(f"No workspace {args.name} in {project}")
        found = ask("DELETE" if args.action == "delete" else "GET", f"/ui-api/workspaces/{identifier}")
    if args.action != "delete":
        print(json.dumps(found, indent=2, ensure_ascii=False))


def report(args, ask, project):
    path, where = "/ui-api/report", {"project": project, "name": args.name}
    if args.action == "list":
        for item in ask("GET", "/ui-api/reports", params={"project": project}):
            print(item["name"])
    elif args.action == "show":
        print(ask("GET", path, params=where)["text"], end="")
    elif args.action == "write":
        ask("PUT", path, params=where, json={"text": given(args), **({"base": Path(args.base).read_text()} if args.base else {})})
    elif args.action == "edit":
        if args.old is None or args.new is None:
            raise CairnError("usage: cairn-ui report edit NAME --old TEXT --new TEXT")
        ask("POST", path, params=where, json={"old": args.old, "new": args.new})
    else:
        ask("DELETE", path, params=where)


def pages(args):
    import uvicorn

    from .reports import Reports
    from .server import create_app
    from .workspaces import Workspaces

    token = None if args.no_auth else settings().get("token") or new_token()
    state = Path(args.state)
    workspaces = Workspaces(state / "ui.sqlite3")
    with Client(args.repo) as client:
        print(f"Showing {client.repo} at http://{args.host}:{args.port}" + (f"/#token={token}" if token and sys.stderr.isatty() else ""), file=sys.stderr)
        app = create_app(client, workspaces, Reports(state / "reports", state / "ui.sqlite3"), token=token)
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning", timeout_graceful_shutdown=2)


if __name__ == "__main__":
    sys.exit(main())

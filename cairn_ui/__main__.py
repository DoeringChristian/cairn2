import argparse
import json
import sys
from pathlib import Path

from cairn import CairnError, Client
from cairn.__main__ import new_token
from cairn.client import settings

from .workspaces import Workspaces


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cairn-ui", description="Show what Cairn holds, live.")
    parser.add_argument("--repo", help="A repository, or the URL of a Cairn server")
    parser.add_argument("--state", default=".cairn/ui.sqlite3", help="Where workspaces are kept")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-auth", action="store_true")
    commands = parser.add_subparsers(dest="command")
    sub = commands.add_parser("workspace", help="Read or write a workspace as JSON")
    sub.add_argument("action", choices=["list", "show", "write", "delete"])
    sub.add_argument("name", nargs="?", default="")
    sub.add_argument("--project")
    sub.add_argument("--file", help="The layout to write; standard input when not given")
    sub.add_argument("--expected", type=int, metavar="REVISION", help="Only if it still is at this revision; 0: not there")
    args = parser.parse_args(argv)
    try:
        return workspace(args) if args.command == "workspace" else pages(args)
    except (CairnError, OSError, ValueError) as error:
        print(f"cairn-ui: {error}", file=sys.stderr)
        return 1


def workspace(args):
    store, project = Workspaces(args.state), args.project or settings().get("project") or "default"
    if (args.action == "list") == bool(args.name):
        raise CairnError(f"usage: cairn-ui workspace {args.action}{'' if args.action == 'list' else ' NAME'}")
    if args.action == "list":
        found = store.list(project)
    elif args.action == "show":
        found = store.named(project, args.name)
    elif args.action == "write":
        found = store.write(project, args.name, json.loads(Path(args.file).read_text() if args.file else sys.stdin.read()), args.expected)
    else:
        store.delete(project, args.name)
        return 0
    print(json.dumps(found, indent=2, ensure_ascii=False))
    return 0


def pages(args):
    import uvicorn

    from .server import create_app

    token = None if args.no_auth else settings().get("token") or new_token()
    with Client(args.repo) as client:
        print(f"Showing {client.repo} at http://{args.host}:{args.port}" + (f"/#token={token}" if token and sys.stderr.isatty() else ""), file=sys.stderr)
        uvicorn.run(create_app(client, Workspaces(args.state), token=token), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())

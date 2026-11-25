import argparse
import json
import os
import secrets
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from .client import OUTCOMES, Client, literal, settings
from .errors import CairnError
from .types import Array, Figure, Image, Table


def plain(value):
    if isinstance(value, (Image, Array, Table, Figure)):
        value = value.expand()
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, float) and value - value != 0:
        return str(value)  # NaN and infinities have no JSON
    return value if isinstance(value, (str, int, float, bool, type(None))) else repr(value)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cairn", description="Log runs, store artifacts, and find them again.")
    parser.add_argument("--repo")
    parser.add_argument("--project")
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("find", help="Artifacts by name and values")
    sub.add_argument("name", nargs="?", default="")
    sub.add_argument("--where", action="append", default=[], metavar="CONDITION", help="Such as 'loss < 0.1' or tag:paper; all must hold")
    sub.add_argument("--order", default="")
    sub.add_argument("--limit", type=int, default=50)
    sub.add_argument("--latest", action="store_true", help="Only the newest completed version of each name")
    sub.add_argument("--state", default="", help="live, finished, completed or deleted")
    sub.add_argument("--ids", action="store_true", help="Print IDs too")
    sub.add_argument("--show", action="append", default=[], metavar="PATH")
    sub = commands.add_parser("files", help="Files of every artifact, or of one")
    sub.add_argument("pattern", nargs="?", default="")
    sub.add_argument("--text", default="")
    sub.add_argument("--in", dest="ref")
    for name, text in (("records", "Records, one JSON line each"), ("console", "What a program printed"), ("links", "What an artifact uses, and what uses it"), ("stop", "Finish an abandoned artifact"), ("delete", "Hide an artifact"), ("restore", "Show a deleted artifact again, by its ID")):
        commands.add_parser(name, help=text).add_argument("ref")
    sub = commands.add_parser("show", help="What an artifact is, and its current values")
    sub.add_argument("ref")
    sub.add_argument("--json", action="store_true", help="Only the values")
    sub = commands.add_parser("start", help="Start an artifact to log to from scripts, and print its ID")
    sub.add_argument("name")
    sub.add_argument("values", nargs="*", metavar="KEY=VALUE", help="Its configuration")
    sub = commands.add_parser("log", help="Add a record to a live artifact")
    sub.add_argument("ref")
    sub.add_argument("values", nargs="*", metavar="KEY=VALUE")
    sub.add_argument("--file", action="append", default=[], metavar="KEY=PATH")
    sub = commands.add_parser("finish", help="Finish an artifact started from a script")
    sub.add_argument("ref")
    sub.add_argument("--outcome", default="completed", choices=OUTCOMES)
    sub = commands.add_parser("add", help="Store files or folders as a new version")
    sub.add_argument("name")
    sub.add_argument("paths", nargs="+")
    sub.add_argument("--link", action="store_true")
    sub = commands.add_parser("download", help="Write an artifact's files to a folder")
    sub.add_argument("ref")
    sub.add_argument("to")
    sub.add_argument("--only", default="")
    tag = commands.add_parser("tag", help="Describe artifacts by tags", usage="cairn tag [add] REF TAG[=VALUE] ...  |  remove REF TAG ...  |  list REF")
    alias = commands.add_parser("alias", help="Point names at versions", usage="cairn alias [add] REF ALIAS [--expected REF]  |  remove NAME ALIAS  |  list NAME")
    for sub in (tag, alias):
        sub.add_argument("words", nargs="+", metavar="WORD")
    alias.add_argument("--expected", metavar="REF", help="Only if it still points there; none: at nothing")
    commands.add_parser("projects", help="Projects and their artifacts")
    commands.add_parser("recover", help="Send what ended processes left behind")
    for name in ("check", "rebuild", "collect"):
        commands.add_parser(name)
    sub = commands.add_parser("server", help="Serve the repository")
    sub.add_argument("--host", default="127.0.0.1")
    sub.add_argument("--port", type=int, default=8765)
    sub.add_argument("--no-auth", action="store_true")
    sub.add_argument("--link-root", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        return run(args) or 0
    except (CairnError, OSError) as error:
        print(f"cairn: {error}", file=sys.stderr)
        return 1


def head(found):
    def when(seconds):
        return datetime.fromtimestamp(seconds).astimezone().isoformat(" ", "seconds")

    state = found.outcome or "finished" if found.finished else f"live, last heard {when(found.heard_at)}"
    lines = {"artifact": f"{found.ref}  ({found.id}, project {found.project})", "state": state, "started": when(found.created_at)}
    if found.finished:
        lines["finished"] = when(found.finished_at)
    lines["records"] = len(found)
    if found.aliases:
        lines["aliases"] = ", ".join(found.aliases)
    if found.tags:
        lines["tags"] = ", ".join(k if v is None else f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in found.tags.items())
    print("\n".join(f"{k + ':':10} {v}" for k, v in lines.items()), end="\n\n")


def pairs(given, read):
    found = {}
    for text in given:
        key, equals, value = text.partition("=")
        if not equals or not key or key in found:
            raise CairnError(f"Not KEY=VALUE, or given twice: {text}")
        found[key] = read(value)
    return found


def label(args, client):
    """cairn tag and cairn alias; a reference never reads add, remove or list."""
    action, *words = args.words if args.words[0] in ("add", "remove", "list") else ("add", *args.words)
    if action == "list":
        if len(words) != 1:
            raise CairnError(f"usage: cairn {args.command} list REF")
        if args.command == "tag":
            for key, value in client.get(words[0]).tags.items():
                print(key if value is None else f"{key}={json.dumps(value, ensure_ascii=False)}")
        else:
            for name, found in client.aliases(words[0]).items():
                print(f"{name}\t{found.ref}")
        return
    ref, *names = words or [""]
    if not names or (args.command == "alias" and len(names) != 1):
        raise CairnError(f"usage: cairn {args.command} {action} REF {'TAG ...' if args.command == 'tag' else 'ALIAS'}")
    if args.command == "tag" and action == "add":
        pairs = [name.partition("=") for name in names]
        if len({key for key, _, _ in pairs}) < len(pairs):
            raise CairnError("A tag is given twice")
        client._label(ref, {key: literal(value) if equals else None for key, equals, value in pairs if not key.startswith("@") or _refuse(key)})
    elif args.command == "tag":
        client.untag(ref, *names)
    elif action == "add":
        client.alias(ref, names[0], expected=... if args.expected is None else None if args.expected == "none" else args.expected)
    else:
        client.unalias(ref, names[0])


def _refuse(key):
    raise CairnError(f"Tags do not start with @: {key}")


def new_token():
    path = Path(".cairn/token").absolute()
    path.parent.mkdir(exist_ok=True)
    token = secrets.token_urlsafe(32)
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
        out.write(token + "\n")
    print(f"New token in {path}", file=sys.stderr)
    return token


def run(args):
    if args.command == "server":
        import uvicorn

        from .server import create_app
        from .store import Repository

        token = None if args.no_auth else settings().get("token") or new_token()
        repository = Repository(args.repo or settings().get("repo") or ".cairn/repo", link_roots=args.link_root)
        print(f"Serving {repository.path} at http://{args.host}:{args.port}", file=sys.stderr)
        uvicorn.run(create_app(repository, token=token), host=args.host, port=args.port, log_level="warning")
        return None
    with Client(args.repo, project=args.project) as client:
        if args.command == "find":
            found = client.find(args.name, where=args.where, order=args.order, limit=args.limit, latest=args.latest, state=args.state, values=args.show)
            for item in found:
                print("\t".join([item.ref, *([item.id] if args.ids else []), *(f"{k}={plain(v)}" for k, v in item.values.items())]))
            print(f"{len(found)} of {found.total}", file=sys.stderr)
        elif args.command == "files":
            for item in client.get(args.ref).files(args.pattern, text=args.text) if args.ref else client.files(args.pattern, text=args.text):
                print(f"{item.size}\t{item.reference}")
        elif args.command == "show":
            found = client.get(args.ref)
            if not args.json:
                head(found)
            print(json.dumps(plain(found.last()), indent=2, ensure_ascii=False))
        elif args.command == "records":
            for ordinal, record in client.get(args.ref).records():
                print(json.dumps({"ordinal": ordinal, **plain(record)}, ensure_ascii=False))
        elif args.command == "console":
            print(client.get(args.ref).console(), end="")
        elif args.command == "projects":
            for name, found in client.projects().items():
                print("\t".join([name, *(f"{n}={count}" for n, count in sorted(found["artifacts"].items()))]))
        elif args.command == "links":
            reader = client.get(args.ref)
            for arrow, links in (("uses", reader.uses()), ("used by", reader.used_by())):
                for path, other in links:
                    print(f"{arrow}\t{other.ref}\t{path}")
        elif args.command == "start":
            namespace, _, name = args.name.partition("/")
            made = client.artifact(namespace, name, pairs(args.values, literal) if args.values else None, provenance=namespace == "runs")
            made.leave()
            made.flush()
            print(made.id)
        elif args.command == "log":
            made = client.attach(args.ref)
            fields, files = pairs(args.values, literal), pairs(args.file, Path)
            if not fields and not files or fields.keys() & files.keys():
                raise CairnError("Give each key once, as KEY=VALUE or --file KEY=PATH")
            made.log({**fields, **files})
            made.leave()
            made.flush()
        elif args.command == "finish":
            print(client.get(args.ref).finish(args.outcome).ref)
        elif args.command == "add":
            from .types import Directory, File

            namespace, _, name = args.name.partition("/")
            with client.artifact(namespace, name) as made:
                for path in args.paths:
                    made.log({Path(path).resolve().name: (Directory if Path(path).is_dir() else File)(path, link=args.link)})
            print(f"{args.name}@v{made.version}")
        elif args.command == "download":
            for path in client.get(args.ref).download(args.to, args.only):
                print(path)
        elif args.command in ("tag", "alias"):
            label(args, client)
        elif args.command == "stop":
            print(client.get(args.ref).finish("incomplete", gone=True).ref)
        elif args.command == "restore":
            print(client.restore(args.ref).ref)
        elif args.command == "delete":
            client.delete(args.ref)
        elif args.command == "recover":
            for folder, error in client.recover():
                print(f"{folder}: {error}")
        elif client.remote:
            raise CairnError("This needs the repository itself, not a server")
        elif args.command == "check":
            problems = client.backend.check(content=True)
            print("\n".join(problems) or "ok")
            return bool(problems)
        elif args.command == "rebuild":
            client.backend.rebuild()
        elif args.command == "collect":
            print(client.backend.collect(), "freed")
    return None


if __name__ == "__main__":
    sys.exit(main())

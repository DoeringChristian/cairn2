"""Small, explicit repo, project and single-token configuration helpers."""

import os
import secrets
import socket
import tomllib
from pathlib import Path
from urllib.parse import quote, urlsplit

from .model import ValidationError


def _cwd(cwd):
    return Path(cwd or Path.cwd()).expanduser().resolve()


def _read_settings(path):
    try:
        with path.open("rb") as file:
            values = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValidationError(f"Cannot read configuration {path}: {error}") from error
    if unknown := set(values) - {"repo", "project", "token_file"}:
        raise ValidationError(f"Unknown settings in {path}: {', '.join(sorted(unknown))}")
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ValidationError(f"Settings in {path} must be nonempty strings")
    return values


def _settings(cwd):
    """Merge .cairn.toml files upward; the nearest file setting a key wins.

    The nearest level selecting a repo, by its repo setting or a .cairn/ folder,
    also supplies the token: its token_file, or .cairn/token for a local repo.
    """
    settings = {}
    current = _cwd(cwd)
    for parent in (current, *current.parents):
        folder, path = parent / ".cairn", parent / ".cairn.toml"
        if folder.is_file():
            raise ValidationError(f"{folder} must be a folder; move its settings to {path}")
        values = _read_settings(path) if path.is_file() else {}
        if "project" in values:
            settings.setdefault("project", values["project"])
        if "repo" not in settings and ("repo" in values or folder.is_dir()):
            repo = values.get("repo", str(folder / "repo"))
            settings["repo"] = (repo, parent)
            if "token_file" in values:
                settings["token_file"] = parent / Path(values["token_file"]).expanduser()
            elif urlsplit(repo).scheme not in ("http", "https") and (folder / "token").is_file():
                settings["token_file"] = folder / "token"
    return settings


def _token(value):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Authentication token must be a nonempty string")
    value = value.strip()
    if any(not 33 <= ord(character) <= 126 for character in value):
        raise ValidationError(
            "Authentication token must contain only printable non-whitespace ASCII"
        )
    return value


def _read_token(path):
    path = Path(path).expanduser()
    try:
        if path.stat().st_size > 16384:
            raise ValueError("token file is larger than 16 KiB")
        return _token(path.read_text())
    except (OSError, ValueError) as error:
        raise ValidationError(f"Cannot read token file {path}: {error}") from error


def resolve_token(explicit=None, *, cwd=None):
    """Discover a token without creating one or exposing its value in errors."""
    if explicit is not None:
        return _token(explicit)
    if "CAIRN_TOKEN" in os.environ:
        return _token(os.environ["CAIRN_TOKEN"])
    if "CAIRN_TOKEN_FILE" in os.environ:
        return _read_token(_cwd(cwd) / Path(os.environ["CAIRN_TOKEN_FILE"]).expanduser())
    if path := _settings(cwd).get("token_file"):
        return _read_token(path)
    path = Path.home() / ".config" / "cairn" / "token"
    return _read_token(path) if path.is_file() else None


def ensure_token(explicit=None, *, cwd=None):
    """Discover a token or atomically create a private project token file."""
    existing = resolve_token(explicit, cwd=cwd)
    if existing is not None:
        return existing
    directory = _cwd(cwd) / ".cairn"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "token"
    value = secrets.token_urlsafe(32)
    temporary = directory / f".token-{secrets.token_hex(8)}"
    try:
        with os.fdopen(
            os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        ) as file:
            file.write(value + "\n")
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return _read_token(path)
        return value
    finally:
        temporary.unlink(missing_ok=True)


def resolve_repo(explicit=None, *, cwd=None):
    """Resolve an explicit URL/path, CAIRN_REPO, .cairn.toml or .cairn/ folder."""
    value = explicit
    base = _cwd(cwd)
    if value is None:
        value = os.environ.get("CAIRN_REPO")
    if value is None:
        value, base = _settings(cwd).get("repo", (str(base / ".cairn" / "repo"), base))
    try:
        value = os.fspath(value)
    except TypeError as error:
        raise ValidationError("Repo must be a nonempty URL or path") from error
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Repo must be a nonempty URL or path")
    if urlsplit(value).scheme in ("http", "https"):
        return value.rstrip("/")
    return str((base / Path(value).expanduser()).resolve())


def resolve_project(explicit=None, *, cwd=None):
    """Resolve an explicit project, CAIRN_PROJECT, .cairn.toml, or "default"."""
    value = explicit
    if value is None:
        value = os.environ.get("CAIRN_PROJECT") or _settings(cwd).get("project", "default")
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Project must be a nonempty string")
    return value


def startup_urls(host, port, *, token=None):
    """Return reachable loopback and hostname URLs when listening on all interfaces."""
    hosts = ["localhost", socket.gethostname()] if host in ("0.0.0.0", "::") else [host]
    fragment = "#token=" + quote(_token(token), safe="") if token is not None else ""
    return list(
        dict.fromkeys(
            f"http://{'[' + item + ']' if ':' in item else item}:{port}/{fragment}"
            for item in hosts
        )
    )


def startup_url(host, port, *, token=None):
    return startup_urls(host, port, token=token)[-1]

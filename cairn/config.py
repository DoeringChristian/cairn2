"""Small, explicit repo and single-token configuration helpers."""

import json
import os
import secrets
import socket
from pathlib import Path
from urllib.parse import quote, urlsplit

from .model import ValidationError


def _cwd(cwd):
    return Path(cwd or Path.cwd()).expanduser().resolve()


def _projects(cwd):
    current = _cwd(cwd)
    for parent in (current, *current.parents):
        candidate = parent / ".cairn"
        if candidate.is_dir():
            yield candidate, None
        elif candidate.is_file():
            try:
                if candidate.stat().st_size > 65536:
                    raise ValueError("configuration is larger than 64 KiB")
                value = json.loads(candidate.read_text())
                if not isinstance(value, dict):
                    raise TypeError("configuration must be an object")
            except (OSError, TypeError, ValueError) as error:
                raise ValidationError(f"Cannot read configuration {candidate}: {error}") from error
            yield candidate, value


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
    for path, config in _projects(cwd):
        if config is None:
            if (path / "token").is_file():
                return _read_token(path / "token")
        elif "token" in config:
            return _token(config["token"])
        elif "token_file" in config:
            if not isinstance(config["token_file"], str):
                raise ValidationError(f"token_file in {path} must be a path string")
            return _read_token(path.parent / Path(config["token_file"]).expanduser())
    path = Path.home() / ".config" / "cairn" / "token"
    return _read_token(path) if path.is_file() else None


def ensure_token(explicit=None, *, cwd=None):
    """Discover a token or atomically create a private project token file."""
    existing = resolve_token(explicit, cwd=cwd)
    if existing is not None:
        return existing
    directory = _cwd(cwd) / ".cairn"
    if directory.is_file():
        raise ValidationError(f"Add token or token_file to {directory} before serving")
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
    """Resolve an explicit URL/path, environment setting, or project repository."""
    value = explicit
    base = _cwd(cwd)
    if value is None:
        value = os.environ.get("CAIRN_REPO")
    if value is None:
        for path, config in _projects(cwd):
            if config is None:
                return str(path / "repo")
            value = config.get("repo")
            if value is not None:
                base = path.parent
                break
    if value is None:
        return str(base / ".cairn" / "repo")
    try:
        value = os.fspath(value)
    except TypeError as error:
        raise ValidationError("Repo must be a nonempty URL or path") from error
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Repo must be a nonempty URL or path")
    if urlsplit(value).scheme in ("http", "https"):
        return value.rstrip("/")
    return str((base / Path(value).expanduser()).resolve())


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

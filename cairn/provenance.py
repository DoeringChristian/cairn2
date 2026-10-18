"""Best-effort client-side provenance; captures never read the process environment."""

from __future__ import annotations

import gzip
import importlib.metadata
import io
import json
import os
import platform
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

FILE_LIMIT = 4 * 1024 * 1024
SOURCE_LIMIT = 32 * 1024 * 1024
FILE_COUNT = 2000
LOCKFILES = {
    "pyproject.toml",
    "uv.lock",
    "pixi.toml",
    "pixi.lock",
    "poetry.lock",
    "pdm.lock",
    "Pipfile",
    "Pipfile.lock",
    "setup.py",
    "setup.cfg",
    "environment.yml",
    "environment.yaml",
    "conda-lock.yml",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.toml",
    "Cargo.lock",
}
SOURCE_SUFFIXES = {
    ".py",
    ".pyi",
    ".qmd",
    ".md",
    ".rst",
    ".ipynb",
    ".r",
    ".jl",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cu",
    ".rs",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".sh",
    ".bash",
    ".zsh",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".tex",
}
EXCLUDED_DIRS = {
    ".git",
    ".cairn",
    ".ssh",
    ".aws",
    ".azure",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pixi",
}


def _lockfile(name):
    return name in LOCKFILES or (name.startswith("requirements") and name.endswith(".txt"))


def _safe_name(name):
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return False
    for part in path.parts:
        lower = part.lower()
        if lower in EXCLUDED_DIRS or Path(lower).stem in {
            "token",
            "tokens",
            "credentials",
            "secret",
            "secrets",
            ".netrc",
            ".npmrc",
            ".pypirc",
        }:
            return False
        if lower.startswith((".env", "id_rsa", "id_ed25519", "id_dsa", "id_ecdsa")):
            return False
        if lower.endswith((".pem", ".key", ".p12", ".pfx")):
            return False
    return True


def _git(root, args, limit=FILE_LIMIT):
    """Bound memory and duration, including when git encounters pathological inputs."""
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            stdout=output,
            stderr=errors,
            stdin=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10
        try:
            while process.poll() is None:
                if time.monotonic() > deadline:
                    raise TimeoutError("git command exceeded 10 seconds")
                if (
                    os.fstat(output.fileno()).st_size > limit
                    or os.fstat(errors.fileno()).st_size > FILE_LIMIT
                ):
                    raise ValueError("git command output exceeds capture limit")
                time.sleep(0.01)
            if os.fstat(output.fileno()).st_size > limit:
                raise ValueError("git command output exceeds capture limit")
            if process.returncode:
                # Git stderr may contain user-controlled configuration or credentials.
                raise RuntimeError(f"git {args[0]} exited {process.returncode}")
            output.seek(0)
            return output.read(limit + 1)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def _read(root, name):
    """Open relative to root without following symlinks, including parent components."""
    parts = Path(name).parts
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in parts[:-1]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(source, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("not a regular file")
            if info.st_size > FILE_LIMIT:
                raise ValueError("file exceeds 4 MiB")
            data = stream.read(FILE_LIMIT + 1)
            if len(data) > FILE_LIMIT:
                raise ValueError("file exceeds 4 MiB")
            return data
    finally:
        os.close(descriptor)


def _archive(root, names, omissions):
    result = io.BytesIO()
    total = count = omitted = 0
    with (
        gzip.GzipFile(fileobj=result, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for name in sorted(set(names)):
            reason = None
            if not _safe_name(name):
                reason = "excluded sensitive or unsafe path"
            elif count >= FILE_COUNT:
                reason = "2000-file limit"
            else:
                try:
                    data = _read(root, name)
                    if total + len(data) > SOURCE_LIMIT:
                        reason = "32 MiB source limit"
                except (OSError, ValueError) as error:
                    reason = type(error).__name__ + (
                        f": {error}" if isinstance(error, ValueError) else " reading file"
                    )
            if reason:
                omitted += 1
                if len(omissions) < 100:
                    omissions.append({"path": name, "reason": reason})
                continue
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), 0o644
            archive.addfile(member, io.BytesIO(data))
            total += len(data)
            count += 1
    return result.getvalue(), {"files": count, "bytes": total, "omitted": omitted}


def _command():
    args = list(getattr(sys, "orig_argv", sys.argv))
    redact_next = False
    for index, value in enumerate(args):
        sensitive = any(
            word in value.lower().split("=", 1)[0]
            for word in ("password", "token", "secret", "api-key", "api_key")
        )
        if redact_next:
            args[index], redact_next = "<redacted>", False
        elif sensitive and value.startswith("-"):
            if "=" in value:
                args[index] = value.split("=", 1)[0] + "=<redacted>"
            else:
                redact_next = True
    return args


def collect(*, capture_source=True):
    """Return JSON metadata and named bytes; unavailable evidence is explicitly recorded.

    Source archives are bounded best-effort working-tree snapshots, not backups.
    Known credential paths are excluded; arbitrary secrets embedded in code cannot
    be detected. Set capture_source=False to disable source and diff collection.
    """
    captures, errors, omissions = {}, [], []
    provenance = {
        "format_version": 1,
        "source_enabled": capture_source,
        "errors": errors,
        "omissions": omissions,
        "git": None,
    }
    metadata = {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "executable": sys.executable,
            "command": {"argv": _command()},
        },
        "provenance": provenance,
    }
    try:
        root = cwd = Path.cwd()
        metadata["environment"]["cwd"] = str(cwd)
    except OSError:
        errors.append("working directory unavailable")
        provenance["complete"] = False
        return metadata, captures
    packages = []
    try:
        for package in importlib.metadata.distributions():
            try:
                packages.append(
                    {"name": package.metadata["Name"] or "unknown", "version": package.version}
                )
            except Exception:  # noqa: BLE001 - malformed third-party package metadata
                errors.append("installed package metadata unreadable")
        payload = json.dumps(
            sorted(packages, key=lambda item: (item["name"], item["version"])), indent=2
        ).encode()
        if len(payload) > FILE_LIMIT:
            raise ValueError("package inventory exceeds 4 MiB")
        captures["provenance/packages.json"] = payload
    except Exception as error:  # noqa: BLE001 - provenance must not prevent a run
        errors.append(f"package inventory unavailable: {type(error).__name__}")
    names = []
    try:
        root = Path(os.fsdecode(_git(cwd, ["rev-parse", "--show-toplevel"])).strip())
    except (OSError, RuntimeError, TimeoutError, ValueError) as error:
        provenance["scope"] = "entry-script-and-cwd-dependency-files"
        errors.append(f"git root unavailable: {type(error).__name__}")
        try:
            names = [p.name for p in cwd.iterdir() if _lockfile(p.name)]
            if sys.argv and sys.argv[0]:
                script = Path(os.path.abspath(sys.argv[0]))
                if script.is_relative_to(cwd):
                    names.append(str(script.relative_to(cwd)))
        except OSError:
            errors.append("fallback source enumeration failed")
    else:
        provenance["scope"] = "git-working-tree"
        git = provenance["git"] = {"root": str(root), "head": None, "branch": None}
        for key, args in [
            ("head", ["rev-parse", "--verify", "HEAD"]),
            ("branch", ["symbolic-ref", "--quiet", "--short", "HEAD"]),
        ]:
            try:
                git[key] = _git(root, args).decode().strip()
            except (OSError, RuntimeError, TimeoutError, ValueError):
                pass  # Unborn commits and detached heads are valid states.
        try:
            status = _git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=normal"])
            git["dirty"] = bool(status)
            tracked = _git(root, ["ls-files", "-z"], limit=8 * 1024 * 1024).split(b"\0")
            untracked = _git(
                root, ["ls-files", "--others", "--exclude-standard", "-z"], limit=8 * 1024 * 1024
            ).split(b"\0")
            names = [os.fsdecode(name) for name in tracked if name]
            names += [
                os.fsdecode(name)
                for name in untracked
                if name
                and (
                    _lockfile(Path(os.fsdecode(name)).name)
                    or Path(os.fsdecode(name)).suffix.lower() in SOURCE_SUFFIXES
                )
            ]
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            errors.append(f"git source enumeration failed: {type(error).__name__}")
        if capture_source:
            # Restrict diff to the same safe paths; never capture excluded credentials.
            safe = [name for name in names if _safe_name(name)]
            try:
                base = ["HEAD"] if git["head"] else ["--cached"]
                if safe:
                    captures["provenance/git.diff"] = _git(
                        root,
                        [
                            "--literal-pathspecs",
                            "diff",
                            "--binary",
                            "--no-ext-diff",
                            "--no-textconv",
                            *base,
                            "--",
                            *safe,
                        ],
                    )
                else:
                    captures["provenance/git.diff"] = b""
                if not git["head"]:
                    provenance["diff_scope"] = (
                        "staged-unborn-tree; source archive captures working files"
                    )
            except (OSError, RuntimeError, TimeoutError, ValueError) as error:
                errors.append(f"git diff unavailable: {type(error).__name__}")
    selected = names if capture_source else [name for name in names if _lockfile(Path(name).name)]
    try:
        payload, statistics = _archive(root, selected, omissions)
        provenance["source"] = statistics
        name = "provenance/source.tar.gz" if capture_source else "provenance/lockfiles.tar.gz"
        captures[name] = payload
    except (OSError, ValueError, UnicodeError) as error:
        errors.append(f"source archive unavailable: {type(error).__name__}")
    provenance["complete"] = not errors and not omissions
    return metadata, captures

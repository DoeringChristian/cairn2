"""Local, portable reports using MarkdownIt and a fresh Jupyter Python kernel.

This is a documented QMD subset, not a Quarto implementation: YAML front matter,
Markdown tables/footnotes, dollar math, static rich outputs, ``{python}`` cells,
``#|`` cell options, local includes, callouts, and fig/tbl/eq/sec cross references.
Declare inputs in ``cairn.inputs`` and additional local files in
``cairn.resources``. The supplied report reader exposes only declared artifact references, resolved
to frozen IDs. Independently opened clients are outside this reproducibility policy.
Python itself is trusted local code, not a security sandbox.
"""

from __future__ import annotations

import base64
import hashlib
import html
import importlib.metadata
import json
import mimetypes
import os
import platform
import re
import shutil
import sys
import tempfile
import traceback
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .model import ValidationError, canonical, now, safe_path


class ReportBuildError(ValidationError):
    """A build failed; its captured inputs and diagnostics remain available."""

    def __init__(self, message: str, directory: Path, *, source=None):
        super().__init__(message, details={"build_directory": str(directory), "source": source})
        self.directory = directory


@dataclass(frozen=True)
class BuildResult:
    directory: Path
    manifest: dict

    @property
    def path(self) -> Path:
        return self.directory


_INCLUDE = re.compile(r'^\s*\{\{<\s*include\s+(?:"([^"]+)"|\'([^\']+)\'|(\S+))\s*>\}\}\s*$')
_LABEL = re.compile(r"\s*\{#((?:fig|tbl|eq|sec)-[\w.-]+)\}\s*$")
_REFERENCE = re.compile(r"(?<![\w\\])@((?:fig|tbl|eq|sec)-[\w-]+(?:\.[\w-]+)*)")
_KINDS = {"fig": "Figure", "tbl": "Table", "eq": "Equation", "sec": "Section"}


def _dependencies():
    try:
        import nbformat
        import yaml
        from latex2mathml.converter import convert
        from markdown_it import MarkdownIt
        from mdit_py_plugins.container import container_plugin
        from mdit_py_plugins.dollarmath import dollarmath_plugin
        from mdit_py_plugins.footnote import footnote_plugin
        from nbclient import NotebookClient
    except ImportError as exc:
        raise ValidationError(
            "Reports require the 'reports' extra: pip install 'cairn2[reports]'"
        ) from exc
    return (
        nbformat,
        yaml,
        MarkdownIt,
        NotebookClient,
        container_plugin,
        dollarmath_plugin,
        footnote_plugin,
        convert,
    )


def _write_json(path: Path, value: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


def _file_info(path: Path, relative: str) -> dict:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return {
        "path": relative,
        "digest": digest.hexdigest(),
        "size": path.stat().st_size,
        "media_type": mimetypes.guess_type(relative)[0] or "application/octet-stream",
    }


def _inside(root: Path, name: str) -> Path:
    relative = safe_path(name)
    candidate = root / relative
    if candidate.is_symlink() or root.resolve() not in candidate.resolve().parents:
        raise ValidationError(f"Report path must stay inside its source directory: {name}")
    for parent in candidate.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValidationError(f"Report paths cannot traverse symbolic links: {name}")
    return candidate


def _frontmatter(text: str, yaml) -> tuple[dict, str, int]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text, 0
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            try:
                metadata = yaml.safe_load("".join(lines[1:index])) or {}
            except yaml.YAMLError as exc:
                raise ValidationError(f"Invalid report front matter: {exc}") from exc
            if not isinstance(metadata, dict):
                raise ValidationError("Report front matter must be a mapping")

            def encode_yaml(value):
                if isinstance(value, date):
                    return value.isoformat()
                raise ValidationError(f"Unsupported YAML value: {type(value).__name__}")

            metadata = json.loads(json.dumps(metadata, default=encode_yaml, allow_nan=False))
            return metadata, "".join(lines[index + 1 :]), index + 1
    raise ValidationError("Unclosed YAML front matter")


class _Sources:
    def __init__(self, source: Path, destination: Path):
        self.root = source.parent
        self.destination = destination
        self.captured: set[str] = set()

    def capture(self, relative: str) -> Path:
        relative = safe_path(relative)
        if relative in self.captured:
            return _inside(self.destination, relative)
        path = _inside(self.root, relative)
        if not path.is_file():
            raise ValidationError(f"Report source or resource is not a file: {relative}")
        target = _inside(self.destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        self.captured.add(relative)
        return target

    def resource(self, relative: str):
        path = _inside(self.root, relative)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_symlink():
                    raise ValidationError(f"Resources cannot contain symbolic links: {child}")
                if child.is_file():
                    self.capture(child.relative_to(self.root).as_posix())
        else:
            self.capture(relative)

    def expand(self, relative: str, *, body=None, offset=0, stack=()) -> tuple[str, list]:
        if relative in stack:
            raise ValidationError(f"Report include cycle: {' -> '.join((*stack, relative))}")
        target = self.capture(relative)
        text = target.read_text("utf-8") if body is None else body
        result, mapping = [], []
        fence = None
        for index, line in enumerate(text.splitlines(keepends=True), 1 + offset):
            marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
            if marker:
                if fence is None:
                    fence = marker.group(1)
                elif marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                    fence = None
            include = _INCLUDE.match(line) if fence is None else None
            if include:
                name = next(part for part in include.groups() if part is not None)
                included = (Path(relative).parent / safe_path(name)).as_posix()
                expanded, locations = self.expand(included, stack=(*stack, relative))
                result.append(expanded if expanded.endswith("\n") else expanded + "\n")
                mapping.extend(locations)
            else:
                result.append(line)
                mapping.append({"path": relative, "line": index})
        return "".join(result), mapping


def _source_range(mapping: list, start: int, end: int) -> dict:
    locations = mapping[start:end]
    if not locations:
        return {"path": "", "line_start": 1, "line_end": 1}
    first, last = locations[0], locations[-1]
    result = {"path": first["path"], "line_start": first["line"], "line_end": last["line"]}
    if first["path"] != last["path"]:
        result["segments"] = locations
    return result


def _cell_options(source: str, yaml) -> dict:
    options = []
    for line in source.splitlines():
        if line.startswith("#|"):
            options.append(line[2:].lstrip())
        elif line.strip() and not line.startswith("#"):
            break
    try:
        value = yaml.safe_load("\n".join(options)) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"Invalid Python cell options: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError("Python cell options must be a mapping")
    supported = {"label", "fig-cap", "tbl-cap", "echo", "eval", "output"}
    unknown = value.keys() - supported
    if unknown:
        raise ValidationError(f"Unsupported cell options: {', '.join(sorted(unknown))}")
    for key in ("echo", "eval", "output"):
        if key in value and not isinstance(value[key], bool):
            raise ValidationError(f"Cell option {key} must be true or false")
    if "label" in value and not re.fullmatch(r"[\w.-]+", str(value["label"])):
        raise ValidationError("Cell labels must contain only letters, digits, _, . and -")
    return value


def _pin_inputs(client, metadata: dict) -> list[dict]:
    settings = metadata.get("cairn", {})
    if not isinstance(settings, dict):
        raise ValidationError("cairn front matter must be a mapping")
    inputs = settings.get("inputs", {})
    if not isinstance(inputs, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in inputs.items()
    ):
        raise ValidationError("cairn.inputs must map input names to artifact references")
    if inputs and client is None:
        raise ValidationError("Declared report inputs require a Cairn Client")
    descriptors = (
        client.backend.resolve_many(list(inputs.values()), project=client.project) if inputs else []
    )
    pinned = []
    for (name, reference), descriptor in zip(inputs.items(), descriptors, strict=True):
        if descriptor.get("state") != "finished":
            raise ValidationError(
                f"Report input {name!r} is live; finish the artifact before building"
            )
        pinned.append(
            {
                "name": name,
                "requested": reference,
                "artifact_id": descriptor["id"],
                "descriptor": descriptor,
            }
        )
    return pinned


def _pin_report_inputs(client, metadata, source, sources):
    lock = None
    if (source.parent / "cairn-lock.json").exists():
        lock = json.loads(sources.capture("cairn-lock.json").read_text("utf-8"))
    elif source.parent.name == "sources" and (source.parent.parent / "manifest.json").is_file():
        bundle, _ = _validated_bundle(source.parent.parent)
        if bundle["source"] != "sources/" + source.name:
            raise ValidationError("Source does not match its enclosing report bundle")
        lock = {"format": "cairn/report-lock", "inputs": bundle.get("inputs", [])}
        _write_json(sources.destination / "cairn-lock.json", lock)
        sources.captured.add("cairn-lock.json")
    if lock is None:
        return _pin_inputs(client, metadata)
    if not isinstance(lock, dict) or lock.get("format") != "cairn/report-lock":
        raise ValidationError("Unsupported cairn-lock.json format")
    if not isinstance(lock.get("inputs"), list):
        raise ValidationError("Report locks must contain an input list")
    locked = {}
    for previous in lock["inputs"]:
        if not isinstance(previous, dict) or any(
            not isinstance(previous.get(key), str) for key in ("name", "requested", "artifact_id")
        ):
            raise ValidationError("Invalid report lock input")
        aliases = previous.get("aliases", [])
        if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
            raise ValidationError("Report lock aliases must be artifact reference strings")
        locked[previous["name"]] = previous
    declarations = metadata.get("cairn", {}).get("inputs", {})
    if not isinstance(declarations, dict):
        return _pin_inputs(client, metadata)
    effective = json.loads(json.dumps(metadata))
    references = effective.setdefault("cairn", {}).setdefault("inputs", {})
    active = {}
    for name, reference in declarations.items():
        previous = locked.get(name)
        if previous and reference in [
            previous["requested"],
            previous["artifact_id"],
            *previous.get("aliases", []),
        ]:
            references[name] = previous["artifact_id"]
            active[name] = previous
    pinned = _pin_inputs(client, effective)
    for pin in pinned:
        previous = active.get(pin["name"])
        if previous:
            if previous["artifact_id"] != pin["artifact_id"]:
                raise ValidationError("Report locks must contain exact immutable version IDs")
            pin["requested"] = declarations[pin["name"]]
            pin["aliases"] = sorted(
                {previous["requested"], *previous.get("aliases", [])} - {pin["requested"]}
            )
    return pinned


class _PinnedBackend:
    """Read operations scoped to one report's repository and declared inputs."""

    def __init__(self, backend, project, pins):
        self._backend, self._project = backend, project
        self._allowed = {}
        for pin in pins:
            for ref in (pin["requested"], pin["artifact_id"], *pin.get("aliases", [])):
                previous = self._allowed.setdefault(ref, pin["artifact_id"])
                if previous != pin["artifact_id"]:
                    raise ValidationError("Report input aliases identify different versions")

    def _reference(self, ref, project):
        if project != self._project:
            raise ValidationError("Report reader is scoped to its declared project")
        if ref not in self._allowed:
            raise ValidationError(
                "Report inputs must be declared in cairn.inputs; undeclared reference: " + str(ref)
            )
        return self._allowed[ref]

    def resolve(self, ref, *, project="default"):
        return self._backend.resolve(self._reference(ref, project), project=project)

    def resolve_many(self, refs, *, project="default"):
        return self._backend.resolve_many(
            [self._reference(ref, project) for ref in refs], project=project
        )

    def query(self, ref, *, project="default", **options):
        return self._backend.query(self._reference(ref, project), project=project, **options)

    def open_blob(self, digest):
        return self._backend.open_blob(digest)


class PinnedInputs:
    """A read-only report client; explicit clients remain ordinary independent clients.

    This is reproducibility policy for the supplied reader, not a Python sandbox.
    Blob reads support values returned by the pinned readers; raw storage access
    and arbitrary external I/O are outside the policy.
    """

    def __init__(self, client, pins):
        self.project = client.project
        self.backend = _PinnedBackend(client.backend, self.project, pins)

    def resolve(self, ref):
        from .sdk import Reader

        if isinstance(ref, Reader):
            ref = ref.id
        descriptor = self.backend.resolve(ref, project=self.project)
        if descriptor["state"] != "finished":
            raise ValidationError("Report inputs must be finished artifacts")
        return Reader(self, descriptor)


_BOOTSTRAP = """# Cairn supplies connection details transiently, outside this notebook.
import json as _json, os as _os
from cairn.sdk import Client as _Client
from cairn.reports import PinnedInputs as _PinnedInputs
_pins = _json.loads(_os.environ.pop("CAIRN_REPORT_PINS"))
_endpoint = _os.environ.pop("CAIRN_REPORT_ENDPOINT", "")
_token = _os.environ.pop("CAIRN_REPORT_TOKEN", "") or None
_project = _os.environ.pop("CAIRN_REPORT_PROJECT", "default")
_source_client = _Client(_endpoint, project=_project, token=_token) if _endpoint else None
client = _PinnedInputs(_source_client, _pins) if _source_client else None
inputs = {pin["name"]: client.resolve(pin["artifact_id"]) for pin in _pins}
del _token
import importlib.metadata as _metadata, platform as _platform
from IPython.display import display as _display
_display({"application/vnd.cairn.environment+json": {
    "python": _platform.python_version(), "platform": _platform.platform(),
    "packages": dict(sorted((d.metadata["Name"], d.version) for d in _metadata.distributions() if d.metadata["Name"]))
}}, raw=True)
"""


def _markdown_engine(deps):
    _, _, MarkdownIt, _, container_plugin, dollarmath_plugin, footnote_plugin, convert = deps
    md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
    md.use(footnote_plugin)

    def math_renderer(content, options):
        try:
            return convert(content, display="block" if options.get("display_mode") else "inline")
        except Exception as exc:
            raise ValidationError(f"Invalid math expression: {content}") from exc

    def math_label(name):
        text = md.options.get("cairn_labels", {}).get(name, {}).get("text", name)
        return (
            f'<a class="math-label" href="#{html.escape(name, quote=True)}">{html.escape(text)}</a>'
        )

    md.use(dollarmath_plugin, allow_labels=True, renderer=math_renderer, label_renderer=math_label)

    def validate(info, markup):
        return bool(re.match(r"\s*\{?\.?callout-(note|tip|warning|important|caution)\b", info))

    def callout(renderer, tokens, index, options, env):
        token = tokens[index]
        if token.nesting < 0:
            return "</aside>\n"
        kind = re.search(r"callout-(\w+)", token.info).group(1)
        title = re.search(r'title="([^"]*)"', token.info)
        return f'<aside class="callout {kind}"><p class="callout-title">{html.escape(title.group(1) if title else kind.title())}</p>\n'

    md.use(container_plugin, "callout", validate=validate, render=callout)
    return md


def _qmd_math_labels(text):
    lines, fence = [], None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker.group(1)
            elif marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
                fence = None
        if fence is None:
            line = re.sub(r"\$\$[ \t]+\{#(eq-[\w.-]+)\}[ \t]*(\n?)$", r"$$ (\1)\2", line)
        lines.append(line)
    return "".join(lines)


def _segments(text: str, mapping: list, md, yaml) -> list[dict]:
    lines = text.splitlines(keepends=True)
    segments, position = [], 0
    for token in md.parse(_qmd_math_labels(text)):
        if token.type != "fence" or not token.info.strip().startswith("{python"):
            continue
        if not re.fullmatch(r"\{python(?:\s+([\w.-]+))?\}", token.info.strip()):
            raise ValidationError(f"Unsupported Python fence header: {token.info}")
        start, end = token.map
        if start > position:
            segments.append(
                {
                    "kind": "markdown",
                    "text": "".join(lines[position:start]),
                    "offset": position,
                    "source": _source_range(mapping, position, start),
                }
            )
        options = _cell_options(token.content, yaml)
        header_label = re.fullmatch(r"\{python(?:\s+([\w.-]+))?\}", token.info.strip()).group(1)
        if header_label:
            options.setdefault("label", header_label)
        segments.append(
            {
                "kind": "code",
                "text": token.content,
                "options": options,
                "offset": start,
                "source": _source_range(mapping, start, end),
                "line_map": mapping[start + 1 : start + 1 + len(token.content.splitlines())],
            }
        )
        position = end
    if position < len(lines):
        segments.append(
            {
                "kind": "markdown",
                "text": "".join(lines[position:]),
                "offset": position,
                "source": _source_range(mapping, position, len(lines)),
            }
        )
    return segments


def _asset(directory: Path, data: bytes, suffix: str) -> str:
    path = "assets/" + hashlib.sha256(data).hexdigest() + suffix
    target = directory / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return path


def _native_output(output: dict, directory: Path) -> dict:
    """cairn/document v2 stores payload bytes once, outside the document tree."""
    result = dict(output)

    def reference(value, mime):
        binary = mime.startswith(("image/", "video/", "audio/")) and mime != "image/svg+xml"
        if binary:
            value = "".join(value) if isinstance(value, list) else value
            payload, encoding = base64.b64decode(value), "binary"
        elif mime.startswith("text/") or mime == "image/svg+xml":
            value = "".join(value) if isinstance(value, list) else str(value)
            payload, encoding = value.encode("utf-8"), "utf-8"
        else:
            payload, encoding = canonical(value), "json"
        suffix = {
            "image/svg+xml": ".svg",
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "video/mp4": ".mp4",
            "video/webm": ".webm",
            "audio/wav": ".wav",
            "audio/mpeg": ".mp3",
            "audio/ogg": ".ogg",
            "text/html": ".html",
            "text/markdown": ".md",
        }.get(mime, ".json" if encoding == "json" else ".txt")
        return {"asset": _asset(directory, payload, suffix), "mime": mime, "encoding": encoding}

    if "data" in output:
        result["data"] = {mime: reference(value, mime) for mime, value in output["data"].items()}
    if output.get("output_type") == "stream":
        result["text"] = reference(output.get("text", ""), "text/plain")
    return result


def _prepare_markdown(text, segments, md, sources, mapping, directory):
    from markdown_it.token import Token

    # Parse the full document once so definitions, references, and footnotes
    # keep their meaning across executable cells.
    env = {}
    tokens = md.parse(_qmd_math_labels(text), env)
    for segment in segments:
        if segment["kind"] == "markdown":
            segment["tokens"] = []
    current = None
    footer = False
    for token in tokens:
        if token.type == "footnote_block_open":
            footer = True
            if not segments or segments[-1]["kind"] != "markdown":
                segments.append(
                    {
                        "kind": "markdown",
                        "text": "",
                        "tokens": [],
                        "offset": len(mapping),
                        "source": _source_range(mapping, max(0, len(mapping) - 1), len(mapping)),
                    }
                )
            current = segments[-1]
        if token.map and not footer:
            current = next(
                (segment for segment in reversed(segments) if segment["offset"] <= token.map[0]),
                None,
            )
        if current is not None and current["kind"] == "markdown":
            current["tokens"].append(token)
    labels, counters = {}, dict.fromkeys(_KINDS, 0)

    def label(name, source):
        prefix = name.split("-", 1)[0]
        if prefix not in _KINDS:
            return
        if name in labels:
            raise ValidationError(f"Duplicate report label: {name}")
        counters[prefix] += 1
        labels[name] = {"text": f"{_KINDS[prefix]} {counters[prefix]}", "source": source}

    for segment in segments:
        if segment["kind"] == "code":
            if name := segment["options"].get("label"):
                label(name, segment["source"])
            continue
        tokens = segment["tokens"]
        for index, token in enumerate(tokens):
            source = (
                _source_range(mapping, token.map[0], token.map[1])
                if token.map
                else segment["source"]
            )
            if token.type == "math_block_label":
                if not re.fullmatch(r"eq-[\w.-]+", token.info):
                    raise ValidationError("Equation labels must use a safe eq-name identifier")
                label(token.info, source)
            if token.type != "inline":
                continue
            match = _LABEL.search(token.content)
            if match:
                name = match.group(1)
                label(name, source)
                if token.children and token.children[-1].type == "text":
                    token.children[-1].content = _LABEL.sub("", token.children[-1].content)
                if index and tokens[index - 1].type in {"heading_open", "paragraph_open"}:
                    tokens[index - 1].attrSet("id", name)
                if name.startswith("tbl-") and token.children and token.children[0].type == "text":
                    token.children[0].content = token.children[0].content.lstrip(": ")
                    token.children[0].content = (
                        labels[name]["text"] + ": " + token.children[0].content
                    )
                image_token = next(
                    (child for child in token.children or [] if child.type == "image"), None
                )
                if (
                    name.startswith("fig-")
                    and image_token
                    and index
                    and tokens[index - 1].type == "paragraph_open"
                ):
                    tokens[index - 1].tag = "figure"
                    if index + 1 < len(tokens) and tokens[index + 1].type == "paragraph_close":
                        tokens[index + 1].tag = "figure"
                    caption = Token("html_inline", "", 0)
                    caption.content = f"<figcaption>{labels[name]['text']}: {html.escape(image_token.content)}</figcaption>"
                    token.children.append(caption)
            for child in token.children or []:
                if child.type != "image":
                    continue
                raw = child.attrGet("src")
                parsed = urlparse(raw)
                if parsed.scheme == "data":
                    continue
                if parsed.scheme or parsed.netloc:
                    raise ValidationError("Portable reports require local images or data URLs")
                relative = (
                    Path(source["path"]).parent / safe_path(unquote(parsed.path))
                ).as_posix()
                captured = sources.capture(relative)
                child.attrSet(
                    "src", _asset(directory, captured.read_bytes(), captured.suffix.lower())
                )
                child.attrSet("loading", "lazy")
    for segment in segments:
        for token in segment.get("tokens", []):
            if not token.children:
                continue
            children = []
            for child in token.children:
                if child.type != "text":
                    children.append(child)
                    continue
                position = 0
                for match in _REFERENCE.finditer(child.content):
                    name = match.group(1)
                    if name not in labels:
                        raise ValidationError(f"Unknown report cross reference: @{name}")
                    before = Token("text", "", 0)
                    before.content = child.content[position : match.start()]
                    link = Token("html_inline", "", 0)
                    link.content = f'<a class="crossref" href="#{html.escape(name)}">{labels[name]["text"]}</a>'
                    children.extend([before, link])
                    position = match.end()
                after = Token("text", "", 0)
                after.content = child.content[position:]
                children.append(after)
            token.children = children
    md.options["cairn_labels"] = labels
    return labels, env


def _rich_output(output: dict, directory: Path, md) -> str:
    kind = output.get("output_type")
    if kind == "stream":
        return f'<pre class="output {html.escape(output.get("name", "stdout"))}">{html.escape(output["text"])}</pre>'
    if kind == "error":
        return f'<pre class="output error">{html.escape(output.get("ename", "Error") + ": " + output.get("evalue", ""))}</pre>'
    data = output.get("data", {})
    for media_type, suffix, tag in (
        ("video/mp4", ".mp4", "video"),
        ("video/webm", ".webm", "video"),
        ("audio/wav", ".wav", "audio"),
        ("audio/mpeg", ".mp3", "audio"),
        ("audio/ogg", ".ogg", "audio"),
    ):
        if media_type in data:
            content = base64.b64decode(data[media_type], validate=True)
            name = _asset(directory, content, suffix)
            return (
                f'<{tag} controls preload="metadata" src="{name}" style="max-width:100%"></{tag}>'
            )
    for media_type, suffix in (
        ("image/svg+xml", ".svg"),
        ("image/png", ".png"),
        ("image/jpeg", ".jpg"),
    ):
        if media_type in data:
            value = data[media_type]
            content = value.encode("utf-8") if suffix == ".svg" else base64.b64decode(value)
            name = _asset(directory, content, suffix)
            return f'<img class="output-image" src="{name}" alt="Python cell image output">'
    if "text/html" in data:
        # An opaque iframe permits static table/HTML output without making its
        # scripts or external URLs part of the report's execution environment.
        body = (
            '<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '
            "content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:\">"
            "<style>body{font:14px system-ui;margin:8px}table{border-collapse:collapse}td,th{padding:6px;border-bottom:1px solid #ddd}</style>"
            + data["text/html"]
        )
        name = _asset(directory, body.encode("utf-8"), ".html")
        return f'<iframe class="rich-html" sandbox="" src="{name}" title="Python HTML output"></iframe>'
    if "text/markdown" in data:
        return md.render(data["text/markdown"])
    if "application/json" in data:
        return (
            '<pre class="output">'
            + html.escape(json.dumps(data["application/json"], indent=2))
            + "</pre>"
        )
    return '<pre class="output">' + html.escape(data.get("text/plain", "")) + "</pre>"


_CSS = """body{font:17px/1.65 system-ui,sans-serif;color:#253139;margin:0;background:#fafaf8}main{max-width:900px;margin:4rem auto;padding:0 2rem 5rem}h1,h2,h3{line-height:1.2;color:#153b38}h1{font-size:2.7rem}h2{margin-top:2.5rem}a{color:#146e67}pre{overflow:auto;padding:1rem;background:#eef1ef;border-radius:6px;font:14px/1.5 ui-monospace,monospace}code{font-family:ui-monospace,monospace}table{border-collapse:collapse;width:100%;margin:1.5rem 0}th,td{padding:.6rem;text-align:left;border-bottom:1px solid #ccd4d0}th{background:#edf1ef}img{max-width:100%;height:auto}.output-image{display:block;margin:1rem auto}figure{margin:1.5rem 0}figcaption,.caption{color:#53645d;font-size:.9rem}.callout{padding:.6rem 1.2rem;border-left:4px solid #42877a;background:#eef6f2;margin:1.4rem 0}.callout-title{font-weight:700;margin:.2rem 0}.warning,.caution,.important{border-color:#b77b34;background:#fff6e8}.stderr,.error{border-left:3px solid #b84141}.rich-html{display:block;width:100%;min-height:280px;border:0}.math.block{overflow:auto}.provenance{color:#61736a;font-size:.85rem;border-top:1px solid #ccd4d0;padding-top:1rem;margin-top:3rem}.source-block{scroll-margin-top:2rem}details>summary{cursor:pointer}@media print{body{background:white}main{margin:0;max-width:none}pre{white-space:pre-wrap}.rich-html{min-height:350px}}"""


def _render(segments, notebook, labels, mapping, md, directory, title, env):
    blocks, rendered = [], []
    for number, segment in enumerate(segments, 1):
        source = segment["source"]
        attributes = f'data-source-path="{html.escape(source["path"], quote=True)}" data-line-start="{source["line_start"]}" data-line-end="{source["line_end"]}"'
        block = {"id": f"block-{number}", "kind": segment["kind"], "source": source}
        if segment["kind"] == "markdown":
            block["text"] = segment["text"]

            def native_node(token, source=source):
                # Keep resolved inline structure (assets, crossrefs and footnotes)
                # so native readers need not reconstruct it from source spelling.
                return {
                    "type": token.type,
                    "tag": token.tag,
                    "nesting": token.nesting,
                    "source": _source_range(mapping, token.map[0], token.map[1])
                    if token.map
                    else source,
                    "content": token.content,
                    "attributes": token.attrs,
                    "info": token.info,
                    "meta": token.meta,
                    "hidden": token.hidden,
                    "children": [native_node(child) for child in token.children or []],
                }

            block["nodes"] = [native_node(token) for token in segment["tokens"]]
            content = md.renderer.render(segment["tokens"], md.options, env)
        else:
            options = segment["options"]
            cell = notebook.cells[segment["cell_index"]]
            block.update(
                {
                    "code": segment["text"],
                    "line_map": segment["line_map"],
                    "options": options,
                    "execution_count": cell.execution_count,
                    "outputs": [_native_output(output, directory) for output in cell.outputs],
                }
            )
            content = ""
            if options.get("echo", True):
                content += (
                    '<pre class="code"><code class="language-python">'
                    + html.escape(segment["text"])
                    + "</code></pre>"
                )
            if options.get("output", True):
                content += "".join(_rich_output(output, directory, md) for output in cell.outputs)
            label = options.get("label")
            caption = options.get("fig-cap") or options.get("tbl-cap")
            if label:
                block["label"] = label
                content = f'<figure id="{html.escape(label, quote=True)}">{content}'
                if caption:
                    prefix = labels.get(label, {}).get("text", "")
                    content += f"<figcaption>{html.escape(prefix + ': ' if prefix else '')}{md.renderInline(str(caption))}</figcaption>"
                content += "</figure>"
            elif caption:
                content += f'<p class="caption">{md.renderInline(str(caption))}</p>'
        rendered.append(
            f'<section class="source-block" id="block-{number}" {attributes}>{content}</section>'
        )
        blocks.append(block)
    document = {
        "format": "cairn/document",
        "version": 2,
        "title": title,
        "blocks": blocks,
        "labels": labels,
    }
    _write_json(directory / "document.json", document)
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body><main>"
        f"<h1>{html.escape(title)}</h1>"
        + "\n".join(rendered)
        + '<p class="provenance">Built locally with Cairn. <a href="manifest.json">Build manifest</a> · '
        '<a href="document.json">Native document</a> · <a href="executed.ipynb">Executed notebook</a></p>'
        "</main></body></html>"
    )
    (directory / "index.html").write_text(page, "utf-8")


def _new_directory(output, source: Path) -> Path:
    if output is None:
        root = source.parent / ".cairn" / "builds"
        root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=source.stem + "-", dir=root))
    directory = Path(output).absolute()
    if directory.is_symlink():
        raise ValidationError("Report output cannot be a symbolic link")
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise ValidationError(f"Report output must be a new or empty directory: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def build(source, output=None, *, client=None, timeout=120, kernel_name="python3") -> BuildResult:
    """Capture, pin, execute and render a report locally, preserving failed builds.

    Supported execution options are ``echo`` and ``eval`` in front matter;
    cell options additionally support ``output``, ``label``, ``fig-cap`` and
    ``tbl-cap``. Includes use ``{{< include relative.qmd >}}`` on a line by itself.
    Equations use ``$$...$$ {#eq-name}`` or ``$$...$$ (eq-name)``; headings/images use ``{#sec-name}`` or
    ``{#fig-name}``. Static rich HTML is displayed in a restricted iframe.
    """
    if client is None:
        from .sdk import _default, _implicit_client

        if _default.get() is not None:
            with _implicit_client() as implicit:
                return build(
                    source, output, client=implicit, timeout=timeout, kernel_name=kernel_name
                )
    deps = _dependencies()
    nbformat, yaml, _, NotebookClient, *_ = deps
    source = Path(source).absolute()
    if source.is_symlink() or not source.is_file():
        raise ValidationError(f"Report source must be an existing regular file: {source}")
    if source.suffix.lower() not in {".qmd", ".md"}:
        raise ValidationError("Report source must be a .qmd or .md file")
    if not isinstance(timeout, int) or timeout <= 0:
        raise ValidationError("Report cell timeout must be a positive number of seconds")
    directory = _new_directory(output, source)
    manifest = {
        "format": "cairn/report",
        "schema_version": 1,
        "status": "building",
        "created_at": now(),
        "source": "sources/" + source.name,
        "html": "index.html",
        "document": "document.json",
        "document_version": 2,
        "notebook": "executed.ipynb",
        "input_policy": "declared-pinned-only",
    }
    notebook, executor, segments = None, None, []
    try:
        sources = _Sources(source, directory / "sources")
        captured_source = sources.capture(source.name)
        metadata, body, offset = _frontmatter(captured_source.read_text("utf-8"), yaml)
        title = str(metadata.get("title", source.stem.replace("-", " ").title()))
        manifest.update(title=title, metadata=metadata)
        text, mapping = sources.expand(source.name, body=body, offset=offset)
        settings = metadata.get("cairn", {})
        if not isinstance(settings, dict):
            raise ValidationError("cairn front matter must be a mapping")
        resources = settings.get("resources", [])
        if not isinstance(resources, list) or any(not isinstance(item, str) for item in resources):
            raise ValidationError("cairn.resources must be a list of relative paths")
        for resource in resources:
            sources.resource(resource)
        pins = _pin_report_inputs(client, metadata, source, sources)
        manifest["inputs"] = [{k: v for k, v in pin.items() if k != "descriptor"} for pin in pins]
        manifest["dependencies"] = [
            {"artifact_id": p["artifact_id"], "role": "report-input:" + p["name"]} for p in pins
        ]
        _write_json(directory / "inputs.json", pins)
        _write_json(directory / "source-map.json", mapping)
        md = _markdown_engine(deps)
        segments = _segments(text, mapping, md, yaml)
        labels, markdown_env = _prepare_markdown(text, segments, md, sources, mapping, directory)
        execution = metadata.get("execute", {})
        if not isinstance(execution, dict) or execution.keys() - {"echo", "eval"}:
            raise ValidationError("Supported front-matter execute options are echo and eval")
        if any(not isinstance(value, bool) for value in execution.values()):
            raise ValidationError("execute.echo and execute.eval must be true or false")
        notebook = nbformat.v4.new_notebook()
        notebook.metadata["kernelspec"] = {
            "name": kernel_name,
            "display_name": kernel_name,
            "language": "python",
        }
        notebook.cells.append(
            nbformat.v4.new_code_cell(
                _BOOTSTRAP, metadata={"tags": ["cairn-bootstrap", "remove-cell"]}
            )
        )
        for segment in segments:
            if segment["kind"] != "code":
                notebook.cells.append(
                    nbformat.v4.new_markdown_cell(
                        segment["text"], metadata={"cairn_source": segment["source"]}
                    )
                )
                continue
            options = {**execution, **segment["options"]}
            segment["options"] = options
            cell_metadata = {"cairn_source": segment["source"]}
            if not options.get("eval", True):
                cell_metadata["tags"] = ["skip-execution"]
            segment["cell_index"] = len(notebook.cells)
            notebook.cells.append(
                nbformat.v4.new_code_cell(segment["text"], metadata=cell_metadata)
            )
        nbformat.write(notebook, directory / "executed.ipynb")
        work = directory / "_work"
        shutil.copytree(directory / "sources", work)
        environment = os.environ.copy()
        package_root = str(Path(__file__).resolve().parent.parent)
        environment["PYTHONPATH"] = os.pathsep.join(
            filter(None, [package_root, environment.get("PYTHONPATH", "")])
        )
        environment["CAIRN_REPORT_PINS"] = json.dumps(manifest["inputs"])
        endpoint = str(client.repo) if client is not None else ""
        if endpoint and not endpoint.startswith(("http://", "https://")):
            endpoint = str(Path(endpoint).absolute())
        environment["CAIRN_REPORT_ENDPOINT"] = endpoint
        environment["CAIRN_REPORT_PROJECT"] = client.project if client is not None else "default"
        environment["CAIRN_REPORT_TOKEN"] = (client.token or "") if client is not None else ""
        manifest["environment"] = {
            "python": platform.python_version(),
            "kernel": kernel_name,
            "packages": {
                name: importlib.metadata.version(name)
                for name in (
                    "cairn2",
                    "nbclient",
                    "nbformat",
                    "markdown-it-py",
                    "mdit-py-plugins",
                    "latex2mathml",
                )
            },
        }
        _write_json(directory / "manifest.json", manifest)
        executor = NotebookClient(
            notebook,
            timeout=timeout,
            kernel_name=kernel_name,
            resources={"metadata": {"path": str(work)}},
        )
        manager = executor.create_kernel_manager()
        if kernel_name == "python3":
            manager.kernel_spec.argv = [
                sys.executable,
                "-m",
                "ipykernel_launcher",
                "-f",
                "{connection_file}",
            ]
        executor.execute(env=environment)
        for emitted in notebook.cells[0].outputs:
            captured = emitted.get("data", {}).get("application/vnd.cairn.environment+json")
            if captured:
                manifest["environment"].update(captured)
        nbformat.write(notebook, directory / "executed.ipynb")
        _render(segments, notebook, labels, mapping, md, directory, title, markdown_env)
        shutil.rmtree(work)
        manifest["status"] = "complete"
        manifest["files"] = [
            _file_info(path, path.relative_to(directory).as_posix())
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path != directory / "manifest.json"
        ]
        _write_json(directory / "manifest.json", manifest)
        return BuildResult(directory, manifest)
    except Exception as exc:
        if notebook is not None:
            nbformat.write(notebook, directory / "executed.ipynb")
        source_location = None
        summary = str(exc)
        if notebook is not None:
            for cell in notebook.cells:
                for emitted in cell.get("outputs", []):
                    if emitted.get("output_type") == "error":
                        summary = emitted.get("ename", "Error") + ": " + emitted.get("evalue", "")
        for segment in segments:
            if segment["kind"] == "code" and notebook is not None:
                cell = notebook.cells[segment.get("cell_index", 0)]
                if any(output.get("output_type") == "error" for output in cell.get("outputs", [])):
                    source_location = segment["source"]
                    break
        manifest.update(
            status="failed",
            error={"type": type(exc).__name__, "message": summary, "source": source_location},
        )
        _write_json(directory / "manifest.json", manifest)
        (directory / "diagnostics.txt").write_text(traceback.format_exc(), "utf-8")
        raise ReportBuildError(
            f"Report build failed: {summary}. Diagnostics: {directory}",
            directory,
            source=source_location,
        ) from exc


def _file_entries(value) -> list:
    if not isinstance(value, list):
        raise ValidationError("Report files must be a list")
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"path", "digest", "size", "media_type"}:
            raise ValidationError("Invalid report file descriptor")
        if (
            not isinstance(entry["digest"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", entry["digest"])
            or type(entry["size"]) is not int
            or entry["size"] < 0
            or not isinstance(entry["media_type"], str)
        ):
            raise ValidationError("Invalid report file digest, size or media type")
        if safe_path(entry["path"]) != entry["path"]:
            raise ValidationError("Report file paths must be canonical relative paths")
    return value


def _matches_file(path: Path, entry: dict) -> bool:
    if not path.is_file():
        return False
    actual = _file_info(path, entry["path"])
    return all(actual[key] == entry[key] for key in ("digest", "size"))


def _validated_bundle(directory: Path) -> tuple[dict, list]:
    try:
        manifest = json.loads(_inside(directory, "manifest.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError("Build directory does not contain a valid manifest.json") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != "cairn/report"
        or manifest.get("schema_version") != 1
    ):
        raise ValidationError("Unsupported report manifest")
    if manifest.get("status") != "complete":
        raise ValidationError("Only complete report builds can be published")
    source = safe_path(manifest.get("source", ""))
    if not source.startswith("sources/"):
        raise ValidationError("Report source must be inside the captured sources directory")
    files = _file_entries(manifest.get("files"))
    paths = set()
    for entry in files:
        relative = safe_path(entry["path"])
        if relative in paths or relative == "manifest.json":
            raise ValidationError(f"Duplicate or reserved report path: {relative}")
        paths.add(relative)
        path = _inside(directory, relative)
        if not _matches_file(path, entry):
            raise ValidationError(f"Report file changed since build: {relative}")
    required = {
        "index.html",
        "document.json",
        "executed.ipynb",
        "inputs.json",
        "source-map.json",
        source,
    }
    if not required.issubset(paths):
        raise ValidationError("Report bundle is missing required source or output files")
    if manifest.get("document_version", 1) == 2:
        document = json.loads((directory / "document.json").read_text("utf-8"))
        if document.get("format") != "cairn/document" or document.get("version") != 2:
            raise ValidationError("Report document version does not match its manifest")
        for block in document.get("blocks", []):
            for output in block.get("outputs", []):
                references = list(output.get("data", {}).items())
                if output.get("output_type") == "stream":
                    references.append(("text/plain", output.get("text")))
                for mime, reference in references:
                    if (
                        not isinstance(reference, dict)
                        or reference.get("mime") != mime
                        or reference.get("encoding") not in {"binary", "utf-8", "json"}
                    ):
                        raise ValidationError("Invalid report output asset reference")
                    asset = safe_path(reference.get("asset", ""))
                    if not asset.startswith("assets/") or asset not in paths:
                        raise ValidationError("Report output asset is absent from the bundle")
    return manifest, [*files, _file_info(directory / "manifest.json", "manifest.json")]


def publish(build_directory, *, client=None, name=None) -> dict:
    """Publish a verified complete bundle as an ordinary report artifact."""
    if client is None:
        from .sdk import _implicit_client

        with _implicit_client() as implicit:
            return publish(build_directory, client=implicit, name=name)
    directory = Path(build_directory).absolute()
    manifest, files = _validated_bundle(directory)
    name = name or Path(manifest["source"]).stem
    blobs = []
    for entry in files:
        uploaded = client.backend.put_blob(
            _inside(directory, entry["path"]), digest=entry["digest"], size=entry["size"]
        )
        blobs.append({**uploaded, "name": entry["path"], "media_type": entry["media_type"]})
    value = {
        "$cairn": 1,
        "format": "report/bundle",
        "version": 1,
        "data": {
            "manifest": manifest,
            "files": files,
            "document_version": manifest.get("document_version", 1),
        },
        "blobs": blobs,
        "refs": manifest.get("dependencies", []),
    }
    request = (
        "report-publish-"
        + hashlib.sha256(
            canonical({"manifest": manifest, "name": name, "project": client.project})
        ).hexdigest()
    )
    result = client.backend.start(client.project, "reports", name, request_id=request + "-start")
    artifact_id, writer = result["artifact"]["id"], result["writer"]["id"]
    client.backend.append_batch(
        artifact_id,
        writer,
        [
            {
                "sequence": 1,
                "kind": "metadata",
                "data": {"title": manifest["title"], "build_created_at": manifest["created_at"]},
            }
        ],
    )
    client.backend.close_writer(artifact_id, writer, 1)
    return client.backend.finish(artifact_id, value=value, request_id=request + "-finish")


def _report_descriptor(ref, client):
    descriptor = client.backend.resolve(ref, project=client.project)
    if descriptor.get("state") != "finished":
        raise ValidationError("Report export requires a published version")
    value = descriptor.get("manifest", {}).get("value")
    if (
        not isinstance(value, dict)
        or value.get("$cairn") != 1
        or value.get("format") != "report/bundle"
        or value.get("version") != 1
        or not isinstance(value.get("data"), dict)
    ):
        raise ValidationError("Artifact version does not contain a report bundle")
    blobs = {(blob["digest"], blob["size"]) for blob in value.get("blobs", [])}
    for entry in _file_entries(value["data"].get("files")):
        if (entry["digest"], entry["size"]) not in blobs:
            raise ValidationError("Report files must be listed as standard artifact blobs")
    return descriptor, value


def export(ref, destination, *, client) -> Path:
    """Export one exact report version and verify every downloaded file."""
    descriptor, value = _report_descriptor(ref, client)
    directory = _new_directory(destination, Path("report.qmd"))
    try:
        files = _file_entries(value["data"].get("files"))
        seen = set()
        for entry in files:
            relative = safe_path(entry["path"])
            if relative in seen:
                raise ValidationError(f"Duplicate report export path: {relative}")
            seen.add(relative)
            path = _inside(directory, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            with client.backend.open_blob(entry["digest"]) as stream, path.open("wb") as output:
                shutil.copyfileobj(stream, output, length=1024 * 1024)
            if not _matches_file(path, entry):
                raise ValidationError(
                    f"Downloaded report file failed integrity verification: {relative}"
                )
        manifest, _ = _validated_bundle(directory)
        if manifest != value["data"]["manifest"]:
            raise ValidationError("Published report manifest does not match the bundled manifest")
        _write_json(
            directory / "publication.json",
            {"artifact_id": descriptor["id"]},
        )
        return directory
    except Exception:
        shutil.rmtree(directory)
        raise


def copy(ref, destination, *, client) -> Path:
    """Copy captured report sources with all input references rewritten to exact IDs."""
    _, yaml, *_ = _dependencies()
    destination = Path(destination).absolute()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValidationError(f"Report copy destination must be new or empty: {destination}")
    with tempfile.TemporaryDirectory(prefix="cairn-report-copy-") as temporary:
        exported = export(ref, Path(temporary) / "bundle", client=client)
        manifest = json.loads((exported / "manifest.json").read_text("utf-8"))
        shutil.copytree(exported / "sources", destination, dirs_exist_ok=True)
        source = _inside(destination, manifest["source"].removeprefix("sources/"))
        metadata, body, _ = _frontmatter(source.read_text("utf-8"), yaml)
        metadata.setdefault("cairn", {})["inputs"] = {
            pin["name"]: pin["artifact_id"] for pin in manifest.get("inputs", [])
        }
        source.write_text(
            "---\n"
            + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True)
            + "---\n"
            + body,
            "utf-8",
        )
        _write_json(
            destination / "cairn-lock.json",
            {
                "format": "cairn/report-lock",
                "inputs": manifest.get("inputs", []),
                "publication": json.loads((exported / "publication.json").read_text("utf-8")),
            },
        )
    return source


def new(destination="NewReport.qmd", *, title="New report") -> Path:
    """Create a small executable report source without opening a browser."""
    path = Path(destination).absolute()
    if path.suffix.lower() not in {".qmd", ".md"}:
        path = path / "NewReport.qmd"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValidationError(f"Report source already exists: {path}")
    path.write_text(
        "---\ntitle: " + json.dumps(title) + "\ncairn:\n  inputs: {}\n  resources: []\n---\n\n"
        "## Results {#sec-results}\n\n"
        "Declare artifact references in `cairn.inputs`, then read them with\n"
        '`inputs["name"].read()` or `inputs["name"].records()`.\n\n'
        '```{python}\nprint("A report built locally with Cairn")\n```\n\n'
        "::: {.callout-note}\nInputs are pinned before any Python cell executes.\n:::\n",
        "utf-8",
    )
    return path

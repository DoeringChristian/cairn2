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
import itertools
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
from urllib.parse import quote, unquote, urlparse

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
_LABEL = re.compile(r"\s*\{#((?:fig|tbl|eq|sec)-[\w.-]+)(?:\s[^{}]*)?\}\s*$")
_ATTRIBUTES = re.compile(
    r"\{((?:\s*(?:[#.][^\s{}\"'=]+|[\w-]+=(?:\"[^\"]*\"|'[^']*'|[^\s{}\"']+)))+)\s*\}"
)
_ATTRIBUTE = re.compile(r"([#.])([^\s{}\"'=]+)|([\w-]+)=(?:\"([^\"]*)\"|'([^']*)'|([^\s{}\"']+))")
_INLINE = re.compile(r"\{python\}\s+(.+)", re.DOTALL)
_ALLOWED = {"id", "class", "width", "height", "title"}
_REFERENCE = re.compile(
    r"(?<![\w\\])@(?:([\w.-]+(?:/[\w.-]+)+(?:@[\w.-]+)?)#)?((?:fig|tbl|eq|sec)-[\w-]+(?:\.[\w-]+)*)"
)
_EMBED = re.compile(r"\{\{<\s*embed\s+([\w.-]+(?:/[\w.-]+)+(?:@[\w.-]+)?)#([\w.-]+)\s*>\}\}")
_KINDS = {"fig": "Figure", "tbl": "Table", "eq": "Equation", "sec": "Section"}


def _dependencies():
    try:
        import nbformat
        import yaml
        from nbclient import NotebookClient
    except ImportError as exc:
        raise ValidationError(
            "Reports require the 'reports' extra: pip install 'cairn2[reports]'"
        ) from exc
    return nbformat, yaml, NotebookClient, _markdown_dependencies()


def _markdown_dependencies():
    try:
        from latex2mathml.converter import convert
        from markdown_it import MarkdownIt
        from mdit_py_plugins.container import container_plugin
        from mdit_py_plugins.dollarmath import dollarmath_plugin
        from mdit_py_plugins.footnote import footnote_plugin
    except ImportError as exc:
        raise ValidationError(
            "Report Markdown requires the 'reports' extra: pip install 'cairn2[reports]'"
        ) from exc
    return MarkdownIt, container_plugin, dollarmath_plugin, footnote_plugin, convert


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
    supported = {
        "label",
        "fig-cap",
        "tbl-cap",
        "fig-subcap",
        "echo",
        "eval",
        "output",
        "layout-ncol",
        "column",
        "cap-location",
    }
    unknown = value.keys() - supported
    if unknown:
        raise ValidationError(f"Unsupported cell options: {', '.join(sorted(unknown))}")
    for key in ("echo", "eval", "output"):
        if key in value and not isinstance(value[key], bool):
            raise ValidationError(f"Cell option {key} must be true or false")
    if "layout-ncol" in value and (
        type(value["layout-ncol"]) is not int or value["layout-ncol"] < 1
    ):
        raise ValidationError("Cell option layout-ncol must be a positive integer")
    if "fig-subcap" in value and not isinstance(value["fig-subcap"], list):
        raise ValidationError("Cell option fig-subcap must be a list of captions")
    if value.get("column", "margin") not in _COLUMNS:
        raise ValidationError(f"Cell option column must be one of {', '.join(sorted(_COLUMNS))}")
    if value.get("cap-location", "bottom") not in {"top", "bottom", "margin"}:
        raise ValidationError("Cell option cap-location must be top, bottom or margin")
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


def _record_inputs(directory: Path, manifest: dict, pins: list, referenced: list) -> None:
    """Add the reports the text references and the artifacts cells read to the lineage."""
    record = directory / "inputs.jsonl"
    used = referenced + (
        [json.loads(line) for line in record.read_text("utf-8").splitlines()]
        if record.exists()
        else []
    )
    record.unlink(missing_ok=True)
    manifest["inputs"].extend(used)
    seen = {item["artifact_id"] for item in manifest["dependencies"]}
    for item in used:
        if item["artifact_id"] not in seen:
            seen.add(item["artifact_id"])
            manifest["dependencies"].append({k: item[k] for k in ("artifact_id", "role")})
    _write_json(directory / "inputs.json", [*pins, *used])


def _compose(metadata: dict, settings: dict, overrides: list, sources) -> dict | None:
    """Compose the front-matter config with Hydra, using cairn.config_path for config groups."""
    config = metadata.get("config")
    if config is None:
        if overrides:
            raise ValidationError("Config overrides require a config in the report front matter")
        return None
    if not isinstance(config, dict) or "hydra" in config:
        raise ValidationError("Report config must be a mapping without a hydra key")
    try:
        from hydra import compose, initialize, initialize_config_dir
        from hydra.core.config_store import ConfigStore
        from hydra.core.global_hydra import GlobalHydra
        from hydra.core.singleton import Singleton
        from hydra.errors import HydraException
        from omegaconf import OmegaConf
        from omegaconf.errors import OmegaConfBaseException
    except ImportError as exc:
        raise ValidationError(
            "Reports require the 'reports' extra: pip install 'cairn2[reports]'"
        ) from exc
    path = settings.get("config_path")
    if path is not None:
        if not isinstance(path, str):
            raise ValidationError("cairn.config_path must be a relative directory")
        sources.resource(path)
        folder = _inside(sources.destination, path)
        if not folder.is_dir():
            raise ValidationError(f"cairn.config_path is not a directory: {path}")
    state = Singleton.get_state()
    try:
        GlobalHydra.instance().clear()
        ConfigStore.instance().store(name="cairn-report", node=config)
        context = (
            initialize_config_dir(config_dir=str(folder), version_base=None)
            if path is not None
            else initialize(config_path=None, version_base=None)
        )
        with context:
            composed = compose("cairn-report", overrides=list(overrides))
        return OmegaConf.to_container(composed, resolve=True, throw_on_missing=True)
    except (HydraException, OmegaConfBaseException) as exc:
        raise ValidationError(f"Invalid report config: {exc}") from exc
    finally:
        Singleton.set_state(state)


def sweep(overrides) -> list[list[str]]:
    """Expand Hydra sweep overrides (a=1,2, range(), choice()) into one list per build."""
    from hydra.core.override_parser.overrides_parser import OverridesParser
    from hydra.errors import HydraException

    try:
        parsed = OverridesParser.create().parse_overrides(list(overrides))
    except HydraException as exc:
        raise ValidationError(f"Invalid config overrides: {exc}") from exc
    axes = [
        [f"{item.get_key_element()}={value}" for value in item.sweep_string_iterator()]
        if item.is_sweep_override()
        else [item.input_line]
        for item in parsed
    ]
    return [list(combination) for combination in itertools.product(*axes)]


_KEY = r"[\w](?:[\w:.#$%&+?<>~/-]*\w)?"
_CITATIONS = re.compile(r"\[([^\[\]]*@[^\[\]]*)\]|(?<![\w\\@\[])@(" + _KEY + ")")
_CITATION = re.compile(r"\s*(.*?)(-?)@(" + _KEY + r")(?:,\s*(.*?))?\s*", re.DOTALL)


class _Citations:
    """BibTeX entries cited as [@key], [@a; @b, p. 3], [-@key] or @key."""

    def __init__(self, metadata, sources):
        files = metadata.get("bibliography", [])
        files = [files] if isinstance(files, str) else files
        if not isinstance(files, list) or any(not isinstance(name, str) for name in files):
            raise ValidationError("bibliography must be a BibTeX file or a list of them")
        self.numeric = metadata.get("citation-style", "author-year") == "numeric"
        if metadata.get("citation-style", "author-year") not in {"author-year", "numeric"}:
            raise ValidationError("citation-style must be author-year or numeric")
        self.entries, self.cited = {}, []
        if files:
            import bibtexparser
            from bibtexparser import middlewares

            for name in files:
                library = bibtexparser.parse_string(
                    sources.capture(name).read_text("utf-8"),
                    append_middleware=[
                        middlewares.LatexDecodingMiddleware(),
                        middlewares.SeparateCoAuthors(),
                        middlewares.SplitNameParts(),
                    ],
                )
                if library.failed_blocks:
                    raise ValidationError(f"Cannot parse {name}: {library.failed_blocks[0].error}")
                self.entries.update({entry.key: entry for entry in library.entries})

    def _field(self, key, name):
        field = self.entries[key].fields_dict.get(name)
        return field.value if field else ""

    def _year(self, key):
        return str(self._field(key, "year") or str(self._field(key, "date"))[:4] or "n.d.")

    def _short(self, key):
        names = [
            " ".join(n.von + n.last)
            for n in self._field(key, "author") or self._field(key, "editor") or []
        ]
        if not names:
            return str(self._field(key, "title"))
        return (
            names[0]
            if len(names) == 1
            else f"{names[0]} and {names[1]}"
            if len(names) == 2
            else f"{names[0]} et al."
        )

    def _link(self, key, text):
        if key not in self.cited:
            self.cited.append(key)
        return f'<a class="citation" href="#ref-{html.escape(key, quote=True)}">{html.escape(text)}</a>'

    def render(self, match):
        """HTML for a citation match, or None to leave the text as written."""
        if match.group(2):
            key = match.group(2)
            if key not in self.entries:
                return None
            label = str(self.cited.index(key) + 1 if key in self.cited else len(self.cited) + 1)
            if self.numeric:
                return f"{html.escape(self._short(key))} [{self._link(key, label)}]"
            return f"{html.escape(self._short(key))} ({self._link(key, self._year(key))})"
        items = [_CITATION.fullmatch(part) for part in match.group(1).split(";")]
        if not all(items) or any(item.group(3) not in self.entries for item in items):
            return None
        parts = []
        for item in items:
            prefix, suppress, key, locator = item.groups()
            number = self.cited.index(key) + 1 if key in self.cited else len(self.cited) + 1
            text = (
                str(number)
                if self.numeric
                else self._year(key)
                if suppress
                else f"{self._short(key)} {self._year(key)}"
            )
            parts.append(
                (html.escape(prefix.strip()) + " " if prefix.strip() else "")
                + self._link(key, text)
                + (", " + html.escape(locator) if locator else "")
            )
        return f"[{', '.join(parts)}]" if self.numeric else f"({'; '.join(parts)})"

    def bibliography(self):
        """Formatted entries of every cited key, numbered in citation order or by author."""
        keys = (
            self.cited
            if self.numeric
            else sorted(self.cited, key=lambda k: (self._short(k).lower(), self._year(k)))
        )
        for number, key in enumerate(keys, 1):
            names = [
                " ".join(n.von + n.last)
                + (", " + " ".join(p[0] + "." for p in n.first) if n.first else "")
                for n in self._field(key, "author") or self._field(key, "editor") or []
            ]
            authors = (
                names[0]
                if len(names) == 1
                else ", ".join(names[:-1]) + ", & " + names[-1]
                if names
                else ""
            )
            title = html.escape(str(self._field(key, "title")))
            container = self._field(key, "journal") or self._field(key, "booktitle")
            details = ", ".join(
                str(part)
                for part in (
                    self._field(key, "volume")
                    + (f"({self._field(key, 'number')})" if self._field(key, "number") else ""),
                    self._field(key, "pages"),
                )
                if part
            )
            text = (f"[{number}] " if self.numeric else "") + (
                html.escape(authors) + " " if authors else ""
            )
            text += f"({html.escape(self._year(key))}). "
            text += (
                f"{title}. <em>{html.escape(str(container))}</em>"
                if container
                else f"<em>{title}</em>"
            )
            text += (", " + html.escape(details) if details else "") + "."
            if publisher := self._field(key, "publisher"):
                text += f" {html.escape(str(publisher))}."
            doi, url = self._field(key, "doi"), self._field(key, "url")
            link = f"https://doi.org/{doi}" if doi else url
            if str(link).startswith(("https://", "http://")):
                text += (
                    f' <a href="{html.escape(str(link), quote=True)}">{html.escape(str(link))}</a>'
                )
            yield key, text


class _References:
    """Other reports referenced or embedded by the text, resolved once per build."""

    def __init__(self, client):
        self.client, self.reports, self.used = client, {}, []

    def get(self, ref, role):
        if self.client is None:
            raise ValidationError("Cross-document references require a Cairn repository")
        if ref not in self.reports:
            descriptor = self.client.backend.resolve(ref, project=self.client.project)
            if descriptor.get("state") != "finished":
                raise ValidationError(f"Referenced report {ref!r} is live; finish it first")
            _, value = _report_descriptor(descriptor["id"], self.client)
            blobs = {blob["name"]: blob["digest"] for blob in value["blobs"]}
            with self.client.backend.open_blob(blobs["document.json"]) as stream:
                labels = json.load(stream).get("labels", {})
            project = quote(self.client.project, safe="")
            self.reports[ref] = {
                "id": descriptor["id"],
                "title": value["data"]["manifest"]["title"],
                "labels": labels,
                "blobs": blobs,
                "url": f"/p/{project}/a/{descriptor['id']}",
            }
            self.used.append({"requested": ref, "artifact_id": descriptor["id"], "role": role})
        return self.reports[ref]

    def label(self, ref, name, role="reference"):
        report = self.get(ref, role)
        if name not in report["labels"]:
            raise ValidationError(f"Unknown cross reference: @{ref}#{name}")
        return report, report["labels"][name]["text"]

    def cell(self, ref, name, yaml):
        """The executed cell labelled name in another report, to show its outputs here."""
        import nbformat

        report, text = self.label(ref, name, "embed")
        with self.client.backend.open_blob(report["blobs"]["executed.ipynb"]) as stream:
            notebook = nbformat.reads(stream.read().decode("utf-8"), as_version=4)
        for cell in notebook.cells:
            if cell.cell_type != "code" or "cairn-bootstrap" in cell.metadata.get("tags", []):
                continue
            options = _cell_options(cell.source, yaml)
            if cell.metadata.get("cairn_label", options.get("label")) == name:
                caption = {k: options[k] for k in ("fig-cap", "tbl-cap") if k in options}
                return {
                    "cell": cell,
                    "text": cell.source,
                    "line_map": [],
                    "options": {"echo": False, "output": True, **caption},
                    "caption_prefix": f"{text} in {report['title']}:",
                }
        raise ValidationError(f"Cannot embed @{ref}#{name}: it is not a Python cell")


class ReportInputs:
    """What one report build reads: references resolve once, found and used artifacts are recorded.

    Each reference resolves once per build, so @latest gives one version
    throughout. Explicit clients remain ordinary independent clients. This is
    reproducibility policy for cairn.find and cairn.use, not a Python sandbox.
    """

    def __init__(self, client, pins, record=None):
        self.client, self.project, self._record = client, client.project, record
        self._resolved = {}
        for pin in pins:
            self._resolved[pin["requested"]] = self._resolved[pin["artifact_id"]] = pin[
                "artifact_id"
            ]
        self._recorded = {pin["artifact_id"] for pin in pins}
        self._descriptors = {}

    def _depend(self, artifact_id, role, requested):
        if artifact_id in self._recorded:
            return
        self._recorded.add(artifact_id)
        if self._record:
            with open(self._record, "a", encoding="utf-8") as file:
                entry = {"requested": requested, "artifact_id": artifact_id, "role": role}
                file.write(json.dumps(entry) + "\n")

    def find(self, pattern, *, where=None, order=None, limit=None):
        return self.client._find(pattern, where=where, order=order, limit=limit, owner=self)

    def use(self, ref, *, role="input"):
        reader = self._reader(ref)
        self._depend(reader.id, role, ref if isinstance(ref, str) else reader.id)
        return reader

    def _reader(self, ref):
        from .sdk import Reader, _identifier

        ref = _identifier(ref)
        descriptor = self._descriptors.get(self._resolved.get(ref))
        if descriptor is None:
            descriptor = self.client.backend.resolve(
                self._resolved.get(ref, ref), project=self.project
            )
        if descriptor["state"] != "finished":
            raise ValidationError(
                f"Report input {ref!r} is live; finish the artifact before building"
            )
        self._resolved[ref] = self._resolved[descriptor["id"]] = descriptor["id"]
        self._descriptors[descriptor["id"]] = descriptor
        return Reader(self.client, descriptor)


_BOOTSTRAP = """# Cairn supplies connection details transiently, outside this notebook.
import json as _json, os as _os
import cairn.sdk as _sdk
from cairn.sdk import Client as _Client
from cairn.reports import ReportInputs as _ReportInputs
_pins = _json.loads(_os.environ.pop("CAIRN_REPORT_PINS"))
_endpoint = _os.environ.pop("CAIRN_REPORT_ENDPOINT", "")
_token = _os.environ.pop("CAIRN_REPORT_TOKEN", "") or None
_project = _os.environ.pop("CAIRN_REPORT_PROJECT", "default")
_config = _os.environ.pop("CAIRN_REPORT_CONFIG", "")
_record = _os.environ.pop("CAIRN_REPORT_RECORD")
_source_client = _Client(_endpoint, project=_project, token=_token) if _endpoint else None
_sdk._report = _ReportInputs(_source_client, _pins, _record) if _source_client else None
inputs = {pin["name"]: _sdk._report._reader(pin["artifact_id"]) for pin in _pins}
if _endpoint:
    _os.environ.update(CAIRN_REPO=_endpoint, CAIRN_PROJECT=_project)
if _config:
    from omegaconf import OmegaConf as _OmegaConf
    cfg = _OmegaConf.create(_json.loads(_config))
del _token
import importlib.metadata as _metadata, platform as _platform
from IPython.display import display as _display
_display({"application/vnd.cairn.environment+json": {
    "python": _platform.python_version(), "platform": _platform.platform(),
    "packages": dict(sorted((d.metadata["Name"], d.version) for d in _metadata.distributions() if d.metadata["Name"]))
}}, raw=True)
"""


def _markdown_engine(deps):
    MarkdownIt, container_plugin, dollarmath_plugin, footnote_plugin, convert = deps
    from mdit_py_plugins.attrs import attrs_plugin
    from mdit_py_plugins.deflist import deflist_plugin
    from mdit_py_plugins.subscript import sub_plugin
    from mdit_py_plugins.superscript import superscript_plugin
    from mdit_py_plugins.tasklists import tasklists_plugin

    md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
    md.use(footnote_plugin).use(attrs_plugin, spans=True).use(deflist_plugin)
    md.use(tasklists_plugin).use(sub_plugin).use(superscript_plugin)

    def attributes(state, silent):
        """Pandoc attributes after images and code, e.g. {#fig-a width=50% .wide}."""
        last = state.tokens[-1] if state.tokens else None
        if state.src[state.pos] != "{" or last is None or last.type not in {"image", "code_inline"}:
            return False
        match = _ATTRIBUTES.match(state.src, state.pos)
        if not match:
            return False
        if not silent:
            for item in _ATTRIBUTE.finditer(match.group(1)):
                if item.group(1) == "#":
                    last.attrSet("id", item.group(2))
                elif item.group(1) == ".":
                    last.attrJoin("class", item.group(2))
                else:
                    last.attrSet(
                        item.group(3), next(v for v in item.group(4, 5, 6) if v is not None)
                    )
        state.pos = match.end()
        return True

    def allowed(state):
        for token in state.tokens:
            for child in token.children or []:
                if child.type in {"image", "code_inline", "span_open"}:
                    keep = _ALLOWED | ({"src", "alt"} if child.type == "image" else set())
                    child.attrs = {k: v for k, v in child.attrs.items() if k in keep}

    def image(self, tokens, index, options, env):
        """An HTML file in image syntax is an interactive viewer in a sandboxed frame."""
        token = tokens[index]
        if not token.attrGet("src").endswith(".html"):
            return self.image(tokens, index, options, env)
        height = token.attrGet("height") or "480"
        height = height + "px" if height.isdigit() else height
        style = f' style="height:{height}"' if _LENGTH.fullmatch(height) else ""
        return (
            f'<iframe class="viewer" sandbox="allow-scripts allow-same-origin" '
            f'src="{html.escape(token.attrGet("src"), quote=True)}" '
            f'title="{html.escape(token.content, quote=True)}"{style}></iframe>'
        )

    def comment_block(state, start, end, silent):
        """HTML comments are notes for authors and never render; other raw HTML stays text."""
        begin = state.bMarks[start] + state.tShift[start]
        if state.sCount[start] - state.blkIndent >= 4 or not state.src.startswith("<!--", begin):
            return False
        if not silent:
            close, line = state.src.find("-->", begin + 4), start
            while line + 1 < end and (close < 0 or state.eMarks[line] < close):
                line += 1
            state.line = line + 1
        return True

    def comment_inline(state, silent):
        close = state.src.find("-->", state.pos + 4)
        if not state.src.startswith("<!--", state.pos) or close < 0:
            return False
        state.pos = close + 3
        return True

    md.add_render_rule("image", image)
    md.block.ruler.before(
        "html_block",
        "cairn_comment",
        comment_block,
        {"alt": ["paragraph", "reference", "blockquote"]},
    )
    md.inline.ruler.before("html_inline", "cairn_comment", comment_inline)
    md.inline.ruler.before("attr", "cairn_attributes", attributes)
    md.core.ruler.push("cairn_attributes", allowed)

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

    def div(renderer, tokens, index, options, env):
        if tokens[index].nesting < 0:
            return "</div>\n"
        attributes = _div_attributes(tokens[index].info)
        names = html.escape(" ".join(attributes["class"]), quote=True)
        return f'<div class="{names}">\n'

    md.use(container_plugin, "callout", validate=validate, render=callout)
    md.use(
        container_plugin,
        "div",
        validate=lambda info, markup: bool(re.match(r"\s*\{", info)),
        render=div,
    )
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


def _div_attributes(info: str) -> dict:
    """Attributes of a fenced div: {#id .class key=value}, or a bare class name."""
    info = info.strip()
    if not info.startswith("{"):
        return {"class": info.split()[:1]}
    attributes = {"class": []}
    for item in _ATTRIBUTE.finditer(info.strip("{} ")):
        if item.group(1) == "#":
            attributes["id"] = item.group(2)
        elif item.group(1) == ".":
            attributes["class"].append(item.group(2))
        else:
            attributes[item.group(3)] = next(v for v in item.group(4, 5, 6) if v is not None)
    return attributes


def _splits(attributes: dict) -> bool:
    """Figure and layout divs hold one child per block, so each can be placed."""
    return attributes.get("id", "").startswith(("fig-", "tbl-")) or any(
        key in attributes for key in ("layout", "layout-ncol", "layout-nrow")
    )


def _grid(attributes: dict, count: int) -> list[list[float]]:
    """Rows of cell widths in percent from layout, layout-ncol or layout-nrow."""
    try:
        if "layout" in attributes:
            rows = json.loads(attributes["layout"])
        elif "layout-nrow" in attributes:
            rows = [[1] * -(-count // int(attributes["layout-nrow"]))]
        else:
            rows = [[1] * int(attributes["layout-ncol"])]
        widths = [[100 * abs(w) / sum(abs(v) for v in row) for w in row] for row in rows]
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        raise ValidationError(f"Invalid figure layout: {exc}") from exc
    if not widths or not all(widths):
        raise ValidationError("Figure layouts need at least one cell per row")
    grid = []
    while sum(map(len, grid)) < count:
        grid.append([round(w, 4) for w in widths[len(grid) % len(widths)]])
    return grid


def _segments(text: str, mapping: list, md, yaml) -> list[dict]:
    """Split the source into Markdown, Python cells and the fenced divs around them."""
    lines = text.splitlines(keepends=True)
    segments, position, stack = [], 0, []

    def markdown(end):
        if "".join(lines[position:end]).strip():
            segments.append(
                {
                    "kind": "markdown",
                    "text": "".join(lines[position:end]),
                    "offset": position,
                    "source": _source_range(mapping, position, end),
                }
            )
        return max(position, end)

    def marker(kind, line, **fields):
        segments.append(
            {
                "kind": kind,
                "offset": line,
                "source": _source_range(mapping, line, line + 1),
                **fields,
            }
        )

    tokens = md.parse(_qmd_math_labels(text))
    for index, token in enumerate(tokens):
        parent = stack[-1] if stack else None
        if token.type.startswith("container_") and token.level == len(stack) - (token.nesting < 0):
            if token.nesting == 1:
                start, end = token.map
                position = markdown(start)
                attributes = _div_attributes(token.info)
                marker("open", start, attributes=attributes)
                position = start + 1
                stack.append({"end": end, "attributes": attributes, "tab": None})
                continue
            closed = parent["end"] < len(lines) and re.fullmatch(
                r"\s*:{3,}\s*", lines[parent["end"]]
            )
            position = markdown(parent["end"])
            if parent["tab"]:
                marker("close", parent["end"])
            marker("close", parent["end"])
            position = parent["end"] + (1 if closed else 0)
            stack.pop()
            continue
        if parent and token.level == len(stack) and token.map and token.nesting >= 0:
            if "panel-tabset" in parent["attributes"]["class"] and token.type == "heading_open":
                parent["tab"] = parent["tab"] or token.tag
                if token.tag == parent["tab"]:
                    start, end = token.map
                    position = markdown(start)
                    if any(segment.get("tab") is parent for segment in segments):
                        marker("close", start)
                    title = re.sub(
                        r"^\s*#+\s*|\s*\{[^{}]*\}\s*$|\s*#+\s*$", "", lines[start].strip()
                    )
                    marker("open", start, attributes={"class": ["tab"], "title": title}, tab=parent)
                    position = end
                    continue
            elif _splits(parent["attributes"]):
                position = markdown(token.map[0])
        if token.type == "paragraph_open" and token.level == len(stack):
            embed = _EMBED.fullmatch(tokens[index + 1].content.strip())
            if embed:
                start, end = token.map
                position = markdown(start)
                segments.append(
                    {
                        "kind": "embed",
                        "reference": embed.group(1),
                        "label": embed.group(2),
                        "offset": start,
                        "source": _source_range(mapping, start, end),
                    }
                )
                position = end
                continue
        if token.type != "fence" or not token.info.strip().startswith("{python"):
            continue
        if not re.fullmatch(r"\{python(?:\s+([\w.-]+))?\}", token.info.strip()):
            raise ValidationError(f"Unsupported Python fence header: {token.info}")
        start, end = token.map
        position = markdown(start)
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
    position = markdown(len(lines))
    for segment in segments:
        segment.pop("tab", None)
    return segments


def _asset(directory: Path, data: bytes, suffix: str) -> str:
    path = "assets/" + hashlib.sha256(data).hexdigest() + suffix
    target = directory / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return path


def _markdown_tokens(output, md, labels, references=None):
    """Parsed Markdown output of a cell, or None; parsed once for every reader."""
    value = output.get("data", {}).get("text/markdown")
    if value is None:
        return None
    text = "".join(value) if isinstance(value, list) else str(value)
    return _resolved(md.parse(text, {}), labels, references)


def _native_output(output: dict, directory: Path, nodes=None) -> dict:
    """cairn/document v2 stores payload bytes once, outside the document tree.

    From v4 Markdown output also carries its parsed nodes."""
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
        if nodes is not None:
            result["data"]["text/markdown"]["nodes"] = nodes
    if output.get("output_type") == "stream":
        result["text"] = reference(output.get("text", ""), "text/plain")
    return result


def _crossrefs(content, labels, references=None):
    """Text and links for @label and @namespace/name#label references in content.

    Without references (old documents) unresolvable references stay text."""
    from markdown_it.token import Token

    result, position = [], 0
    for match in _REFERENCE.finditer(content):
        reference, name = match.groups()
        if references is None and (reference or name not in labels):
            continue
        before = Token("text", "", 0)
        before.content = content[position : match.start()]
        link = Token("html_inline", "", 0)
        if reference:
            report, text = references.label(reference, name)
            link.content = (
                f'<a class="crossref" href="{html.escape(report["url"], quote=True)}#'
                f'{html.escape(name)}">{html.escape(text)} in '
                f"<em>{html.escape(report['title'])}</em></a>"
            )
        elif name not in labels:
            raise ValidationError(f"Unknown report cross reference: @{name}")
        else:
            link.content = (
                f'<a class="crossref" href="#{html.escape(name)}">{labels[name]["text"]}</a>'
            )
        result.extend([before, link])
        position = match.end()
    after = Token("text", "", 0)
    after.content = content[position:]
    return [*result, after]


def _resolved(tokens, labels, references=None):
    """Tokens with cross references in their text resolved, as in report text."""
    for token in tokens:
        if token.children:
            token.children = [
                part
                for child in token.children
                for part in (
                    _crossrefs(child.content, labels, references)
                    if child.type == "text"
                    else [child]
                )
            ]
    return tokens


def _native_node(token, source, mapping=None):
    """A token as stored in a native document, with its source range.

    Fenced divs carry their parsed attributes, so readers never parse Markdown."""
    attributes = token.attrs
    if token.type.startswith("container_") and token.nesting == 1:
        attributes = {
            key: " ".join(value) if key == "class" else value
            for key, value in _div_attributes(token.info).items()
        }
    return {
        "type": token.type,
        "tag": token.tag,
        "nesting": token.nesting,
        "source": _source_range(mapping, token.map[0], token.map[1])
        if token.map and mapping is not None
        else source,
        "content": token.content,
        "attributes": attributes,
        "info": token.info,
        "meta": token.meta,
        "hidden": token.hidden,
        "children": [_native_node(child, source, mapping) for child in token.children or []],
    }


def _prepare_markdown(text, segments, md, sources, mapping, directory, references, citations):
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
    depth = 0
    for token in tokens:
        if token.type.startswith("container_") and token.level == depth - (token.nesting < 0):
            depth += token.nesting
            continue
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
    labels, counters, letters, stack = {}, dict.fromkeys(_KINDS, 0), {}, []

    def parent(name):
        kind = name.split("-", 1)[0] + "-"
        return next(
            (
                o["attributes"]["id"]
                for o in reversed(stack)
                if o["attributes"].get("id", "").startswith(kind)
            ),
            None,
        )

    def subfigure(name):
        letters[name] = letters.get(name, 0) + 1
        return f"({chr(96 + letters[name])})"

    def label(name, source):
        """Number a label and return its caption prefix; subfigures are lettered."""
        prefix = name.split("-", 1)[0]
        if prefix not in _KINDS:
            return None
        if name in labels:
            raise ValidationError(f"Duplicate report label: {name}")
        if (outer := parent(name)) and prefix in {"fig", "tbl"}:
            letter = subfigure(outer)
            labels[name] = {"text": labels[outer]["text"] + letter, "source": source}
            return letter
        counters[prefix] += 1
        labels[name] = {"text": f"{_KINDS[prefix]} {counters[prefix]}", "source": source}
        return labels[name]["text"] + ":"

    for segment in segments:
        if segment["kind"] == "open":
            segment["caption_prefix"] = label(
                segment["attributes"].get("id", ""), segment["source"]
            )
            stack.append(segment)
            continue
        if segment["kind"] == "close":
            stack.pop()
            continue
        if segment["kind"] == "embed":
            continue
        if segment["kind"] == "code":
            options = segment["options"]
            if name := options.get("label"):
                segment["caption_prefix"] = label(name, segment["source"])
            elif (outer := parent("fig-")) and (options.get("fig-cap") or options.get("tbl-cap")):
                segment["caption_prefix"] = subfigure(outer)
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
            name = match.group(1) if match else ""
            prefix = label(name, source) if name else None
            if name:
                if token.children and token.children[-1].type == "text":
                    token.children[-1].content = _LABEL.sub("", token.children[-1].content)
                if index and tokens[index - 1].type in {"heading_open", "paragraph_open"}:
                    tokens[index - 1].attrSet("id", name)
                if name.startswith("tbl-") and token.children and token.children[0].type == "text":
                    token.children[0].content = token.children[0].content.lstrip(": ")
                    token.children[0].content = prefix + " " + token.children[0].content
            image_token = next(
                (child for child in token.children or [] if child.type == "image"), None
            )
            outer = parent("fig-")
            if (
                image_token
                and index
                and tokens[index - 1].type == "paragraph_open"
                and (name.startswith("fig-") or (outer and not name and image_token.content))
            ):
                prefix = prefix or subfigure(outer)
                tokens[index - 1].tag = "figure"
                image_token.attrs.pop("id", None)
                if index + 1 < len(tokens) and tokens[index + 1].type == "paragraph_close":
                    tokens[index + 1].tag = "figure"
                caption = Token("html_inline", "", 0)
                caption.content = (
                    f"<figcaption>{prefix} {html.escape(image_token.content)}</figcaption>"
                )
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
                if captured.suffix.lower() == ".html":
                    child.attrSet("src", "sources/" + relative)
                    continue
                child.attrSet(
                    "src", _asset(directory, captured.read_bytes(), captured.suffix.lower())
                )
                child.attrSet("loading", "lazy")
    for index, segment in enumerate(segments):
        if segment["kind"] != "open":
            continue
        children, depth = [], 0
        for child in segments[index + 1 :]:
            if child["kind"] == "close" and depth == 0:
                break
            if depth == 0 and (child["kind"] != "markdown" or child["tokens"]):
                children.append(child)
            depth += {"open": 1, "close": -1}.get(child["kind"], 0)
        last = children[-1] if len(children) > 1 else None
        if (
            segment["caption_prefix"]
            and last
            and last["kind"] == "markdown"
            and [t.type for t in last["tokens"]] == ["paragraph_open", "inline", "paragraph_close"]
        ):
            last["caption"] = True
            last["tokens"][0].tag = last["tokens"][2].tag = "figcaption"
            prefix = Token("text", "", 0)
            prefix.content = segment["caption_prefix"] + " "
            last["tokens"][1].children.insert(0, prefix)
        cells = [child for child in children if not child.get("caption")]
        if any(key in segment["attributes"] for key in ("layout", "layout-ncol", "layout-nrow")):
            segment["grid"] = _grid(segment["attributes"], len(cells))

    for segment in segments:
        for token in segment.get("tokens", []):
            if not token.children:
                continue
            children = []
            for child in token.children:
                if child.type != "text":
                    children.append(child)
                    continue
                position, content, child.content = 0, child.content, ""
                for match in _CITATIONS.finditer(content):
                    if (rendered := citations.render(match)) is not None:
                        child.content += content[position : match.start()]
                        child.content += "\0" + rendered + "\0"
                        position = match.end()
                child.content += content[position:]
                pieces = child.content.split("\0")
                for index, piece in enumerate(pieces):
                    if index % 2:
                        cited = Token("html_inline", "", 0)
                        cited.content = piece
                        children.append(cited)
                        continue
                    children.extend(_crossrefs(piece, labels, references))
            token.children = children
    if citations.cited:
        heading = [
            Token("heading_open", "h2", 1),
            Token("inline", "", 0),
            Token("heading_close", "h2", -1),
        ]
        title = Token("text", "", 0)
        title.content = "References"
        heading[1].children = [title]
        entries = []
        for key, formatted in citations.bibliography():
            entry = Token("html_inline", "", 0)
            entry.content = formatted
            line = Token("inline", "", 0)
            line.children = [entry]
            opened = Token("paragraph_open", "p", 1)
            opened.attrs = {"id": f"ref-{key}", "class": "reference"}
            entries += [opened, line, Token("paragraph_close", "p", -1)]
        position = next(
            (
                i
                for i, s in enumerate(segments)
                if s["kind"] == "open" and s["attributes"].get("id") == "refs"
            ),
            None,
        )
        segments.insert(
            len(segments) if position is None else position + 1,
            {
                "kind": "markdown",
                "text": "",
                "tokens": entries if position is not None else heading + entries,
                "offset": len(mapping),
                "source": _source_range(mapping, max(0, len(mapping) - 1), len(mapping)),
            },
        )
    for segment in segments:
        segment["inline"] = [
            match.group(1)
            for token in segment.get("tokens", [])
            for child in token.children or []
            if child.type == "code_inline" and (match := _INLINE.fullmatch(child.content))
        ]
    md.options["cairn_labels"] = labels
    return labels, env


def _rich_output(output: dict, directory: Path, md, tokens) -> str:
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
        return md.renderer.render(tokens, md.options, {})
    if "application/json" in data:
        return (
            '<pre class="output">'
            + html.escape(json.dumps(data["application/json"], indent=2))
            + "</pre>"
        )
    return '<pre class="output">' + html.escape(data.get("text/plain", "")) + "</pre>"


_CSS = """body{font:17px/1.65 system-ui,sans-serif;color:#253139;margin:0;background:#fafaf8}main{max-width:900px;margin:4rem auto;padding:0 2rem 5rem}h1,h2,h3{line-height:1.2;color:#153b38}h1{font-size:2.7rem}h2{margin-top:2.5rem}a{color:#146e67}pre{overflow:auto;padding:1rem;background:#eef1ef;border-radius:6px;font:14px/1.5 ui-monospace,monospace}code{font-family:ui-monospace,monospace}table{display:block;width:fit-content;max-width:100%;overflow-x:auto;border-collapse:collapse;margin:1.5rem 0}th,td{padding:.4rem .75rem;text-align:left;border-bottom:1px solid #ccd4d0}th{background:#edf1ef}img{max-width:100%;height:auto}.output-image{display:block;margin:1rem auto}figure{margin:1.5rem 0}figcaption,.caption{color:#53645d;font-size:.9rem}.callout{padding:.6rem 1.2rem;border-left:4px solid #42877a;background:#eef6f2;margin:1.4rem 0}.callout-title{font-weight:700;margin:.2rem 0}.warning,.caution,.important{border-color:#b77b34;background:#fff6e8}.stderr,.error{border-left:3px solid #b84141}.rich-html{display:block;width:100%;min-height:280px;border:0}.viewer{display:block;width:100%;height:480px;border:1px solid #ccd4d0;border-radius:6px}.math.block{overflow:auto}.task-list-item{list-style:none}.task-list-item-checkbox{margin:0 .5rem 0 -1.5rem}dt{font-weight:600}dd{margin:0 0 .6rem 1.5rem}.columns,.layout-row{display:flex;gap:1.5rem}.column{flex:1 1 0;min-width:0}.layout-cell{min-width:0}.layout-grid{display:grid;gap:1rem}.panel-tabset{border:1px solid #ccd4d0;border-radius:6px;padding:0 1rem;margin:1.4rem 0}.tab-title{font-weight:700;border-bottom:1px solid #ccd4d0;padding:.5rem 0}.column-page,.column-page-inset{margin:0 -8%}.column-body-outset{margin:0 -4%}.column-screen,.column-screen-inset{width:100vw;margin-left:calc(50% - 50vw)}@media(min-width:1300px){.column-margin,.margin-caption{float:right;clear:right;width:240px;margin-right:-280px;font-size:.85rem}}@media(max-width:700px){table{font-size:.85rem}th,td{min-width:8rem;padding:.3rem .5rem}.columns,.layout-row{flex-direction:column}.column{flex:none!important}.layout-cell{width:auto!important}}.provenance{color:#61736a;font-size:.85rem;border-top:1px solid #ccd4d0;padding-top:1rem;margin-top:3rem}.source-block{scroll-margin-top:2rem}details>summary{cursor:pointer}@media print{body{background:white}main{margin:0;max-width:none}pre{white-space:pre-wrap}.rich-html{min-height:350px}}"""


_CALLOUT = re.compile(r"callout-(?:note|tip|warning|important|caution)")
_LENGTH = re.compile(r"\d+(?:\.\d+)?(?:px|%|em|rem)?")
_COLUMNS = {"margin", "page", "page-inset", "screen", "screen-inset", "body-outset"}


def _container(segment, children) -> str:
    """Static HTML of a fenced div around its rendered children."""
    attributes = segment["attributes"]
    classes = attributes["class"]
    cells = [part for child, part in children if not child.get("caption")]
    if grid := segment.get("grid"):
        rows, position = [], 0
        for row in grid:
            row_cells = cells[position : position + len(row)]
            position += len(row)
            rows.append(
                '<div class="layout-row">'
                + "".join(
                    f'<div class="layout-cell" style="width:{width}%">{cell}</div>'
                    for width, cell in zip(row, row_cells, strict=False)
                )
                + "</div>"
            )
        cells = rows
    body = "".join(cells) + "".join(part for child, part in children if child.get("caption"))
    callout = next((c for c in classes if _CALLOUT.fullmatch(c)), None)
    if callout:
        kind = callout.removeprefix("callout-")
        title = html.escape(attributes.get("title", kind.title()))
        return f'<aside class="callout {kind}"><p class="callout-title">{title}</p>{body}</aside>'
    if "tab" in classes:
        title = html.escape(attributes.get("title", ""))
        body = f'<p class="tab-title">{title}</p>{body}'
    ident = attributes.get("id", "")
    tag = "figure" if ident.startswith(("fig-", "tbl-")) else "div"
    width = attributes.get("width", "")
    style = f' style="flex:0 0 {width}"' if "column" in classes and _LENGTH.fullmatch(width) else ""
    names = html.escape(" ".join(classes), quote=True)
    return (
        f"<{tag}"
        + (f' id="{html.escape(ident, quote=True)}"' if ident else "")
        + (f' class="{names}"' if names else "")
        + f"{style}>{body}</{tag}>"
    )


def _tree(items):
    """Nest (segment, html) pairs between open and close markers into containers."""
    result, stack = [], []
    for segment, part in items:
        if segment["kind"] == "open":
            stack.append((segment, result))
            result = []
        elif segment["kind"] == "close":
            opened, outer = stack.pop()
            outer.append((opened, _container(opened, result)))
            result = outer
        else:
            result.append((segment, part))
    return result


def _render(segments, notebook, labels, references, mapping, md, directory, title, env):
    blocks, items = [], []
    for number, segment in enumerate(segments, 1):
        source = segment["source"]
        kind = "code" if segment["kind"] == "embed" else segment["kind"]
        block = {"id": f"block-{number}", "kind": kind, "source": source}
        if segment["kind"] in {"open", "close"}:
            if segment["kind"] == "open":
                block["attributes"] = segment["attributes"]
                if "grid" in segment:
                    block["grid"] = segment["grid"]
            blocks.append(block)
            items.append((segment, ""))
            continue
        attributes = f'data-source-path="{html.escape(source["path"], quote=True)}" data-line-start="{source["line_start"]}" data-line-end="{source["line_end"]}"'
        if segment["kind"] == "markdown":
            block["text"] = segment["text"]
            if "inline_cell" in segment:
                values = iter(
                    next(
                        output["data"]["application/vnd.cairn.inline+json"]
                        for output in notebook.cells[segment["inline_cell"]].outputs
                        if "application/vnd.cairn.inline+json" in output.get("data", {})
                    )
                )
                for token in segment["tokens"]:
                    for child in token.children or []:
                        if child.type == "code_inline" and _INLINE.fullmatch(child.content):
                            child.type, child.tag, child.content = "text", "", next(values)
            if segment.get("caption"):
                block["caption"] = True

            block["nodes"] = [_native_node(token, source, mapping) for token in segment["tokens"]]
            content = md.renderer.render(segment["tokens"], md.options, env)
        else:
            options = segment["options"]
            cell = segment.get("cell") or notebook.cells[segment["cell_index"]]

            def inline(text):
                return _resolved(md.parseInline(str(text), {}), labels, references)[0].children

            markdown = [_markdown_tokens(output, md, labels, references) for output in cell.outputs]
            caption = options.get("fig-cap") or options.get("tbl-cap")
            caption = caption and inline(caption)
            subcaptions = [inline(text) for text in options.get("fig-subcap", [])]
            if "cell" in segment:
                block["embed"] = {"reference": segment["reference"], "label": segment["label"]}
            block.update(
                {
                    "code": segment["text"],
                    "line_map": segment["line_map"],
                    "options": options,
                    "execution_count": cell.execution_count,
                    "outputs": [
                        _native_output(
                            output,
                            directory,
                            tokens and [_native_node(token, source) for token in tokens],
                        )
                        for output, tokens in zip(cell.outputs, markdown)
                    ],
                }
            )
            if caption:
                block["caption_nodes"] = [_native_node(token, source) for token in caption]
            if subcaptions:
                block["subcaption_nodes"] = [
                    [_native_node(token, source) for token in children] for children in subcaptions
                ]
            content = ""
            if options.get("echo", True):
                content += (
                    '<pre class="code"><code class="language-python">'
                    + html.escape(segment["text"])
                    + "</code></pre>"
                )
            if options.get("output", True):
                outputs = [
                    _rich_output(output, directory, md, tokens)
                    for output, tokens in zip(cell.outputs, markdown)
                ]
                pending = enumerate(subcaptions)
                for index, output in enumerate(cell.outputs):
                    if output.get("output_type") in {"display_data", "execute_result"} and (
                        subcaption := next(pending, None)
                    ):
                        outputs[index] = (
                            f"<figure>{outputs[index]}<figcaption>({chr(97 + subcaption[0])}) "
                            f"{md.renderer.renderInline(subcaption[1], md.options, {})}</figcaption></figure>"
                        )
                if columns := options.get("layout-ncol"):
                    outputs = [
                        f'<div class="layout-grid" style="grid-template-columns:'
                        f'repeat({columns},minmax(0,1fr))">'
                        + "".join(f"<div>{part}</div>" for part in outputs)
                        + "</div>"
                    ]
                content += "".join(outputs)
            label = options.get("label")
            prefix = segment.get("caption_prefix")
            if prefix:
                block["caption_prefix"] = prefix
            margin = ' class="margin-caption"' if options.get("cap-location") == "margin" else ""
            if label:
                block["label"] = label
                content = f'<figure id="{html.escape(label, quote=True)}">{content}'
                if caption:
                    content += f"<figcaption{margin}>{html.escape(prefix + ' ' if prefix else '')}{md.renderer.renderInline(caption, md.options, {})}</figcaption>"
                content += "</figure>"
            elif caption:
                names = "caption margin-caption" if margin else "caption"
                content += f'<p class="{names}">{html.escape(prefix + " " if prefix else "")}{md.renderer.renderInline(caption, md.options, {})}</p>'
        column = (
            f" column-{segment['options']['column']}"
            if segment["kind"] == "code" and "column" in segment["options"]
            else ""
        )
        part = f'<section class="source-block{column}" id="block-{number}" {attributes}>{content}</section>'
        if segment.get("caption"):
            part = content.replace(
                "<figcaption>",
                f'<figcaption class="source-block" id="block-{number}" {attributes}>',
                1,
            )
        items.append((segment, part))
        blocks.append(block)
    document = {
        "format": "cairn/document",
        "version": 4,
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
        + "\n".join(part for _, part in _tree(items))
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


def build(
    source, output=None, *, client=None, timeout=120, kernel_name="python3", overrides=()
) -> BuildResult:
    """Capture, pin, execute and render a report locally, preserving failed builds.

    Supported execution options are ``echo`` and ``eval`` in front matter;
    cell options additionally support ``output``, ``label``, ``fig-cap`` and
    ``tbl-cap``. Includes use ``{{< include relative.qmd >}}`` on a line by itself.
    Equations use ``$$...$$ {#eq-name}`` or ``$$...$$ (eq-name)``; headings/images use ``{#sec-name}`` or
    ``{#fig-name}``. Static rich HTML is displayed in a restricted iframe.
    A front-matter ``config`` is composed with Hydra and ``overrides`` and is
    available to cells as ``cfg``.
    """
    if client is None:
        from .sdk import _default, _implicit_client

        if _default.get() is not None:
            with _implicit_client() as implicit:
                return build(
                    source,
                    output,
                    client=implicit,
                    timeout=timeout,
                    kernel_name=kernel_name,
                    overrides=overrides,
                )
    nbformat, yaml, NotebookClient, markdown = _dependencies()
    source = Path(source).absolute()
    if source.is_symlink() or not source.is_file():
        raise ValidationError(f"Report source must be an existing regular file: {source}")
    if source.suffix.lower() not in {".qmd", ".md"}:
        raise ValidationError("Report source must be a .qmd or .md file")
    if not isinstance(timeout, int) or timeout <= 0:
        raise ValidationError("Report cell timeout must be a positive number of seconds")
    if isinstance(overrides, str) or any(not isinstance(item, str) for item in overrides):
        raise ValidationError("Config overrides must be a list of strings")
    directory = _new_directory(output, source)
    manifest = {
        "format": "cairn/report",
        "schema_version": 1,
        "status": "building",
        "created_at": now(),
        "source": "sources/" + source.name,
        "html": "index.html",
        "document": "document.json",
        "document_version": 4,
        "notebook": "executed.ipynb",
        "input_policy": "recorded",
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
        config = _compose(metadata, settings, overrides, sources)
        if config is not None:
            manifest.update(config=config, overrides=list(overrides))
        pins = _pin_inputs(client, metadata)
        manifest["inputs"] = [{k: v for k, v in pin.items() if k != "descriptor"} for pin in pins]
        manifest["dependencies"] = [
            {"artifact_id": p["artifact_id"], "role": "report-input:" + p["name"]} for p in pins
        ]
        _write_json(directory / "inputs.json", pins)
        _write_json(directory / "source-map.json", mapping)
        md = _markdown_engine(markdown)
        segments = _segments(text, mapping, md, yaml)
        references, citations = _References(client), _Citations(metadata, sources)
        labels, markdown_env = _prepare_markdown(
            text, segments, md, sources, mapping, directory, references, citations
        )
        for segment in segments:
            if segment["kind"] == "embed":
                segment.update(references.cell(segment["reference"], segment["label"], yaml))
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
            if segment["kind"] == "markdown":
                notebook.cells.append(
                    nbformat.v4.new_markdown_cell(
                        segment["text"], metadata={"cairn_source": segment["source"]}
                    )
                )
                if segment["inline"] and execution.get("eval", True):
                    segment["inline_cell"] = len(notebook.cells)
                    notebook.cells.append(
                        nbformat.v4.new_code_cell(
                            "_display({'application/vnd.cairn.inline+json': [str(eval(_source)) "
                            f"for _source in {segment['inline']!r}]}}, raw=True)",
                            metadata={"tags": ["cairn-inline", "remove-cell"]},
                        )
                    )
            if segment["kind"] != "code":
                continue
            options = {**execution, **segment["options"]}
            segment["options"] = options
            cell_metadata = {"cairn_source": segment["source"]}
            if "label" in options:
                cell_metadata["cairn_label"] = options["label"]
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
        environment["CAIRN_REPORT_CONFIG"] = json.dumps(config) if config is not None else ""
        environment["CAIRN_REPORT_RECORD"] = str(directory / "inputs.jsonl")
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
        _record_inputs(directory, manifest, pins, references.used)
        for emitted in notebook.cells[0].outputs:
            captured = emitted.get("data", {}).get("application/vnd.cairn.environment+json")
            if captured:
                manifest["environment"].update(captured)
        nbformat.write(notebook, directory / "executed.ipynb")
        _render(segments, notebook, labels, references, mapping, md, directory, title, markdown_env)
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
            index = segment.get("cell_index", segment.get("inline_cell"))
            if index is not None and notebook is not None:
                cell = notebook.cells[index]
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
    if manifest.get("document_version", 1) in {2, 3, 4}:
        document = json.loads((directory / "document.json").read_text("utf-8"))
        if (
            document.get("format") != "cairn/document"
            or document.get("version") != manifest["document_version"]
        ):
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
                "data": {
                    "title": manifest["title"],
                    "build_created_at": manifest["created_at"],
                    **({"config": manifest["config"]} if "config" in manifest else {}),
                },
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
    """Copy captured report sources for editing; a rebuild resolves its inputs again."""
    destination = Path(destination).absolute()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValidationError(f"Report copy destination must be new or empty: {destination}")
    with tempfile.TemporaryDirectory(prefix="cairn-report-copy-") as temporary:
        exported = export(ref, Path(temporary) / "bundle", client=client)
        manifest = json.loads((exported / "manifest.json").read_text("utf-8"))
        shutil.copytree(exported / "sources", destination, dirs_exist_ok=True)
    return _inside(destination, manifest["source"].removeprefix("sources/"))


def new(destination="NewReport.qmd", *, title="New report") -> Path:
    """Create a small executable report source without opening a browser."""
    path = Path(destination).absolute()
    if path.suffix.lower() not in {".qmd", ".md"}:
        path = path / "NewReport.qmd"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValidationError(f"Report source already exists: {path}")
    path.write_text(
        "---\ntitle: " + json.dumps(title) + "\ncairn:\n  resources: []\n---\n\n"
        "## Results {#sec-results}\n\n"
        'Read artifacts with `cairn.use("runs/name")` or `cairn.find("runs/*")`, then\n'
        "`.value` or `.records()`.\n\n"
        '```{python}\nprint("A report built locally with Cairn")\n```\n\n'
        "::: {.callout-note}\nThe manifest records the exact version of every artifact read.\n:::\n",
        "utf-8",
    )
    return path

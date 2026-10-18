"""Versioned typed values with explicit blobs and immutable-version references."""

from __future__ import annotations

import io
import json
import mimetypes
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import CapacityError, NotFound, Unsupported, ValidationError, canonical, safe_path


@dataclass
class Blob:
    """Bytes, a path or a binary stream to ingest when this value is recorded."""

    source: Any
    name: str | None = None
    media_type: str | None = None


@dataclass
class _Generated:
    write: Callable


class _BoundedFile:
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit

    def write(self, data):
        if self.limit is not None and self.stream.tell() + len(data) > self.limit:
            raise CapacityError("Serialized media exceeds the remote outbox capacity")
        return self.stream.write(data)

    def fileno(self):
        raise io.UnsupportedOperation("Use bounded writes for serialized media")

    def __getattr__(self, name):
        return getattr(self.stream, name)


@dataclass
class TypedValue:
    """An extensible value format. Every dependency belongs in blobs or refs."""

    format: str
    data: Any
    blobs: list = field(default_factory=list)
    refs: list = field(default_factory=list)
    version: int = 1


class File(TypedValue):
    def __init__(self, source, *, name=None, media_type=None):
        name = name or (Path(source).name if isinstance(source, (str, os.PathLike)) else "file")
        name = safe_path(name)
        media_type = media_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        super().__init__(
            "file", {"name": name, "media_type": media_type}, [Blob(source, name, media_type)]
        )


class Directory(TypedValue):
    def __init__(self, source):
        root = Path(source)
        if not root.is_dir():
            raise ValidationError("Directory source must be a directory")
        files = []
        blobs = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValidationError(f"Directory captures do not follow symlinks: {path}")
            if path.is_file():
                name = safe_path(path.relative_to(root).as_posix())
                files.append(name)
                blobs.append(Blob(path, name, mimetypes.guess_type(name)[0]))
        super().__init__("directory", {"name": root.name, "files": files}, blobs)


class Image(TypedValue):
    def __init__(self, source, *, name="image.png"):
        if isinstance(source, (bytes, bytearray, str, os.PathLike)):
            blob = Blob(source, name, mimetypes.guess_type(name)[0] or "image/png")
        else:
            try:
                from PIL import Image as PILImage
            except ImportError as exc:
                raise Unsupported("Image arrays require Pillow; install cairn2[media]") from exc
            image = source if isinstance(source, PILImage.Image) else PILImage.fromarray(source)
            blob = Blob(
                _Generated(lambda stream: image.save(stream, format="PNG")), name, "image/png"
            )
        super().__init__("image", {"name": safe_path(name)}, [blob])


class Array(TypedValue):
    def __init__(self, value, *, name="array.npy"):
        try:
            import numpy as np
        except ImportError as exc:
            raise Unsupported("Arrays require NumPy; install cairn2[media]") from exc
        array = np.asarray(value)
        if array.dtype.hasobject:
            raise ValidationError("Object arrays cannot be serialized without pickle")
        super().__init__(
            "array/npy",
            {"shape": list(array.shape), "dtype": str(array.dtype)},
            [
                Blob(
                    _Generated(lambda stream: np.save(stream, array, allow_pickle=False)),
                    safe_path(name),
                    "application/x-npy",
                )
            ],
        )


class Table(TypedValue):
    def __init__(self, value):
        if isinstance(value, (list, tuple)):
            rows = list(value)
            if not all(isinstance(row, dict) for row in rows):
                raise ValidationError("Table rows must be dictionaries")
            columns = list(dict.fromkeys(key for row in rows for key in row))
            super().__init__("table/rows", {"columns": columns, "rows": rows})
            return
        try:
            import pyarrow as pa
        except ImportError as exc:
            raise Unsupported("Arrow and pandas tables require cairn2[media]") from exc
        table = value if isinstance(value, pa.Table) else pa.Table.from_pandas(value)

        def write(stream):
            with pa.ipc.new_file(stream, table.schema) as writer:
                writer.write_table(table)

        super().__init__(
            "table/arrow",
            {"columns": table.column_names, "rows": table.num_rows},
            [Blob(_Generated(write), "table.arrow", "application/vnd.apache.arrow.file")],
        )


class Figure(TypedValue):
    def __init__(self, figure):
        if hasattr(figure, "to_json"):
            super().__init__("figure/plotly", json.loads(figure.to_json()))
        elif hasattr(figure, "savefig"):
            super().__init__(
                "figure/svg",
                {},
                [
                    Blob(
                        _Generated(
                            lambda stream: figure.savefig(stream, format="svg", bbox_inches="tight")
                        ),
                        "figure.svg",
                        "image/svg+xml",
                    )
                ],
            )
        else:
            raise ValidationError("Figure expects a Plotly or matplotlib figure")


def references(value):
    """Yield standard envelope dependencies without interpreting literal payloads."""
    if isinstance(value, dict):
        if value.get("$cairn") == 1:
            yield from (("blob", blob) for blob in value.get("blobs", []))
            yield from (("ref", ref) for ref in value.get("refs", []))
            if value.get("format") == "json/literal":
                return
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def _blob_descriptor(blob):
    if not isinstance(blob, dict):
        raise ValidationError("Blob descriptors must be dictionaries")
    digest = blob.get("digest")
    size = blob.get("size")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ValidationError("Blob digest must be a lowercase SHA-256 hex digest")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ValidationError("Blob size must be a nonnegative integer")
    if "name" in blob:
        safe_path(blob["name"])
    return dict(blob)


def encode(value, put_blob):
    """Convert Python values to JSON, committing immutable bytes through put_blob."""
    if isinstance(value, StoredValue):
        for kind, dependency in references(value.envelope):
            if kind == "blob":
                _blob_descriptor(dependency)
        canonical(value.envelope)
        return value.envelope
    if isinstance(value, TypedValue):
        if not isinstance(value.format, str) or not value.format:
            raise ValidationError("A typed value format must be a non-empty string")
        data = encode(value.data, put_blob)
        blobs = []
        for blob in value.blobs:
            if isinstance(blob, dict):
                blobs.append(_blob_descriptor(blob))
                continue
            if not isinstance(blob, Blob):
                raise ValidationError("TypedValue blobs must be Blob objects or blob descriptors")
            if isinstance(blob.source, _Generated):
                with tempfile.TemporaryFile("w+b") as stream:
                    limit = getattr(getattr(put_blob, "__self__", None), "max_bytes", None)
                    blob.source.write(_BoundedFile(stream, limit))
                    stream.seek(0)
                    descriptor = put_blob(stream)
            else:
                source = bytes(blob.source) if isinstance(blob.source, bytearray) else blob.source
                descriptor = put_blob(source)
            descriptor = dict(descriptor)
            if blob.name is not None:
                descriptor["name"] = safe_path(blob.name)
            if blob.media_type is not None:
                descriptor["media_type"] = blob.media_type
            blobs.append(descriptor)
        result = {
            "$cairn": 1,
            "format": value.format,
            "version": value.version,
            "data": data,
            "blobs": blobs,
            "refs": list(value.refs),
        }
    elif isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValidationError("JSON object keys must be strings")
        result = {key: encode(item, put_blob) for key, item in value.items()}
        if "$cairn" in value:
            dependencies = [
                dependency for item in result.values() for dependency in references(item)
            ]
            result = {
                "$cairn": 1,
                "format": "json/literal",
                "version": 1,
                "data": result,
                "blobs": [d for kind, d in dependencies if kind == "blob"],
                "refs": [d for kind, d in dependencies if kind == "ref"],
            }
    elif isinstance(value, (list, tuple)):
        result = [encode(item, put_blob) for item in value]
    elif value is None or isinstance(value, (str, int, float, bool)):
        result = value
    else:
        raise ValidationError(f"Unsupported value {type(value).__name__}; use a typed value")
    canonical(result)
    return result


@dataclass
class StoredValue:
    """A decoded typed value, retaining its format and exact content dependencies."""

    envelope: dict
    backend: Any = field(repr=False)

    @property
    def format(self):
        return self.envelope["format"]

    @property
    def version(self):
        return self.envelope["version"]

    @property
    def data(self):
        return decode(self.envelope["data"], self.backend)

    @property
    def blobs(self):
        return self.envelope["blobs"]

    @property
    def refs(self):
        return self.envelope["refs"]

    def open(self, name=None):
        matches = [blob for blob in self.blobs if name is None or blob.get("name") == name]
        if len(matches) != 1:
            raise NotFound("Select exactly one blob by its name")
        return self.backend.open_blob(matches[0]["digest"])

    def load(self):
        """Reconstruct a supported Python object; unknown formats preserve their data."""
        if self.format == "table/rows":
            return self.data["rows"]
        if self.format == "figure/plotly":
            try:
                import plotly.graph_objects as go
            except ImportError:
                return self.data
            return go.Figure(self.data)
        if self.format in {"array/npy", "table/arrow", "image"}:
            with self.open() as stream:
                if self.format == "array/npy":
                    import numpy as np

                    return np.load(stream, allow_pickle=False)
                if self.format == "table/arrow":
                    import pyarrow as pa

                    return pa.ipc.open_file(stream).read_all()
                from PIL import Image as PILImage

                with PILImage.open(stream) as image:
                    return image.copy()
        if self.format in {"file", "figure/svg"}:
            with self.open() as stream:
                return stream.read()
        return self.data

    def download(self, to, *, name=None):
        selected = [blob for blob in self.blobs if name is None or blob.get("name") == name]
        if not selected:
            raise NotFound("No matching downloadable blobs")
        destination = Path(to)
        many = len(selected) > 1 or self.format == "directory"
        if many:
            destination.mkdir(parents=True, exist_ok=True)
        paths = []
        for blob in selected:
            path = destination
            if many or destination.is_dir():
                filename = safe_path(blob.get("name") or blob["digest"])
                path = destination / filename
                if not path.resolve().is_relative_to(destination.resolve()):
                    raise ValidationError("Download path escapes the destination")
            path.parent.mkdir(parents=True, exist_ok=True)
            with (
                self.backend.open_blob(blob["digest"]) as source,
                tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output,
            ):
                temporary = Path(output.name)
                try:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    raise
            os.replace(temporary, path)
            paths.append(path)
        return paths if many else paths[0]


def decode(value, backend):
    if isinstance(value, dict):
        if value.get("$cairn") == 1:
            if value.get("format") == "json/literal":
                return {key: decode(item, backend) for key, item in value["data"].items()}
            return StoredValue(value, backend)
        return {key: decode(item, backend) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item, backend) for item in value]
    return value

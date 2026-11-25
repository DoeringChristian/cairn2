"""What programs log besides plain values. Each becomes a map or a blob, which is all the
server sees, and is read back as the same type; `load` gives what was logged."""

import io
import json
import mimetypes
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PLOTLY = "application/vnd.plotly.v1+json"


@dataclass
class File:
    """A file, or bytes. `immutable` promises it stays unchanged until the artifact is
    finished; `link` that it stays where it is for as long as the artifact exists."""

    source: Any
    media_type: str | None = None
    immutable: bool = False
    link: bool = False

    def media(self):
        if self.media_type:
            return self.media_type
        name = os.fspath(self.source) if isinstance(self.source, (str, os.PathLike)) else ""
        return mimetypes.guess_type(name)[0] or "application/octet-stream"


@dataclass
class Ref:
    """An artifact, given as a reference, an ID or a reader. `incomplete` allows one that
    did not complete."""

    artifact: Any
    incomplete: bool = False


class Directory(Mapping):
    """A folder, as one value: its files by relative path."""

    def __init__(self, source, *, immutable=False, link=False):
        if isinstance(source, Mapping):
            self._files = dict(source)
            return
        root, self._files = Path(source), {}
        if not root.is_dir():
            raise NotADirectoryError(root)
        for path in sorted(root.rglob("*")):
            if path.is_symlink() and not path.exists():
                raise FileNotFoundError(f"Broken link: {path}")
            if path.is_file():
                self._files[path.relative_to(root).as_posix()] = File(path, immutable=immutable, link=link)

    def __getitem__(self, name):
        return self._files[name]

    def __iter__(self):
        return iter(self._files)

    def __len__(self):
        return len(self._files)

    def __repr__(self):
        return f"<Directory {len(self)} files>"

    def expand(self):
        if "_type" in self._files:
            raise ValueError("A folder cannot hold a file named _type at its top")
        return {"_type": "directory", **self._files}

    @classmethod
    def stored(cls, value):
        value.pop("_type")
        return cls(value)


class _Typed:
    """A value whose content is encoded when it is logged, and decoded when it is loaded."""

    type = ""

    def __init__(self, source, **meta):
        self.source, self.meta = source, meta
        self._stored = None

    def __repr__(self):
        shown = {k: v for k, v in (self._stored or self.meta).items() if k not in ("_type", "data")}
        return f"<{type(self).__name__}{''.join(f' {k}={v!r}' for k, v in shown.items())}>"

    def expand(self):
        return dict(self._stored) if self._stored else {**self.meta, "_type": self.type, **self._encode()}

    @classmethod
    def stored(cls, value):
        made = cls(None)
        made._stored = value
        made.meta = {k: v for k, v in value.items() if k not in ("_type", "data")}
        return made

    def read(self):
        """The encoded content."""
        data = self.expand()["data"]
        if not isinstance(data, File):
            return data.read()
        return data.source if isinstance(data.source, bytes) else Path(data.source).read_bytes()


class Image(_Typed):
    """A picture: an array (height, width[, channels]), a PIL image, or an image file.

    Arrays of floats are clipped to 0..1; they are stored as 8-bit PNG."""

    type = "image"

    def _encode(self):
        if isinstance(self.source, (str, os.PathLike)):
            return {"data": File(self.source)}
        from PIL import Image as Pillow

        image = self.source
        if not isinstance(image, Pillow.Image):
            import numpy as np

            pixels = np.asarray(image)
            if pixels.ndim == 3 and pixels.shape[2] == 1:
                pixels = pixels[..., 0]
            if pixels.dtype == bool:
                pixels = pixels.astype(np.uint8) * 255
            elif pixels.dtype.kind == "f":
                pixels = (np.nan_to_num(pixels.astype(np.float64)).clip(0, 1) * 255 + 0.5).astype(np.uint8)
            elif pixels.dtype != np.uint8:
                raise TypeError("Images are floats in 0..1 or 8-bit integers; log other arrays as Array")
            image = Pillow.fromarray(pixels)
        out = io.BytesIO()
        image.save(out, format="PNG")
        return {"width": image.width, "height": image.height, "data": File(out.getvalue(), "image/png")}

    def load(self):
        """A PIL image."""
        from PIL import Image as Pillow

        image = Pillow.open(io.BytesIO(self.read()))
        image.load()
        return image

    def array(self):
        """The pixels, as 8-bit integers."""
        import numpy as np

        return np.asarray(self.load())


class Array(_Typed):
    """A NumPy array."""

    type = "array"

    def _encode(self):
        import numpy as np

        array = np.asarray(self.source)
        if array.dtype.hasobject:
            raise TypeError("Arrays of Python objects cannot be logged")
        out = io.BytesIO()
        np.save(out, array, allow_pickle=False)
        return {"shape": list(array.shape), "dtype": str(array.dtype), "data": File(out.getvalue(), "application/x-npy")}

    def load(self):
        import numpy as np

        return np.load(io.BytesIO(self.read()), allow_pickle=False)


class Table(_Typed):
    """Rows as dicts of JSON values."""

    type = "table"

    def _encode(self):
        rows = [dict(row) for row in self.source]
        columns = list(dict.fromkeys(key for row in rows for key in row))
        data = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
        return {"rows": len(rows), "columns": columns, "data": File(data, "application/x-ndjson")}

    def load(self):
        return [json.loads(line) for line in self.read().decode().split("\n") if line]


class Figure(_Typed):
    """A Matplotlib figure, stored as SVG, or a Plotly figure, stored as its JSON; `load`
    gives the SVG's text or the JSON's value."""

    type = "figure"

    def _encode(self):
        if hasattr(self.source, "to_plotly_json"):
            return {"data": File(self.source.to_json().encode(), PLOTLY)}
        out = io.BytesIO()
        self.source.savefig(out, format="svg")
        return {"data": File(out.getvalue(), "image/svg+xml")}

    def load(self):
        data = self.expand()["data"]
        return json.loads(self.read()) if data.media_type == PLOTLY else self.read().decode()


TYPES = {"directory": Directory, "image": Image, "array": Array, "table": Table, "figure": Figure}


def typed(value):
    """A stored map as its type; unknown types stay maps."""
    found = TYPES.get(kind) if isinstance(kind := value.get("_type"), str) else None
    return found.stored(value) if found else value

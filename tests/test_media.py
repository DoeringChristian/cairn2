import hashlib
import io
from pathlib import Path

import pytest

from cairn.media import (
    Array,
    Blob,
    Directory,
    Figure,
    File,
    Image,
    StoredValue,
    Table,
    TypedValue,
    decode,
    encode,
)
from cairn.model import ValidationError


class Blobs:
    def __init__(self):
        self.values = {}

    def put_blob(self, source):
        content = (
            source
            if isinstance(source, bytes)
            else Path(source).read_bytes()
            if isinstance(source, (str, Path))
            else source.read()
        )
        digest = hashlib.sha256(content).hexdigest()
        self.values[digest] = content
        return {"digest": digest, "size": len(content)}

    def open_blob(self, digest):
        return io.BytesIO(self.values[digest])


def test_literal_envelopes_are_round_tripped_without_interpretation():
    backend = Blobs()
    literal = {
        "$cairn": 1,
        "format": "fake",
        "blobs": [{"not": "a dependency"}],
        "nested": {"$cairn": "ordinary data"},
    }
    value = encode(literal, backend.put_blob)
    assert value["format"] == "json/literal"
    assert value["blobs"] == []
    assert decode(value, backend) == literal


def test_nested_typed_values_and_custom_formats_are_lossless(tmp_path):
    backend = Blobs()
    nested = {"$cairn": "literal key", "attachment": File(b"hello", name="hello.txt")}
    encoded = encode(nested, backend.put_blob)
    assert len(encoded["blobs"]) == 1
    value = decode(encoded, backend)
    assert value["$cairn"] == "literal key"
    assert value["attachment"].load() == b"hello"
    custom = TypedValue(
        "model/custom",
        {"architecture": "linear"},
        [Blob(b"weights", "weights.bin")],
        [{"artifact_id": "version-1"}],
    )
    result = decode(encode(custom, backend.put_blob), backend)
    assert isinstance(result, StoredValue)
    assert result.format == "model/custom"
    assert result.data == {"architecture": "linear"}
    assert result.refs == [{"artifact_id": "version-1"}]
    output = result.download(tmp_path / "model")
    assert output.read_bytes() == b"weights"


def test_directory_download_and_path_escape(tmp_path):
    backend = Blobs()
    source = tmp_path / "inputs"
    (source / "nested").mkdir(parents=True)
    (source / "a.txt").write_text("A")
    (source / "nested/b.txt").write_text("B")
    value = decode(encode(Directory(source), backend.put_blob), backend)
    output = tmp_path / "output"
    paths = value.download(output)
    assert len(paths) == 2
    assert (output / "nested/b.txt").read_text() == "B"
    with pytest.raises(ValidationError):
        File(b"x", name="../escape")
    (source / "link").symlink_to(source / "a.txt")
    with pytest.raises(ValidationError, match="symlinks"):
        Directory(source)


def test_arrowless_table_is_typed_and_queryable_json():
    backend = Blobs()
    rows = [{"x": 1, "label": "one"}, {"x": 2, "label": None}]
    result = decode(encode(Table(rows), backend.put_blob), backend)
    assert result.format == "table/rows"
    assert result.load() == rows


def test_numpy_and_image_reconstruction():
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    backend = Blobs()
    array = np.arange(24, dtype=np.float32).reshape(4, 6)
    value = decode(encode(Array(array), backend.put_blob), backend)
    np.testing.assert_array_equal(value.load(), array)
    assert value.load().dtype == array.dtype
    with pytest.raises(ValidationError, match="Object arrays"):
        Array(np.array([{}], dtype=object))
    pixels = np.zeros((3, 4, 3), dtype=np.uint8)
    pixels[:, :, 0] = 255
    image = decode(encode(Image(pixels), backend.put_blob), backend)
    np.testing.assert_array_equal(np.asarray(image.load()), pixels)


def test_nonfinite_numbers_and_nonstring_keys_rejected():
    backend = Blobs()
    with pytest.raises(ValidationError):
        encode({"value": float("nan")}, backend.put_blob)
    with pytest.raises(ValidationError):
        encode({1: "ambiguous"}, backend.put_blob)
    with pytest.raises(ValidationError, match="digest"):
        encode(TypedValue("unsafe", {}, [{"digest": "../../escape", "size": 0}]), backend.put_blob)


def test_arrow_schema_and_nulls_round_trip():
    pa = pytest.importorskip("pyarrow")
    backend = Blobs()
    table = pa.table({"index": [1, None], "label": ["first", "second"]})
    stored = decode(encode(Table(table), backend.put_blob), backend)
    assert stored.load().equals(table)
    assert stored.load().schema == table.schema


def test_interactive_and_static_figure_round_trip():
    go = pytest.importorskip("plotly.graph_objects")
    matplotlib = pytest.importorskip("matplotlib.figure")
    backend = Blobs()
    figure = go.Figure(data=[go.Scatter(x=[1, 2], y=[3, 5], name="series")])
    interactive = decode(encode(Figure(figure), backend.put_blob), backend)
    assert interactive.load().to_plotly_json() == figure.to_plotly_json()
    static = matplotlib.Figure()
    static.subplots().plot([1, 2], [3, 5])
    exported = decode(encode(Figure(static), backend.put_blob), backend)
    assert b"<svg" in exported.load()

"""Between Python values and the Value message. Records are encoded once, here."""

from . import cairn_pb2 as pb
from .errors import Invalid

MAX_RECORD = 64 * 1024 * 1024
MAX_DEPTH = 32


def encode(value, target, depth=0):
    """Write a plain value, or a Blob or Ref message, into a Value message."""
    if depth > MAX_DEPTH:
        raise Invalid(f"Values nest at most {MAX_DEPTH} levels")
    if value is None:
        target.null = True
    elif value is True or value is False:
        target.flag = value
    elif isinstance(value, int):
        if not -(2**63) <= value < 2**63:
            raise Invalid("Integers must fit in 64 bits")
        target.int = value
    elif isinstance(value, float):
        target.float = value
    elif isinstance(value, str):
        target.text = value
    elif isinstance(value, bytes):
        target.bytes = value
    elif isinstance(value, pb.Blob):
        target.blob.CopyFrom(value)
    elif isinstance(value, pb.Ref):
        target.ref.CopyFrom(value)
    elif isinstance(value, dict):
        target.map.SetInParent()
        encode_map(value, target.map, depth)
    elif isinstance(value, (list, tuple)):
        target.list.SetInParent()
        for item in value:
            encode(item, target.list.items.add(), depth + 1)
    elif hasattr(value, "item") and getattr(value, "ndim", None) == 0:  # a NumPy or torch scalar
        encode(value.item(), target, depth)
    else:
        raise Invalid(f"Cannot log a {type(value).__name__}")


def encode_map(value, target, depth=0):
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise Invalid("Keys are nonempty strings")
        entry = target.entries.add()
        entry.key = key
        encode(item, entry.value, depth + 1)
    return target


def decode(value):
    kind = value.WhichOneof("kind")
    if kind == "map":
        return decode_map(value.map)
    if kind == "list":
        return [decode(item) for item in value.list.items]
    return None if kind in ("null", None) else getattr(value, kind)


def decode_map(value):
    return {entry.key: decode(entry.value) for entry in value.entries}


def record(value):
    data = encode_map(value, pb.Map()).SerializeToString()
    if len(data) > MAX_RECORD:
        raise Invalid("A record holds at most 64 MiB; log several, or files instead of bytes")
    return data


def read(data):
    return decode_map(pb.Map.FromString(data))

"""Write the protocol's messages for the pages: uv run python tools/ui_schema.py"""

import json
from pathlib import Path

from google.protobuf.descriptor_pb2 import FileDescriptorProto

from cairn import cairn_pb2 as pb

TARGET = Path(__file__).parents[1] / "ui/src/schema.ts"
KINDS = {1: "double", 4: "uint64", 8: "bool", 9: "string", 11: "message", 12: "bytes", 13: "uint32", 18: "sint64"}


def schema():
    """Each message's fields: number, name, kind, message, and whether there is one, many or a map."""
    file = FileDescriptorProto.FromString(pb.DESCRIPTOR.serialized_pb)
    found, maps = {}, set()

    def add(message, prefix):
        name = prefix + message.name
        if message.options.map_entry:
            maps.add(name)
        for nested in message.nested_type:
            add(nested, f"{name}.")
        found[name] = []
        for field in message.field:
            kind = field.type_name.removeprefix(".cairn.v1.")
            found[name].append([field.number, field.name, KINDS[field.type], kind, "map" if kind in maps else "many" if field.label == 3 else "one"])

    for message in file.message_type:
        add(message, "")
    return "// Written by tools/ui_schema.py from cairn.proto.\nexport default {\n" + ",\n".join(f" {json.dumps(k)}: {json.dumps(v)}" for k, v in found.items()) + "\n}\n"


if __name__ == "__main__":
    TARGET.write_text(schema())

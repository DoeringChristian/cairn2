"""Portable values, identifiers and errors shared by every access path."""

import hashlib
import json
import math
import re
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath

PROTOCOL_VERSION = 4


class CairnError(Exception):
    code = "cairn_error"

    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = details or {}


class ValidationError(CairnError):
    code = "validation_error"


class NotFound(CairnError):
    code = "not_found"


class Conflict(CairnError):
    code = "conflict"


class NotPublished(CairnError):
    code = "not_published"


class Pending(CairnError):
    code = "pending"


class Unauthorized(CairnError):
    code = "unauthorized"


class IntegrityError(CairnError):
    code = "integrity_error"


class Unsupported(CairnError):
    code = "unsupported"


class CapacityError(CairnError):
    code = "capacity_error"


def new_id(prefix=None):
    identifier = uuid.uuid4().hex
    return f"{prefix}_{identifier}" if prefix else identifier


def now():
    return datetime.now(UTC).isoformat()


def validate_json(value, depth=0):
    if depth > 64:
        raise ValidationError("JSON nesting exceeds 64 levels")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if re.search(r"[\x00\ud800-\udfff]", value):
            raise ValidationError(
                "JSON strings and keys must contain Unicode scalar values other than NUL"
            )
        return
    if isinstance(value, int):
        if abs(value) > 2**53 - 1:
            raise ValidationError(
                "JSON integers must be exactly representable in 53 bits; use a tensor"
            )
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("Non-finite numbers require a typed array")
        return
    if isinstance(value, list):
        for item in value:
            validate_json(item, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for key, item in value.items():
            validate_json(key, depth + 1)
            validate_json(item, depth + 1)
        return
    raise ValidationError(f"Not portable JSON: {type(value).__name__}")


def canonical(value):
    validate_json(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def validate_name(value):
    if not isinstance(value, str) or not value or len(value) > 255:
        raise ValidationError("Names must contain 1–255 characters")
    if any(ord(c) < 32 for c in value) or "@" in value:
        raise ValidationError("Names cannot contain control characters or @")
    return value


def safe_path(value):
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValidationError("Invalid portable relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or PureWindowsPath(value).drive
        or ".." in path.parts
        or value in {".", ""}
    ):
        raise ValidationError("Expected a relative path within the destination")
    return path.as_posix()


def validate_digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValidationError("Expected a lowercase SHA-256 digest")
    return value


def dependencies(value):
    """Collect standard envelope dependencies without interpreting opaque formats."""
    blobs, refs = {}, {}

    def visit(item, depth=0):
        if depth > 64:
            raise ValidationError("Value nesting exceeds 64 levels")
        if isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif isinstance(item, dict):
            if "$cairn" in item:
                if (
                    type(item["$cairn"]) is not int
                    or item["$cairn"] != 1
                    or type(item.get("version")) is not int
                    or item["version"] != 1
                ):
                    raise Unsupported("Unsupported value envelope version")
                if not isinstance(item.get("format"), str) or not item["format"]:
                    raise ValidationError("Typed values require a format")
                if not isinstance(item.get("blobs", []), list) or not isinstance(
                    item.get("refs", []), list
                ):
                    raise ValidationError("Typed value blobs and refs must be lists")
                for blob in item.get("blobs", []):
                    if not isinstance(blob, dict):
                        raise ValidationError("Blob descriptors must be objects")
                    sha = validate_digest(blob.get("digest"))
                    size = blob.get("size")
                    if type(size) is not int or size < 0:
                        raise ValidationError("Blob size must be a nonnegative integer")
                    if sha in blobs and blobs[sha]["size"] != size:
                        raise ValidationError("Conflicting sizes for one content digest")
                    blobs[sha] = {"digest": sha, "size": size}
                for ref in item.get("refs", []):
                    if not isinstance(ref, dict) or not isinstance(ref.get("artifact_id"), str):
                        raise ValidationError("Dependencies require an exact artifact ID")
                    role = ref.get("role", "input")
                    if not isinstance(role, str):
                        raise ValidationError("Dependency role must be a string")
                    refs[(ref["artifact_id"], role)] = {
                        "artifact_id": ref["artifact_id"],
                        "role": role,
                    }
                if item["format"] == "json/literal":
                    return
            for key, child in item.items():
                if key not in {"blobs", "refs"} or "$cairn" not in item:
                    visit(child, depth + 1)

    validate_json(value)
    visit(value)
    return list(blobs.values()), list(refs.values())

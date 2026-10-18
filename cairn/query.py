"""Bounded, portable predicates and stateless keyset cursors."""

import base64
import json
import re
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from .model import ValidationError, canonical, digest


def parts(pointer):
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise ValidationError("Field paths use JSON pointers, for example /loss")
    tokens = pointer.split("/")[1:] if pointer else []
    if len(tokens) > 16:
        raise ValidationError("Field paths may have at most 16 segments")
    for token in tokens:
        if re.search(r"~(?![01])", token):
            raise ValidationError("Invalid JSON pointer escape")
    return [token.replace("~1", "/").replace("~0", "~") for token in tokens]


def _array_index(token):
    # RFC 6901 permits ASCII decimal indexes without signs or leading zeroes.
    # A larger index cannot address a stored array, but remains a valid object key.
    if len(token) <= 19 and re.fullmatch(r"0|[1-9][0-9]*", token):
        index = int(token)
        if index <= 2**63 - 1:
            return index
    return None


def extract(data, pointer):
    value = data
    for part in parts(pointer):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif (
            isinstance(value, list)
            and (index := _array_index(part)) is not None
            and index < len(value)
        ):
            value = value[index]
        else:
            return False, None
    return True, value


def _sqlite_field(column, tokens, *, containers=False):
    # SQLite versions differ in how JSON paths handle escaped object keys.
    # Traverse those keys directly instead of relying on path-string escaping.
    if all(
        _array_index(token) is None
        and not any(character in '"\\' or ord(character) < 32 for character in token)
        for token in tokens
    ):
        path = "$" + "".join("." + json.dumps(token, ensure_ascii=False) for token in tokens)
        return sa.func.json_type(column, path), sa.func.json_extract(column, path)
    # json_each distinguishes object keys from array indexes and preserves JSON
    # types. Correlated joins keep SQL size linear even through mixed containers.
    source, joined, conditions = column, None, []
    for token in tokens:
        node = sa.func.json_each(source).table_valued("key", "value", "type", "atom").alias()
        joined = node if joined is None else joined.join(node, sa.true())
        match = sa.and_(sa.func.typeof(node.c.key) == "text", node.c.key == token)
        if (index := _array_index(token)) is not None:
            match = sa.or_(
                match, sa.and_(sa.func.typeof(node.c.key) == "integer", node.c.key == index)
            )
        conditions.append(match)
        source = sa.case((node.c.type.in_(["array", "object"]), node.c.value), else_="null")
    select = sa.select(node.c.type, node.c.atom).select_from(joined).where(*conditions).limit(1)
    return (
        select.with_only_columns(node.c.type).scalar_subquery(),
        select.with_only_columns(node.c.value if containers else node.c.atom).scalar_subquery(),
    )


def _field(column, pointer, dialect):
    tokens = parts(pointer)
    if dialect == "postgresql":
        root = sa.cast(column, JSONB)
        value = sa.func.jsonb_extract_path(root, *tokens) if tokens else root
        text = sa.func.jsonb_extract_path_text(root, *tokens) if tokens else sa.cast(value, sa.Text)
        guards = []
        for offset, token in enumerate(tokens):
            if _array_index(token) is None:
                parent = sa.func.jsonb_extract_path(root, *tokens[:offset]) if offset else root
                guards.append(sa.func.jsonb_typeof(parent) != "array")
        if guards:
            value = sa.case((sa.and_(*guards), value), else_=None)
            text = sa.case((sa.and_(*guards), text), else_=None)
        kind = sa.func.jsonb_typeof(value)
        number = sa.case((kind == "number", sa.cast(text, sa.Numeric)), else_=None)
        return kind, text, number
    kind, scalar = _sqlite_field(column, tokens)
    normalized = sa.case(
        (kind.in_(["integer", "real"]), "number"), (kind == "text", "string"), else_=kind
    )
    return normalized, scalar, scalar


def _validate_predicate(where):
    pending, cost = [(where, 0)], 0
    comparisons = {"eq", "ne", "lt", "le", "gt", "ge", "in", "contains"}
    while pending:
        condition, depth = pending.pop()
        if depth > 12 or not isinstance(condition, dict):
            raise ValidationError("Invalid predicate or excessive nesting")
        cost += 1
        if "and" in condition or "or" in condition:
            operator = "and" if "and" in condition else "or"
            children = condition[operator]
            if (
                set(condition) != {operator}
                or not isinstance(children, list)
                or not 1 <= len(children) <= 32
            ):
                raise ValidationError("Boolean predicates require 1–32 conditions")
            pending.extend((child, depth + 1) for child in children)
        else:
            if set(condition) - {"path", "op", "value"} or not {"path", "op"} <= set(condition):
                raise ValidationError("Predicates require path, op, and optional value")
            tokens, operation = parts(condition["path"]), condition["op"]
            if not isinstance(operation, str) or operation not in comparisons | {
                "exists",
                "is_null",
            }:
                raise ValidationError("Unsupported query operator")
            value = condition.get("value")
            if operation == "exists":
                if "value" in condition and not isinstance(value, bool):
                    raise ValidationError("exists requires a boolean value")
            elif operation == "is_null":
                if value is not None:
                    raise ValidationError("is_null does not accept a non-null value")
            else:
                if "value" not in condition:
                    raise ValidationError("Comparison predicates require a value")
                if operation == "in" and (not isinstance(value, list) or len(value) > 100):
                    raise ValidationError("in requires at most 100 scalar values")
                values = value if operation == "in" else [value]
                for item in values:
                    if item is not None and not isinstance(item, (bool, int, float, str)):
                        raise ValidationError("Predicates compare scalar values only")
                    if (item is None or isinstance(item, bool)) and operation not in {
                        "eq",
                        "ne",
                        "in",
                        "contains",
                    }:
                        raise ValidationError("Null and booleans support only eq/ne/in/contains")
                    canonical(item)
            cost += (1 + len(tokens)) * (max(1, len(value)) if operation == "in" else 1)
        if cost > 512:
            raise ValidationError("Predicate exceeds the total condition complexity limit")


def _comparison(field, operation, value, dialect):
    kind, text, number = field
    if value is None:
        return kind == "null" if operation == "eq" else sa.and_(kind.is_not(None), kind != "null")
    if isinstance(value, bool):
        if dialect == "postgresql":
            expression, compatible, argument = text, kind == "boolean", str(value).lower()
        else:
            expression, compatible, argument = text, kind.in_(["true", "false"]), int(value)
    elif isinstance(value, (int, float)):
        expression, compatible, argument = number, kind == "number", value
    elif isinstance(value, str):
        expression = text.collate("C" if dialect == "postgresql" else "BINARY")
        compatible, argument = kind == "string", value
    comparisons = {
        "eq": lambda: expression == argument,
        "ne": lambda: expression != argument,
        "lt": lambda: expression < argument,
        "le": lambda: expression <= argument,
        "gt": lambda: expression > argument,
        "ge": lambda: expression >= argument,
    }
    return sa.and_(compatible, comparisons[operation]())


def _contains(column, pointer, value, dialect):
    tokens = parts(pointer)
    if dialect == "postgresql":
        root = sa.cast(column, JSONB)
        array = sa.func.jsonb_extract_path(root, *tokens) if tokens else root
        kind = _field(column, pointer, dialect)[0]
        return sa.and_(kind == "array", array.op("@>")(sa.literal([value], type_=JSONB)))
    kind, array = _sqlite_field(column, tokens, containers=True)
    members = (
        sa.func.json_each(sa.case((kind == "array", array), else_="[]"))
        .table_valued("key", "value", "type", "atom")
        .alias()
    )
    member_kind = sa.case(
        (members.c.type.in_(["integer", "real"]), "number"),
        (members.c.type == "text", "string"),
        else_=members.c.type,
    )
    return sa.exists(
        sa.select(1)
        .select_from(members)
        .where(_comparison((member_kind, members.c.atom, members.c.atom), "eq", value, dialect))
    )


def membership_keys(pointer, value):
    """Fixed-size indexed keys; numbers compare numerically, booleans distinctly."""
    return _member_keys(parts(pointer), value)


def _member_keys(tokens, value):
    if value is None:
        typed = ["null", None]
    elif isinstance(value, bool):
        typed = ["boolean", value]
    elif isinstance(value, (int, float)):
        canonical(value)
        numeric = Decimal(str(value)).normalize()
        typed = ["number", "0" if not numeric else str(numeric)]
    elif isinstance(value, str):
        typed = ["string", value]
    else:
        raise ValidationError("Array membership requires a scalar value")
    return digest(tokens), digest(typed)


def member_keys(value):
    """Index scalar members of every array, including arrays nested in arrays."""
    canonical(value)
    result, pending = set(), [(value, [])]
    while pending:
        node, tokens = pending.pop()
        if isinstance(node, dict):
            pending.extend((item, [*tokens, key]) for key, item in node.items())
        elif isinstance(node, list):
            for index, item in enumerate(node):
                if not isinstance(item, (dict, list)):
                    result.add(_member_keys(tokens, item))
                else:
                    pending.append((item, [*tokens, str(index)]))
    return result


def discovery_predicate(where, metadata, annotations, artifact_id, dialect, members):
    """Compile discovery filters in SQL over explicit metadata/annotations roots."""
    if where is None:
        return sa.true()
    _validate_predicate(where)

    def compile_node(node):
        for operator, combine in (("and", sa.and_), ("or", sa.or_)):
            if operator in node:
                return combine(*(compile_node(child) for child in node[operator]))
        tokens = parts(node["path"])
        if not tokens or tokens[0] not in {"metadata", "annotations"}:
            raise ValidationError("Discovery paths must start with /metadata or /annotations")
        namespace, tokens = tokens[0], tokens[1:]
        pointer = "".join("/" + token.replace("~", "~0").replace("/", "~1") for token in tokens)
        column = metadata if namespace == "metadata" else annotations
        if namespace == "annotations" and node["op"] == "contains":
            if dialect == "sqlite":
                path_key, value_key = membership_keys(pointer, node["value"])
                return artifact_id.in_(
                    sa.select(members.c.artifact_id).where(
                        members.c.path_key == path_key,
                        members.c.value_key == value_key,
                    )
                )
            # SQLite versions differ in how JSON paths handle escaped object keys.
    # Traverse those keys directly instead of relying on path-string escaping.
    if all(
        _array_index(token) is None
        and not any(character in '"\\' or ord(character) < 32 for character in token)
        for token in tokens
    ):
                # Whole-column containment uses GIN; the exact array guard
                # preserves portable array membership semantics.
                pattern = [node["value"]]
                for token in reversed(tokens):
                    pattern = {token: pattern}
                return sa.and_(
                    sa.cast(column, JSONB).op("@>")(sa.literal(pattern, type_=JSONB)),
                    _contains(column, pointer, node["value"], dialect),
                )
        return _compile_predicate({**node, "path": pointer}, column, dialect)

    return compile_node(where)


def _compile_predicate(where, column, dialect):
    for operator, combine in (("and", sa.and_), ("or", sa.or_)):
        if operator in where:
            return combine(
                *(_compile_predicate(child, column, dialect) for child in where[operator])
            )
    if where["op"] == "contains":
        return _contains(column, where["path"], where["value"], dialect)
    field, operation, value = (
        _field(column, where["path"], dialect),
        where["op"],
        where.get("value"),
    )
    if operation == "exists":
        return field[0].is_not(None) if where.get("value", True) else field[0].is_(None)
    if operation == "is_null":
        return field[0] == "null"
    if operation == "in":
        return (
            sa.or_(*(_comparison(field, "eq", item, dialect) for item in value))
            if value
            else sa.false()
        )
    return _comparison(field, operation, value, dialect)


def predicate(where, column, dialect):
    if where is None:
        return sa.true()
    _validate_predicate(where)
    return _compile_predicate(where, column, dialect)


def encode_cursor(payload):
    return base64.urlsafe_b64encode(canonical(payload)).decode().rstrip("=")


def decode_cursor(cursor):
    try:
        if not isinstance(cursor, str) or len(cursor) > 8192:
            raise TypeError()
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(value, dict):
            raise TypeError()
        return value
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValidationError("Invalid cursor") from exc


def fingerprint(where, fields, kind):
    return digest({"where": where, "fields": fields, "kind": kind})

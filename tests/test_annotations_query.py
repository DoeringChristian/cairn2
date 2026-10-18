"""Portable JSON array membership and server-side discovery compilation."""

import json
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from cairn.model import ValidationError
from cairn.query import discovery_predicate, member_keys, membership_keys, predicate


@pytest.fixture(params=["sqlite", "postgresql"])
def tables(request):
    url = "sqlite://"
    if request.param == "postgresql":
        url = os.environ.get("CAIRN_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("PostgreSQL not configured")
        url = url.replace("postgresql://", "postgresql+psycopg://")
    engine = sa.create_engine(url)
    metadata = sa.MetaData()
    suffix = uuid.uuid4().hex[:8]
    artifacts = sa.Table(
        "query_artifacts_" + suffix,
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("metadata", sa.JSON),
        sa.Column("annotations", sa.JSON),
    )
    members = sa.Table(
        "query_members_" + suffix,
        metadata,
        sa.Column("artifact_id", sa.String, primary_key=True),
        sa.Column("path_key", sa.String, primary_key=True),
        sa.Column("value_key", sa.String, primary_key=True),
    )
    sa.Index(
        "query_members_idx_" + suffix,
        members.c.path_key,
        members.c.value_key,
        members.c.artifact_id,
    )
    if request.param == "postgresql":
        sa.Index(
            "query_annotations_gin_" + suffix,
            sa.cast(artifacts.c.annotations, JSONB),
            postgresql_using="gin",
        )
    metadata.create_all(engine)
    try:
        with engine.begin() as conn:
            yield conn, artifacts, members, request.param
    finally:
        metadata.drop_all(engine)
        engine.dispose()


def test_array_membership_typed_and_nested(tables):
    conn, artifacts, _, dialect = tables
    values = [
        {"a": ["paper", 1, True, None], "nested": [{"a": ["x"]}]},
        {"a": "paper"},
        {"a": {"paper": True}},
        {"a": [["paper"]]},
        {"a": [False, "1"]},
        {"a": [1.0]},
    ]
    conn.execute(
        artifacts.insert(),
        [{"id": str(i), "metadata": value, "annotations": {}} for i, value in enumerate(values)],
    )

    def find(path, value):
        condition = predicate(
            {"path": path, "op": "contains", "value": value}, artifacts.c.metadata, dialect
        )
        return set(conn.scalars(sa.select(artifacts.c.id).where(condition)))

    assert find("/a", "paper") == {"0"}
    assert find("/a", 1) == {"0", "5"}
    assert find("/a", True) == {"0"}
    assert find("/a", None) == {"0"}
    assert find("/a", False) == {"4"}
    assert find("/nested/0/a", "x") == {"0"}
    assert find("/missing", "x") == set()


def test_indexed_discovery(tables):
    conn, artifacts, members, dialect = tables
    annotations = {
        "tags": ["paper", "baseline"],
        "escaped/key": {"~x": [1.0]},
        "nested": [["item"]],
    }
    conn.execute(
        artifacts.insert(),
        [
            {"id": "one", "metadata": {"config": {"lr": 0.001}}, "annotations": annotations},
            {"id": "two", "metadata": {"config": {"lr": 0.1}}, "annotations": {"tags": ["other"]}},
        ],
    )
    conn.execute(
        members.insert(),
        [
            {"artifact_id": "one", "path_key": path, "value_key": value}
            for path, value in member_keys(annotations)
        ],
    )

    def query(where):
        return sa.select(artifacts.c.id).where(
            discovery_predicate(
                where,
                artifacts.c.metadata,
                artifacts.c.annotations,
                artifacts.c.id,
                dialect,
                members,
            )
        )

    condition = {
        "and": [
            {"path": "/annotations/tags", "op": "contains", "value": "paper"},
            {"path": "/metadata/config/lr", "op": "lt", "value": 0.01},
        ]
    }
    assert list(conn.scalars(query(condition))) == ["one"]
    for path, value in [("/escaped~1key/~0x", 1), ("/nested/0", "item")]:
        assert list(
            conn.scalars(query({"path": "/annotations" + path, "op": "contains", "value": value}))
        ) == ["one"]
    if dialect == "sqlite":
        for indexed_condition in [condition, condition["and"][0]]:
            sql = str(query(indexed_condition).compile(compile_kwargs={"literal_binds": True}))
            plan = conn.exec_driver_sql("EXPLAIN QUERY PLAN " + sql).fetchall()
            assert any("INDEX" in row[3] and "query_members" in row[3] for row in plan)
            assert any("SEARCH query_artifacts_" in row[3] for row in plan)
            assert not any("SCAN query_artifacts_" in row[3] for row in plan)
    else:
        conn.exec_driver_sql("SET LOCAL enable_seqscan = off")
        compiled = query(condition).compile(dialect=conn.dialect)
        params = {
            key: json.dumps(value) if isinstance(value, (dict, list)) else value
            for key, value in compiled.params.items()
        }
        plan = conn.exec_driver_sql("EXPLAIN " + str(compiled), params).fetchall()
        assert any("query_annotations_gin_" in row[0] for row in plan)
    for bad in ["/tags", "", "/config/lr"]:
        with pytest.raises(ValidationError, match="Discovery paths"):
            query({"path": bad, "op": "eq", "value": 1})


def test_member_keys_normalize_and_escape():
    assert membership_keys("/a", 1) == membership_keys("/a", 1.0)
    assert membership_keys("/a", 0) == membership_keys("/a", -0.0)
    assert membership_keys("/a", True) != membership_keys("/a", 1)
    assert membership_keys("/a", "1") != membership_keys("/a", 1)
    assert member_keys({"a/b": [1], "a": {"b": [2]}}) == {
        membership_keys("/a~1b", 1),
        membership_keys("/a/b", 2),
    }
    with pytest.raises(ValidationError):
        predicate({"path": "/a", "op": "contains", "value": []}, sa.column("value"), "sqlite")

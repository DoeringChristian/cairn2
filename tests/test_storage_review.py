"""Independent contract checks, including cross-connection and HTTP behavior."""

import concurrent.futures
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from test_http import serve

from cairn import model
from cairn.objects import Files
from cairn.ops import check_repository, collect_garbage, export_repository, import_repository
from cairn.storage import Repository, artifacts, blob_refs, blobs
from cairn.transport import HTTPRepository


@pytest.fixture
def backends(tmp_path):
    direct = Repository(tmp_path / "repository")
    try:
        with serve(direct) as url:
            remote = HTTPRepository(url, token="test-token")
            try:
                yield direct, remote
            finally:
                remote.close()
    finally:
        direct.close()


@pytest.fixture
def postgres_backends(tmp_path):
    configured = os.environ.get("CAIRN_TEST_POSTGRES_URL")
    if not configured:
        pytest.skip("Set CAIRN_TEST_POSTGRES_URL for PostgreSQL review checks")
    base_url = sa.make_url(configured)
    administration = sa.create_engine(base_url)
    schema = "review_" + uuid.uuid4().hex
    with administration.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    try:
        url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
        direct = Repository(
            database_url=url.render_as_string(hide_password=False),
            object_store=Files(tmp_path / "objects"),
        )
        try:
            with serve(direct) as address:
                remote = HTTPRepository(address, token="test-token")
                try:
                    yield direct, remote
                finally:
                    remote.close()
        finally:
            direct.close()
    finally:
        with administration.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        administration.dispose()


def start(repository, name="review"):
    created = repository.start("default", "runs", name)
    return created["artifact"]["id"], created["writer"]["id"]


def entries(values, *, first=1, kind="record"):
    return [
        {"sequence": index, "kind": kind, "data": value}
        for index, value in enumerate(values, first)
    ]


def envelope(*, blobs=(), refs=(), data=None, format="review/value"):
    return {
        "$cairn": 1,
        "version": 1,
        "format": format,
        "data": data,
        "blobs": list(blobs),
        "refs": list(refs),
    }


def test_review_typed_scalar_and_array_pointer_queries(backends):
    direct, remote = backends
    revision, writer = start(direct)
    values = [
        {"label": "missing"},
        {"label": "null", "x": None},
        {"label": "false", "x": False},
        {"label": "true", "x": True},
        {"label": "zero", "x": 0},
        {"label": "one", "x": 1},
        {"label": "float", "x": 1.5},
        {"label": "text", "x": "1"},
        {"label": "text-z", "x": "z"},
        {"label": "array", "x": [True, None, 1, {"key": 2}]},
        {"label": "object", "x": {"0": True, "key": 2}},
    ]
    direct.append_batch(revision, writer, entries(values))
    cases = [
        ("/x", "eq", True, ["true"]),
        ("/x", "ne", True, ["false"]),
        ("/x", "eq", False, ["false"]),
        ("/x", "ne", False, ["true"]),
        ("/x", "eq", 1, ["one"]),
        ("/x", "ne", 1, ["zero", "float"]),
        ("/x", "lt", 1, ["zero"]),
        ("/x", "ge", 1, ["one", "float"]),
        ("/x", "eq", "1", ["text"]),
        ("/x", "ne", "1", ["text-z"]),
        ("/x", "eq", None, ["null"]),
        ("/x", "is_null", None, ["null"]),
        ("/x", "exists", False, ["missing"]),
        ("/x", "in", [True, 1, "1"], ["true", "one", "text"]),
        ("/x/0", "eq", True, ["array", "object"]),
        ("/x/1", "is_null", None, ["array"]),
        ("/x/2", "eq", 1, ["array"]),
        ("/x/3/key", "eq", 2, ["array"]),
        ("/x/key", "eq", 2, ["object"]),
    ]
    for backend in (direct, remote):
        for path, operation, value, expected in cases:
            result = backend.query(revision, where={"path": path, "op": operation, "value": value})
            assert [row["data"]["label"] for row in result["items"]] == expected, (
                backend.__class__.__name__,
                path,
                operation,
                value,
            )
        projection = backend.query(revision, fields=["/label", "/x/0", "/x/1"])
        assert projection["items"][9]["data"] == {"/label": "array", "/x/0": True, "/x/1": None}


def test_review_pointer_escaping_matches_projection(backends):
    direct, remote = backends
    revision, writer = start(direct)
    data = {"a/b": {"~key": {'quote"slash\\': 7}}, "": 8, "a.b": 9, "0": 10}
    direct.append_batch(revision, writer, entries([data]))
    for backend in (direct, remote):
        for pointer, expected in [
            ('/a~1b/~0key/quote"slash\\', 7),
            ("/", 8),
            ("/a.b", 9),
            ("/0", 10),
        ]:
            result = backend.query(
                revision, where={"path": pointer, "op": "eq", "value": expected}, fields=[pointer]
            )
            assert [row["data"] for row in result["items"]] == [{pointer: expected}]


def test_review_pointer_indexes_follow_json_pointer_on_objects_and_arrays(backends):
    direct, remote = backends
    revision, writer = start(direct, "pointer-indexes")
    invalid_indexes = ["00", "-1", "+1", " 0", "²", "١", "99999999999999999999999999999999"]
    data = [
        {"label": "array", "x": [7, 7]},
        {"label": "object", "x": dict.fromkeys(["0", "1", *invalid_indexes], 7)},
        {"label": "scalar", "x": 'a string with "quotes"'},
    ]
    direct.append_batch(revision, writer, entries(data))
    for backend in (direct, remote):
        for token in ["0", "1", *invalid_indexes]:
            pointer = f"/x/{token}"
            expected = ["array", "object"] if token in {"0", "1"} else ["object"]
            result = backend.query(revision, where={"path": pointer, "op": "eq", "value": 7})
            assert [row["data"]["label"] for row in result["items"]] == expected
            result = backend.query(revision, fields=[pointer])
            assert result["items"][1]["data"] == {pointer: 7}
            assert result["items"][2]["data"] == {}
            assert result["items"][0]["data"] == ({pointer: 7} if token in {"0", "1"} else {})


def test_review_deep_queries_have_bounded_sql_and_preserve_scalar_types(backends):
    direct, remote = backends
    revision, writer = start(direct, "deep-pointers")
    object_path = "/" + "/".join(["key"] * 16)
    mixed_path = "/" + "/".join(["key", "0"] * 8)
    object_data, mixed_data = False, True
    for _ in range(16):
        object_data = {"key": object_data}
    for _ in range(8):
        mixed_data = {"key": [mixed_data]}
    direct.append_batch(
        revision, writer, entries([object_data, mixed_data, {"key": ["plain string"]}])
    )
    for backend in (direct, remote):
        for path, value, ordinal in [(object_path, False, 1), (mixed_path, True, 2)]:
            result = backend.query(
                revision, where={"path": path, "op": "eq", "value": value}, fields=[path]
            )
            assert [(row["ordinal"], row["data"]) for row in result["items"]] == [
                (ordinal, {path: value})
            ]
        assert (
            backend.query(revision, where={"path": mixed_path, "op": "eq", "value": 1})["items"]
            == []
        )
        assert (
            len(
                backend.query(
                    revision, where={"path": "/key/0/absent", "op": "exists", "value": False}
                )["items"]
            )
            == 3
        )


def test_review_predicates_reject_invalid_and_excessive_conditions(backends):
    direct, remote = backends
    revision, writer = start(direct, "query-validation")
    direct.append_batch(revision, writer, entries([{"x": 1}]))
    leaf = {"path": "/x", "op": "eq", "value": 1}
    malformed = [
        {},
        {"path": "/x"},
        {"op": "eq", "value": 1},
        {"path": "/x", "op": "eq"},
        {"path": "/x", "op": []},
        {"path": "/x", "op": "is_null", "value": False},
        {"path": "/x", "op": "in", "value": {}},
        {"path": "/x", "op": "in", "value": [{}]},
        {"path": "/x", "op": "in", "value": [1] * 101},
        {"path": "/x", "op": "lt", "value": True},
        {"path": "/x", "op": "gt", "value": None},
        {"and": [None]},
        {"and": []},
        {"or": []},
        {"or": leaf},
        {"and": [leaf], "or": [leaf]},
        {"and": [leaf] * 33},
        {"and": [{"or": [leaf] * 32}] * 32},
        *[
            {"path": "/x", "op": "exists", "value": value}
            for value in [None, 0, 1, "false", [], {}]
        ],
    ]
    deep = leaf
    for _ in range(13):
        deep = {"and": [deep]}
    malformed.append(deep)
    for backend in (direct, remote):
        for condition in malformed:
            with pytest.raises(model.ValidationError):
                backend.query(revision, where=condition)
        assert len(backend.query(revision, where={"path": "/x", "op": "exists"})["items"]) == 1
        assert backend.query(revision, where={"path": "/x", "op": "in", "value": []})["items"] == []
        assert len(backend.query(revision, where={"or": [leaf] * 32})["items"]) == 1
        assert (
            len(
                backend.query(
                    revision, where={"path": "/x", "op": "in", "value": list(range(100))}
                )["items"]
            )
            == 1
        )


def test_review_json_strings_and_keys_have_portable_unicode_domain(backends):
    direct, remote = backends
    revision, writer = start(direct, "portable-unicode")
    invalid = ["\x00", "\ud800", "\udfff", "prefix\x00suffix", "prefix\ud800suffix"]
    for value in invalid:
        for data in ({"value": [value]}, {"nested": {value: "key"}}):
            for backend in (direct, remote):
                with pytest.raises(model.ValidationError):
                    backend.append_batch(revision, writer, entries([data]))
            # Exercise server validation independently of the transport's preflight.
            response = httpx.post(
                f"{remote.url}/v1/artifacts/{revision}/writers/{writer}/entries",
                content=json.dumps({"entries": entries([data])}, ensure_ascii=True).encode(),
                headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
            )
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "validation_error"
    assert direct.writer(revision, writer)["acknowledged"] == 0
    valid = {"\U0001f600": {"text": "\U0010ffff café 日本語\n\t", "empty": ""}}
    remote.append_batch(revision, writer, entries([valid]))
    for backend in (direct, remote):
        assert backend.query(revision)["items"][0]["data"] == valid


@pytest.mark.postgres
def test_review_postgres_json_strings_and_keys_have_portable_unicode_domain(postgres_backends):
    test_review_json_strings_and_keys_have_portable_unicode_domain(postgres_backends)


@pytest.mark.postgres
def test_review_postgres_typed_queries_and_escaped_paths(postgres_backends):
    test_review_typed_scalar_and_array_pointer_queries(postgres_backends)
    test_review_pointer_escaping_matches_projection(postgres_backends)
    test_review_pointer_indexes_follow_json_pointer_on_objects_and_arrays(postgres_backends)
    test_review_deep_queries_have_bounded_sql_and_preserve_scalar_types(postgres_backends)
    test_review_predicates_reject_invalid_and_excessive_conditions(postgres_backends)


@pytest.mark.parametrize("second_value", [1, 1.0])
def test_review_finish_intent_preserves_json_types(backends, second_value):
    for index, backend in enumerate(backends):
        revision, writer = start(backend, f"finish-{index}")
        with pytest.raises(model.Pending):
            backend.finish(revision, value=True, request_id=f"finish-{index}-request")
        with pytest.raises(model.Conflict):
            backend.finish(revision, value=second_value)
        backend.close_writer(revision, writer, 0)
        publication = backend.finish(revision, value=True)
        assert publication["manifest"]["value"] is True
        with pytest.raises(model.Conflict):
            backend.finish(revision, value=second_value)


def test_review_concurrent_start_and_finalization_have_one_current(tmp_path):
    path = tmp_path / "repository"
    repositories = [Repository(path) for _ in range(6)]
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            barrier = threading.Barrier(6)

            def same_request(repository):
                barrier.wait(timeout=5)
                return repository.start("default", "runs", "same", request_id="one-start")

            results = list(pool.map(same_request, repositories))
            assert all(result == results[0] for result in results)
            assert len(repositories[0].list_artifacts()["items"]) == 1

            def independent_revision(pair):
                index, repository = pair
                return repository.start("default", "runs", "competing", request_id=f"start-{index}")

            revisions = list(pool.map(independent_revision, enumerate(repositories)))
            assert len({result["artifact"]["id"] for result in revisions}) == 6
            barrier = threading.Barrier(6)

            def finish(pair):
                repository, result = pair
                revision, writer = result["artifact"]["id"], result["writer"]["id"]
                repository.close_writer(revision, writer, 0)
                barrier.wait(timeout=5)
                return repository.finish(revision)

            publications = list(pool.map(finish, zip(repositories, revisions, strict=True)))
        assert sorted(item["number"] for item in publications) == list(range(1, 7))
        assert all(item["state"] == "finished" for item in publications)
        assert repositories[0].resolve("runs/competing")["number"] == 6
        assert len(repositories[0].versions(revisions[0]["artifact"]["id"])) == 6
    finally:
        for repository in repositories:
            repository.close()


def test_review_concurrent_writers_retry_overlap_without_duplicate_ordinals(tmp_path):
    path = tmp_path / "repository"
    repositories = [Repository(path) for _ in range(6)]
    try:
        revision, first_writer = start(repositories[0])
        writers = [first_writer] + [
            repositories[0].register_writer(revision)["id"] for _ in range(5)
        ]
        barrier = threading.Barrier(6)

        def append(pair):
            repository, writer = pair
            data = [{"producer": writer, "number": number} for number in range(1, 51)]
            repository.append_batch(revision, writer, entries(data[:25]))
            barrier.wait(timeout=5)
            return repository.append_batch(revision, writer, entries(data[19:], first=20))

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            acknowledgments = list(pool.map(append, zip(repositories, writers, strict=True)))
        assert {item["acknowledged"] for item in acknowledgments} == {50}
        records = repositories[0].query(revision, limit=1000)["items"]
        assert [record["ordinal"] for record in records] == list(range(1, 301))
        assert len({(record["writer_id"], record["sequence"]) for record in records}) == 300
    finally:
        for repository in repositories:
            repository.close()


def test_review_finished_artifact_freezes_metadata_captures_and_complete_references(backends):
    direct, remote = backends
    upstream, upstream_writer = start(direct, "upstream")
    direct.close_writer(upstream, upstream_writer, 0)
    direct.finish(upstream)
    blob = direct.put_blob(b"retained bytes")
    linked = envelope(blobs=[blob], refs=[{"artifact_id": upstream, "role": "input"}])
    artifact, writer = start(remote, "same-name")
    remote.append_batch(
        artifact,
        writer,
        [
            {"sequence": 1, "kind": "metadata", "data": {"config": {"scale": 1}}},
            {"sequence": 2, "kind": "record", "data": {"nested": [linked]}},
            {"sequence": 3, "kind": "capture", "data": {"name": "best", "value": linked}},
        ],
    )
    remote.close_writer(artifact, writer, 3)
    finished = remote.finish(artifact, metadata={"caption": "first"})
    frozen = model.canonical(finished)
    newer, newer_writer = start(remote, "same-name")
    remote.append_batch(
        newer,
        newer_writer,
        [
            {"sequence": 1, "kind": "metadata", "data": {"config": {"scale": 2}}},
            {"sequence": 2, "kind": "capture", "data": {"name": "best", "value": 9}},
        ],
    )
    remote.close_writer(newer, newer_writer, 2)
    next_finished = remote.finish(newer)
    assert newer != artifact
    assert model.canonical(direct.resolve(artifact)) == frozen
    assert finished["manifest"]["metadata"] == {"config": {"scale": 1}, "caption": "first"}
    assert finished["manifest"]["captures"] == {"best": linked}
    assert finished["manifest"]["blobs"] == [blob]
    assert finished["manifest"]["dependencies"] == [{"artifact_id": upstream, "role": "input"}]
    assert next_finished["manifest"]["captures"] == {"best": 9}
    assert len(remote.query(artifact)["items"]) == 3
    assert len(remote.query(newer)["items"]) == 2
    with pytest.raises(model.Conflict):
        remote.append_batch(artifact, writer, entries([{"late": True}], first=4))


def test_review_reference_failure_rolls_back_batch_and_literal_data_is_opaque(backends):
    direct, remote = backends
    revision, writer = start(remote)
    missing_blob = {"digest": "f" * 64, "size": 1}
    with pytest.raises(model.IntegrityError):
        remote.append_batch(
            revision,
            writer,
            entries(
                [
                    {"accepted": "only if entire transaction succeeds"},
                    {"nested": {"values": [envelope(blobs=[missing_blob])]}},
                ]
            ),
        )
    assert direct.writer(revision, writer)["acknowledged"] == 0
    assert direct.query(revision)["cutoff"] == 0
    with pytest.raises(model.NotFound):
        direct.append_batch(
            revision,
            writer,
            entries(
                [
                    {"nested": envelope(refs=[{"artifact_id": "missing-version"}])},
                ]
            ),
        )
    literal = envelope(format="json/literal", data={"$cairn": 99, "blobs": [missing_blob]})
    remote.append_batch(revision, writer, entries([{"literal": literal}]))
    remote.close_writer(revision, writer, 1)
    version = remote.finish(revision)
    assert version["manifest"]["blobs"] == []
    assert version["manifest"]["dependencies"] == []


def test_review_upload_is_invisible_until_object_commit(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class PausingFiles(Files):
        def put(self, key, source, *, digest, size):
            entered.set()
            assert release.wait(timeout=5)
            return super().put(key, source, digest=digest, size=size)

    repository = Repository(
        tmp_path / "repository", object_store=PausingFiles(tmp_path / "objects")
    )
    payload = b"reserved content"
    sha = hashlib.sha256(payload).hexdigest()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(repository.put_blob, payload)
            try:
                assert entered.wait(timeout=5)
                assert repository.has_blobs([sha]) == {sha: False}
                with pytest.raises(model.Pending):
                    repository.put_blob(payload)
                revision, writer = start(repository)
                batch = entries(
                    [{"value": envelope(blobs=[{"digest": sha, "size": len(payload)}])}]
                )
                with pytest.raises(model.IntegrityError):
                    repository.append_batch(revision, writer, batch)
            finally:
                release.set()
            assert pending.result(timeout=5) == {"digest": sha, "size": len(payload)}
        assert repository.has_blobs([sha]) == {sha: True}
        assert repository.append_batch(revision, writer, batch)["acknowledged"] == 1
    finally:
        repository.close()


def test_review_gc_preserves_active_local_upload_staging(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    replace = os.replace

    def paused_replace(source, destination):
        if Path(source).name.startswith(".upload-"):
            entered.set()
            assert release.wait(timeout=5)
        return replace(source, destination)

    repository = Repository(tmp_path / "repository")
    try:
        monkeypatch.setattr("cairn.objects.os.replace", paused_replace)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(repository.put_blob, b"upload protected by a live reservation")
            try:
                assert entered.wait(timeout=5)
                result = collect_garbage(repository, grace_seconds=0, dry_run=False)
            finally:
                release.set()
            stored = pending.result(timeout=5)
        assert result["orphans"] == []
        assert repository.has_blobs([stored["digest"]]) == {stored["digest"]: True}
    finally:
        repository.close()


@pytest.mark.postgres
def test_review_postgres_gc_rechecks_refs_after_waiting_for_append(postgres_backends):
    repository, _ = postgres_backends
    payload = b"record retained while cleanup waits for its blob lock"
    blob = repository.put_blob(payload)
    with repository._transaction(write=True) as connection:
        connection.execute(blobs.update().values(lease_until=0))
    revision, writer = start(repository)
    paused, release, gc_started = threading.Event(), threading.Event(), threading.Event()
    state = {}

    def before_execute(connection, cursor, statement, parameters, context, executemany):
        if "INSERT INTO cairn_records" in statement:
            paused.set()
            assert release.wait(timeout=8)
        if "FROM cairn_blobs" in statement and "FOR UPDATE" in statement and "EXISTS" in statement:
            state["pid"] = connection.connection.driver_connection.info.backend_pid
            gc_started.set()

    sa.event.listen(repository.engine, "before_cursor_execute", before_execute)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            append = pool.submit(
                repository.append_batch,
                revision,
                writer,
                entries([{"value": envelope(blobs=[blob])}]),
            )
            try:
                assert paused.wait(timeout=5)
                cleanup = pool.submit(collect_garbage, repository, grace_seconds=0, dry_run=False)
                assert gc_started.wait(timeout=5)
                deadline = time.monotonic() + 4
                waiting = None
                while time.monotonic() < deadline:
                    with repository.engine.connect() as connection:
                        waiting = connection.execute(
                            sa.text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),
                            state,
                        ).scalar()
                    if waiting == "Lock":
                        break
                    time.sleep(0.01)
                assert waiting == "Lock"
            finally:
                release.set()
            assert append.result(timeout=5)["acknowledged"] == 1
            assert cleanup.result(timeout=5)["removed"] == []
        assert repository.has_blobs([blob["digest"]]) == {blob["digest"]: True}
        with repository.open_blob(blob["digest"]) as stream:
            assert stream.read() == payload
    finally:
        release.set()
        sa.event.remove(repository.engine, "before_cursor_execute", before_execute)


@pytest.mark.parametrize("dependency_kind", ["blob", "typed-ref", "dependency-entry"])
def test_review_import_rejects_missing_record_retention(tmp_path, dependency_kind):
    source, destination = Repository(tmp_path / "source"), Repository(tmp_path / "destination")
    try:
        dependency_revision, dependency_writer = start(source, "upstream")
        source.close_writer(dependency_revision, dependency_writer, 0)
        upstream = source.finish(dependency_revision)["id"]
        revision, writer = start(source, "evidence")
        if dependency_kind == "blob":
            content = source.put_blob(b"a record's retained evidence")
            batch = entries([{"value": envelope(blobs=[content])}])
            index_table, manifest_field = "cairn_blob_refs", "blobs"
        else:
            reference = {"artifact_id": upstream, "role": "input"}
            if dependency_kind == "typed-ref":
                batch = entries([{"value": envelope(refs=[reference])}])
            else:
                batch = entries([reference], kind="dependency")
            index_table, manifest_field = "cairn_artifact_refs", "dependencies"
        source.append_batch(revision, writer, batch)
        source.close_writer(revision, writer, 1)
        version = source.finish(revision)["id"]
        bundle = export_repository(source, tmp_path / "bundle")
        manifest = json.loads((bundle / "repository.json").read_text())
        for table in ("cairn_artifacts", index_table):
            entry = manifest["tables"][table]
            path = bundle / entry["file"]
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            if table == "cairn_artifacts":
                for row in rows:
                    if row["id"] == version:
                        row["manifest"][manifest_field] = []
            else:
                rows = [row for row in rows if row["owner"] not in {revision, version}]
            data = b"".join(model.canonical(row) + b"\n" for row in rows)
            path.write_bytes(data)
            entry.update(rows=len(rows), sha256=hashlib.sha256(data).hexdigest())
        (bundle / "repository.json").write_bytes(model.canonical(manifest))
        with pytest.raises(model.IntegrityError):
            import_repository(destination, bundle)
        assert destination.list_artifacts()["items"] == []
    finally:
        source.close()
        destination.close()


def test_review_check_detects_missing_record_manifest_dependency(backends):
    repository, _ = backends
    content = repository.put_blob(b"retained evidence")
    revision, writer = start(repository)
    repository.append_batch(revision, writer, entries([{"value": envelope(blobs=[content])}]))
    repository.close_writer(revision, writer, 1)
    version = repository.finish(revision)
    broken = {**version["manifest"], "blobs": []}
    with repository._transaction(write=True) as connection:
        connection.execute(
            artifacts.update().where(artifacts.c.id == version["id"]).values(manifest=broken)
        )
        connection.execute(blob_refs.delete().where(blob_refs.c.owner == version["id"]))
    assert not check_repository(repository, verify_blobs=True)["ok"]


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda backend, revision, writer: backend.has_blobs("not-a-list"), id="blob-list"
        ),
        pytest.param(
            lambda backend, revision, writer: backend.resolve_many("not-a-list"), id="ref-list"
        ),
        pytest.param(
            lambda backend, revision, writer: backend.append_batch(revision, writer, {}),
            id="entry-list",
        ),
        pytest.param(
            lambda backend, revision, writer: backend.query(revision, kind=["record"]),
            id="record-kind",
        ),
        pytest.param(
            lambda backend, revision, writer: backend.finish(revision, outcome=["completed"]),
            id="outcome",
        ),
        pytest.param(
            lambda backend, revision, writer: backend.set_alias(revision, "best", expected_id=True),
            id="expected-id",
        ),
    ],
)
def test_review_invalid_parameter_types_have_local_http_error_parity(backends, operation):
    errors = []
    for index, backend in enumerate(backends):
        revision, writer = start(backend, f"validation-{index}")
        try:
            operation(backend, revision, writer)
        except (model.CairnError, TypeError) as error:
            errors.append(type(error))
        else:
            errors.append(None)
    assert errors == [model.ValidationError, model.ValidationError]


@pytest.mark.parametrize("backend_fixture", ["backends", "postgres_backends"])
def test_publication_capacity_is_rejected_during_append_atomically(
    request, backend_fixture, monkeypatch
):
    from cairn import storage

    backends = request.getfixturevalue(backend_fixture)
    # Small bound exercises the same accounting without a huge fixture.
    monkeypatch.setattr(storage, "PUBLICATION_COLLECTION_LIMIT", 300)
    for backend in backends:
        started = backend.start("capacity", "artifacts", uuid.uuid4().hex)
        revision, writer = started["artifact"]["id"], started["writer"]["id"]
        entry = {"sequence": 1, "kind": "capture", "data": {"name": "a", "value": "x" * 250}}
        backend.append_batch(revision, writer, [entry])
        used = backend.resolve(revision)["publication_bytes"]
        backend.append_batch(revision, writer, [entry])
        assert backend.resolve(revision)["publication_bytes"] == used
        with pytest.raises(model.CapacityError):
            backend.append_batch(
                revision,
                writer,
                [
                    {"sequence": 2, "kind": "metadata", "data": {"leak": True}},
                    {"sequence": 3, "kind": "capture", "data": {"name": "b", "value": "y" * 100}},
                ],
            )
        live = backend.resolve(revision)
        assert live["cutoff"] == 1 and live["metadata"] == {}
        assert live["publication_bytes"] == used
        # Plain records do not grow the publication collections.
        backend.append_batch(
            revision, writer, [{"sequence": 2, "kind": "record", "data": {"text": "z" * 1000}}]
        )
        backend.close_writer(revision, writer, 2)
        result = backend.finish(revision)
        assert result["manifest"]["captures"] == {"a": "x" * 250}


def test_publication_invalid_inputs_do_not_close_revision(backends, monkeypatch):
    from cairn import storage

    monkeypatch.setattr(storage, "PUBLICATION_VALUE_LIMIT", 64)
    for backend in backends:
        started = backend.start("capacity", "runs", uuid.uuid4().hex)
        revision, writer = started["artifact"]["id"], started["writer"]["id"]
        with pytest.raises(model.CapacityError):
            backend.finish(revision, value="x" * 100)
        assert backend.resolve(revision)["state"] == "live"
        assert backend.resolve(revision)["intent"] is None
        backend.append_batch(
            revision, writer, [{"sequence": 1, "kind": "record", "data": {"ok": 1}}]
        )
        backend.close_writer(revision, writer, 1)
        assert backend.finish(revision, value="valid")["state"] == "finished"


def test_blob_descriptor_capacity_counts_unique_content_and_rejects_finish_extras(
    backends, monkeypatch
):
    from cairn import storage

    monkeypatch.setattr(storage, "PUBLICATION_COLLECTION_LIMIT", 160)
    for backend in backends:
        first, second = backend.put_blob(b"first"), backend.put_blob(b"second")
        typed = lambda blob: {"$cairn": 1, "version": 1, "format": "file", "blobs": [blob]}
        started = backend.start("capacity", "artifacts", uuid.uuid4().hex)
        revision, writer = started["artifact"]["id"], started["writer"]["id"]
        for sequence in (1, 2):
            backend.append_batch(
                revision,
                writer,
                [{"sequence": sequence, "kind": "record", "data": {"file": typed(first)}}],
            )
        used = backend.resolve(revision)["publication_bytes"]
        assert used < 160
        with pytest.raises(model.CapacityError):
            backend.finish(revision, metadata={"file": typed(second)})
        assert backend.resolve(revision)["state"] == "live"
        assert backend.resolve(revision)["publication_bytes"] == used
        backend.close_writer(revision, writer, 2)
        assert backend.finish(revision)["manifest"]["blobs"] == [first]


def test_integrity_reconstructs_publication_capacity(tmp_path):

    repository = Repository(tmp_path / "capacity-integrity")
    try:
        started = repository.start("test", "artifacts", "captures")
        revision, writer = started["artifact"]["id"], started["writer"]["id"]
        repository.append_batch(
            revision,
            writer,
            [
                {"sequence": 1, "kind": "capture", "data": {"name": "example", "value": 42}},
            ],
        )
        assert check_repository(repository)["ok"]
        with repository._transaction(write=True) as conn:
            conn.execute(
                artifacts.update().where(artifacts.c.id == revision).values(publication_bytes=0)
            )
        result = check_repository(repository)
        assert not result["ok"]
        assert "capacity counter" in str(result)
    finally:
        repository.close()

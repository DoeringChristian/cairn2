import contextlib
import hashlib
import io
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from cairn import model
from cairn.server import create_app
from cairn.transport import CHUNK_SIZE, HTTPRepository


@contextlib.contextmanager
def serve(repository, **options):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(repository, token="test-token", **options), log_level="error", lifespan="off"
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("Test HTTP server did not start")
            time.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()


@pytest.fixture
def repositories(tmp_path):
    from cairn.storage import Repository

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


def test_real_http_contract_and_publication(repositories):
    direct, remote = repositories
    assert remote.info() == direct.info()
    assert remote.capabilities()["protocol_version"] == model.PROTOCOL_VERSION == 4
    started = remote.start("test", "runs", "experiment", request_id="start")
    assert remote.start("test", "runs", "experiment", request_id="start") == started
    assert started["artifact"]["namespace"] == "runs"
    assert "type" not in started["artifact"]
    artifact = started["artifact"]["id"]
    revision = started["artifact"]["id"]
    assert remote.resolve(revision)["namespace"] == "runs"
    writer = started["writer"]["id"]
    other = remote.register_writer(revision, request_id="writer-2")
    assert remote.register_writer(revision, request_id="writer-2") == other
    assert remote.writer(revision, writer) == direct.writer(revision, writer)
    entries = [
        {"sequence": 1, "kind": "metadata", "data": {"config": {"lr": 0.01}}},
        {"sequence": 2, "kind": "record", "data": {"loss": 2.0, "epoch": 1}},
        {"sequence": 3, "kind": "record", "data": {"loss": 1.0, "epoch": 2}},
        {"sequence": 4, "kind": "capture", "data": {"name": "best", "value": 1.0}},
    ]
    assert remote.append_batch(revision, writer, entries)["acknowledged"] == 4
    assert remote.append_batch(revision, writer, entries)["acknowledged"] == 4
    exact = artifact
    assert remote.resolve(exact) == direct.resolve(exact)
    assert remote.resolve_many([exact, revision]) == direct.resolve_many([exact, revision])
    query = {
        "where": {"path": "/loss", "op": "lt", "value": 2},
        "fields": ["/loss"],
        "limit": 1,
        "kind": "record",
    }
    assert remote.query(exact, **query) == direct.query(exact, **query)
    first = remote.query(exact, limit=1, kind="record")
    assert first["next_cursor"]
    assert remote.query(exact, limit=1, kind="record", cursor=first["next_cursor"]) == direct.query(
        exact, limit=1, kind="record", cursor=first["next_cursor"]
    )
    with pytest.raises(model.Pending) as caught:
        remote.finish(revision, value={"result": 1.0}, request_id="finish")
    assert caught.value.details
    remote.close_writer(revision, writer, 4)
    remote.close_writer(revision, other["id"], 0)
    final = remote.finish(revision, value={"result": 1.0}, request_id="finish")
    assert final == remote.finish(revision, value={"result": 1.0}, request_id="finish")
    version = final["id"]
    assert remote.resolve("runs/experiment", project="test")["id"] == version
    assert remote.resolve("runs/experiment@latest", project="test")["id"] == version
    assert remote.resolve("runs/experiment@v1", project="test")["id"] == version
    assert final["namespace"] == "runs" and "type" not in final
    assert remote.versions(artifact) == direct.versions(artifact)
    assert remote.list_artifacts(
        project="test", namespace="runs", limit=1
    ) == direct.list_artifacts(project="test", namespace="runs", limit=1)
    remote.set_alias(artifact, "best", request_id="alias")
    assert remote.resolve("runs/experiment@best", project="test")["id"] == exact
    remote.set_alias(artifact, "latest", expected_id=version, request_id="select")
    assert remote.resolve(artifact)["id"] == exact
    comment = remote.add_comment(
        exact, {"path": "report.qmd", "line": 2}, "Checked", request_id="comment"
    )
    assert remote.comments(exact) == direct.comments(exact) == [comment]
    assert remote.delete_comment(comment["id"]) is None
    assert remote.comments(exact) == []
    assert remote.delete(artifact, request_id="delete") == {"id": artifact, "deleted": True}
    assert remote.list_artifacts(project="test")["items"] == []
    assert remote.resolve(exact)["id"] == exact


def test_remote_error_codes_and_conflicts(repositories):
    _, remote = repositories
    with pytest.raises(model.NotFound):
        remote.resolve("missing-version")
    created = remote.start("default", "runs", "conflicts")
    revision, writer = created["artifact"]["id"], created["writer"]["id"]
    remote.append_batch(revision, writer, [{"sequence": 1, "kind": "record", "data": {"x": 1}}])
    with pytest.raises(model.Conflict):
        remote.append_batch(revision, writer, [{"sequence": 1, "kind": "record", "data": {"x": 2}}])
    with pytest.raises(model.ValidationError):
        remote.query(revision, where={"path": "/x", "op": "sql", "value": "bad"})
    with pytest.raises(model.IntegrityError):
        remote.put_blob(b"value", digest="0" * 64)


def test_http_arbitrary_namespaces_nested_names_and_legacy_rejection(repositories):
    direct, remote = repositories
    started = remote.start(project="test", namespace="checkpoints", name="vision/best")
    revision, writer = started["artifact"]["id"], started["writer"]["id"]
    data = {"type": "user-field", "namespace": "also-user-data"}
    remote.append_batch(revision, writer, [{"sequence": 1, "kind": "record", "data": data}])
    assert remote.resolve("checkpoints/vision/best@live", project="test")["id"] == revision
    remote.close_writer(revision, writer, 1)
    version = remote.finish(revision)["id"]
    for backend in (direct, remote):
        assert backend.resolve("checkpoints/vision/best@v1", project="test")["id"] == version
        assert backend.resolve("checkpoints/vision/best@latest", project="test")["id"] == version
        assert backend.query(version)["items"][0]["data"] == data
        assert backend.list_artifacts(namespace="runs")["items"] == []
        with pytest.raises(model.NotFound):
            backend.resolve("checkpoints:vision/best@latest", project="test")
    response = httpx.post(
        f"{remote.url}/v1/artifacts",
        json={"project": "test", "type": "run", "name": "legacy"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == model.ValidationError.code
    response = httpx.get(
        f"{remote.url}/v1/artifacts",
        params={"type": "run"},
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == model.ValidationError.code


def test_blob_upload_and_download_stream_bounded_chunks(repositories, tmp_path):
    direct, remote = repositories
    payload = b"a" * CHUNK_SIZE
    repetitions = 17
    expected = hashlib.sha256()
    for _ in range(repetitions):
        expected.update(payload)

    class GeneratedFile:
        remaining = repetitions
        reads = 0

        def read(self, size):
            assert 0 < size <= CHUNK_SIZE
            self.reads += 1
            if not self.remaining:
                return b""
            self.remaining -= 1
            return payload

    source = GeneratedFile()
    uploaded = remote.put_blob(source, digest=expected.hexdigest(), size=repetitions * CHUNK_SIZE)
    assert source.reads == repetitions + 1
    assert uploaded == {"digest": expected.hexdigest(), "size": repetitions * CHUNK_SIZE}
    assert remote.has_blobs([uploaded["digest"], "0" * 64]) == {
        uploaded["digest"]: True,
        "0" * 64: False,
    }
    actual = hashlib.sha256()
    with remote.open_blob(uploaded["digest"]) as file:
        assert file.seekable()
        while chunk := file.read(CHUNK_SIZE):
            actual.update(chunk)
    assert file.closed
    assert actual.hexdigest() == expected.hexdigest()
    path = tmp_path / "blob"
    path.write_bytes(b"short")
    assert remote.put_blob(path) == remote.put_blob(b"short")
    borrowed = io.BytesIO(b"short")
    assert remote.put_blob(borrowed) == direct.put_blob(b"short")
    assert not borrowed.closed
    with pytest.raises(model.NotFound), remote.open_blob("0" * 64):
        pass


def test_http_auth_discovery_and_missing_token(repositories, monkeypatch, tmp_path):
    _, remote = repositories
    monkeypatch.delenv("CAIRN_TOKEN", raising=False)
    monkeypatch.delenv("CAIRN_TOKEN_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    missing = HTTPRepository(remote.url)
    try:
        with pytest.raises(model.Unauthorized):
            missing.info()
    finally:
        missing.close()
    monkeypatch.setenv("CAIRN_TOKEN", "test-token")
    discovered = HTTPRepository(remote.url)
    try:
        assert discovered.info() == remote.info()
    finally:
        discovered.close()


def test_transport_timeout_scope():
    class SlowRepository:
        def info(self):
            time.sleep(0.15)
            return {"ok": True, "protocol_version": model.PROTOCOL_VERSION}

    with serve(SlowRepository()) as url:
        remote = HTTPRepository(url, token="test-token", timeout=2)
        try:
            with remote.request_timeout(0.02), pytest.raises(httpx.ReadTimeout):
                remote.info()
            assert remote.info() == {"ok": True, "protocol_version": model.PROTOCOL_VERSION}
        finally:
            remote.close()


def test_transport_rejects_incompatible_server_protocol_over_real_http():
    class VersionedRepository:
        protocol = 1

        def info(self):
            return {"protocol_version": self.protocol, "schema_version": 999}

    repository = VersionedRepository()
    with serve(repository) as url:
        remote = HTTPRepository(url, token="test-token")
        try:
            for protocol in (1, 2, 3, None, "4", 4.0):
                repository.protocol = protocol
                with pytest.raises(model.Unsupported, match="client requires 4"):
                    remote.info()
            repository.protocol = model.PROTOCOL_VERSION
            assert remote.info() == {
                "protocol_version": model.PROTOCOL_VERSION,
                "schema_version": 999,
            }
        finally:
            remote.close()


def test_all_shared_errors_survive_real_http():
    class ErrorRepository:
        error = None

        def info(self):
            raise self.error("test failure", details={"evidence": "kept"})

    repository = ErrorRepository()
    with serve(repository) as url:
        remote = HTTPRepository(url, token="test-token")
        try:
            for error in (
                model.ValidationError,
                model.NotFound,
                model.Conflict,
                model.NotPublished,
                model.Pending,
                model.Unauthorized,
                model.IntegrityError,
                model.Unsupported,
                model.CapacityError,
            ):
                repository.error = error
                with pytest.raises(error) as caught:
                    remote.info()
                assert caught.value.details == {"evidence": "kept"}
        finally:
            remote.close()


def test_real_chunked_requests_have_body_limits():
    with (
        serve(object(), max_json_bytes=32, max_blob_bytes=8) as url,
        httpx.Client(base_url=url, headers={"Authorization": "Bearer test-token"}) as client,
    ):
        response = client.post("/v1/artifacts", content=iter([b"x" * 16, b"x" * 17]))
        assert response.status_code == 413
        response = client.put("/v1/blobs", content=iter([b"1234", b"56789"]))
        assert response.status_code == 413


def test_same_name_artifacts_keep_ids_and_aliases_only_select(repositories):
    _, remote = repositories
    first = remote.start("study", "runs", "training")
    second = remote.start("study", "runs", "training")
    first_id, second_id = first["artifact"]["id"], second["artifact"]["id"]
    assert first_id != second_id
    for handle in (first, second):
        artifact_id = handle["artifact"]["id"]
        assert remote.resolve(artifact_id[:8])["state"] == "live"
        remote.close_writer(artifact_id, handle["writer"]["id"], 0)
        assert remote.finish(artifact_id)["id"] == artifact_id
        assert remote.resolve(artifact_id[:8])["state"] == "finished"
    assert remote.resolve("runs/training@latest", project="study")["id"] == second_id
    remote.set_alias(first_id, "latest", expected_id=second_id)
    assert remote.resolve("runs/training@latest", project="study")["id"] == first_id
    assert remote.resolve(second_id)["id"] == second_id
    with pytest.raises(model.Conflict):
        remote.set_alias(second_id, "latest", expected_id=second_id)
    assert {item["id"] for item in remote.versions("runs/training", project="study")} == {
        first_id,
        second_id,
    }
    assert remote.resolve("runs/training@v1", project="study")["id"] == first_id
    assert remote.resolve("runs/training@v2", project="study")["id"] == second_id
    for path in ("/v1/revisions", f"/v1/artifacts/{first_id}/snapshots"):
        response = httpx.post(
            remote.url + path, json={}, headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 404


def test_http_artifact_metadata_annotations_and_finished_updates(repositories):
    direct, remote = repositories
    opened = remote.start("study", "runs", "training")
    artifact, writer = opened["artifact"]["id"], opened["writer"]["id"]
    remote.append_batch(
        artifact, writer, [{"sequence": 1, "kind": "metadata", "data": {"config": {"lr": 0.1}}}]
    )
    remote.annotate(artifact, {"tags": ["baseline", "reviewed"]}, request_id="annotate-live")
    remote.start("study", "runs", "other")
    filters = {
        "project": "study",
        "namespace": "runs",
        "where": {
            "and": [
                {"path": "/metadata/config/lr", "op": "lt", "value": 0.2},
                {"path": "/annotations/tags", "op": "contains", "value": "reviewed"},
            ]
        },
        "state": "live",
    }
    assert remote.list_artifacts(**filters) == direct.list_artifacts(**filters)
    assert [item["id"] for item in remote.list_artifacts(**filters)["items"]] == [artifact]
    remote.close_writer(artifact, writer, 1)
    finished = remote.finish(artifact)
    updated = remote.annotate(
        artifact, {"tags": ["accepted", "baseline"]}, request_id="annotate-final"
    )
    assert updated["annotations"]["tags"] == ["accepted", "baseline"]
    assert (
        remote.annotate(artifact, {"tags": ["accepted", "baseline"]}, request_id="annotate-final")
        == updated
    )
    assert updated["manifest"] == finished["manifest"]
    filters.update(
        where={"path": "/annotations/tags", "op": "contains", "value": "accepted"}, state="finished"
    )
    assert remote.list_artifacts(**filters)["items"][0]["id"] == artifact
    assert (
        remote.list_artifacts(
            where={"path": "/annotations/tags", "op": "contains", "value": "reviewed"}
        )["items"]
        == []
    )


@pytest.mark.parametrize(
    "params",
    [
        [("where", "not-json")],
        [("where", "[]")],
        [("state", "bad")],
        [("tag", "paper")],
        [("project", "study"), ("project", "other")],
        [("where", "{}"), ("where", "{}")],
    ],
)
def test_http_discovery_rejects_malformed_filters(repositories, params):
    _, remote = repositories
    response = httpx.get(
        f"{remote.url}/v1/artifacts", params=params, headers={"Authorization": "Bearer test-token"}
    )
    assert response.status_code == 400

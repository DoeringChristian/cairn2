"""Conformance through real HTTP, PostgreSQL and an HTTP S3 emulator."""

import contextlib
import logging
import math
import os
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import boto3
import psycopg
import pytest
import sqlalchemy as sa
import uvicorn
from moto.server import ThreadedMotoServer
from psycopg import sql

from cairn.model import CapacityError, Conflict, IntegrityError, Pending
from cairn.objects import S3, Files
from cairn.ops import check_repository, export_repository, import_repository
from cairn.server import create_app
from cairn.storage import Repository
from cairn.transport import HTTPRepository


@pytest.fixture(scope="module")
def s3_endpoint():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.stop()


@pytest.fixture(params=["sqlite-files", "postgres-files", "postgres-s3"])
def store(request, tmp_path, monkeypatch, s3_endpoint):
    if request.param == "sqlite-files":
        repository = Repository(tmp_path / "repo")
        try:
            yield repository
        finally:
            repository.close()
        return
    base = os.environ.get("CAIRN_TEST_POSTGRES_URL")
    if not base:
        pytest.skip("Set CAIRN_TEST_POSTGRES_URL to an expendable PostgreSQL admin database")
    database = "cairn2_test_" + uuid.uuid4().hex
    connection_string = base.replace("postgresql+psycopg:", "postgresql:")
    with psycopg.connect(connection_string, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    try:
        if request.param == "postgres-s3":
            monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
            monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
            monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
            bucket = "cairn2-" + uuid.uuid4().hex
            boto3.client("s3", endpoint_url=s3_endpoint).create_bucket(Bucket=bucket)
            objects = S3(bucket, endpoint_url=s3_endpoint, region="us-east-1")
        else:
            objects = Files(tmp_path / "objects")
        url = sa.engine.make_url(base).set(database=database).render_as_string(hide_password=False)
        repository = Repository(database_url=url, object_store=objects)
        try:
            yield repository
        finally:
            repository.close()
    finally:
        with psycopg.connect(connection_string, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))


@contextlib.contextmanager
def http_backend(repository):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(
        uvicorn.Config(create_app(repository, token="conformance-token"), log_level="error")
    )
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline
        time.sleep(0.01)
    backend = HTTPRepository(
        f"http://127.0.0.1:{listener.getsockname()[1]}", token="conformance-token"
    )
    try:
        yield backend
    finally:
        backend.close()
        server.should_exit = True
        thread.join(5)
        listener.close()


@pytest.fixture(params=["direct", "http"])
def backend(request, store):
    if request.param == "direct":
        yield store
    else:
        with http_backend(store) as client:
            yield client


def test_all_backends_lifecycle_content_and_queries(backend):
    started = backend.start("study", "evaluation", "trial", request_id="create")
    assert backend.start("study", "evaluation", "trial", request_id="create") == started
    revision, writer = started["artifact"]["id"], started["writer"]["id"]
    blob = backend.put_blob(b"\x00portable\xff")
    value = {
        "$cairn": 1,
        "format": "future/evaluation",
        "version": 1,
        "data": {"label": "all generic"},
        "blobs": [blob],
        "refs": [],
    }
    entries = [
        {"sequence": 1, "kind": "metadata", "data": {"config": {"lr": 0.01}}},
        {"sequence": 2, "kind": "record", "data": {"loss": 0.4, "truth": True, "items": [False]}},
        {
            "sequence": 3,
            "kind": "record",
            "data": {"loss": None, "truth": 1, "items": {"0": False}},
        },
        {"sequence": 4, "kind": "capture", "data": {"name": "evidence", "value": value}},
    ]
    assert backend.append_batch(revision, writer, entries)["acknowledged"] == 4
    assert backend.append_batch(revision, writer, entries)["cutoff"] == 4
    assert (
        len(backend.query(revision, where={"path": "/truth", "op": "eq", "value": True})["items"])
        == 1
    )
    assert (
        len(backend.query(revision, where={"path": "/truth", "op": "eq", "value": 1})["items"]) == 1
    )
    assert (
        len(
            backend.query(revision, where={"path": "/items/0", "op": "eq", "value": False})["items"]
        )
        == 2
    )
    assert len(backend.query(revision, where={"path": "/loss", "op": "is_null"})["items"]) == 1
    page = backend.query(revision, limit=1)
    backend.append_batch(
        revision, writer, [{"sequence": 5, "kind": "record", "data": {"later": 1}}]
    )
    old_items = list(page["items"])
    while page["next_cursor"]:
        page = backend.query(revision, limit=1, cursor=page["next_cursor"])
        old_items.extend(page["items"])
    assert len(old_items) == 4
    backend.close_writer(revision, writer, 5)
    result = backend.finish(revision, value=value, request_id="finish")
    assert result["id"] == revision
    assert backend.resolve("evaluation/trial@v1", project="study")["id"] == result["id"]
    assert backend.resolve("evaluation/trial@latest", project="study")["id"] == result["id"]
    with backend.open_blob(blob["digest"]) as stream:
        assert stream.read() == b"\x00portable\xff"
    assert backend.query(result["id"])["cutoff"] == 5


def test_all_backends_failed_batches_and_multiwriter_completion(backend):
    first = backend.start("study", "run", "trial")
    revision, writer = first["artifact"]["id"], first["writer"]["id"]
    other = backend.register_writer(revision, request_id="other")
    assert backend.register_writer(revision, request_id="other") == other
    with pytest.raises(Conflict):
        backend.append_batch(revision, writer, [{"sequence": 2, "kind": "record", "data": {}}])
    bad_value = {
        "$cairn": 1,
        "format": "file",
        "version": 1,
        "data": None,
        "blobs": [{"digest": "a" * 64, "size": 1}],
        "refs": [],
    }
    with pytest.raises(IntegrityError):
        backend.append_batch(
            revision,
            writer,
            [
                {"sequence": 1, "kind": "metadata", "data": {"should_not_exist": True}},
                {"sequence": 2, "kind": "record", "data": {"bad": bad_value}},
            ],
        )
    assert backend.resolve(revision)["metadata"] == {}
    assert backend.writer(revision, writer)["acknowledged"] == 0
    backend.close_writer(revision, writer, 0)
    with pytest.raises(Pending):
        backend.finish(revision, value=True)
    with pytest.raises(Conflict):
        backend.finish(revision, value=1)
    backend.close_writer(revision, other["id"], 0)
    assert backend.finish(revision, value=True)["manifest"]["value"] is True


def test_cross_storage_transfer_preserves_versions_and_evidence(store, tmp_path):
    source = Repository(tmp_path / "source")
    try:
        blob = source.put_blob(b"data")
        value = {
            "$cairn": 1,
            "format": "file",
            "version": 1,
            "data": {},
            "blobs": [blob],
            "refs": [],
        }
        source_run = source.start("p", "dataset", "input")
        source.close_writer(source_run["artifact"]["id"], source_run["writer"]["id"], 0)
        data = source.finish(source_run["artifact"]["id"], value=value)
        report = source.start("p", "report", "summary")
        source.append_batch(
            report["artifact"]["id"],
            report["writer"]["id"],
            [
                {
                    "sequence": 1,
                    "kind": "dependency",
                    "data": {"artifact_id": data["id"], "role": "input"},
                }
            ],
        )
        source.close_writer(report["artifact"]["id"], report["writer"]["id"], 1)
        report_version = source.finish(report["artifact"]["id"])
        path = export_repository(source, tmp_path / "transfer")
        import_repository(store, path)
        assert check_repository(store, verify_blobs=True)["ok"]
        assert store.resolve(data["id"])["manifest"] == data["manifest"]
        assert store.resolve(report_version["id"])["manifest"] == report_version["manifest"]
        again = import_repository(store, path)
        assert not any(again["imported"].values())
    finally:
        source.close()


def test_batch_size_limit_rejects_whole_transaction(backend):
    started = backend.start("study", "run", "batch-limit")
    revision, writer = started["artifact"]["id"], started["writer"]["id"]
    entries = [
        {"sequence": number, "kind": "record", "data": {"text": "x" * 900_000}}
        for number in range(1, 6)
    ]
    with pytest.raises(CapacityError):
        backend.append_batch(revision, writer, entries)
    assert backend.writer(revision, writer)["acknowledged"] == 0
    assert backend.query(revision)["items"] == []


def test_pending_finish_transfer_keeps_value_and_dependencies(store, tmp_path):
    source = Repository(tmp_path / "pending-source")
    try:
        started = source.start("study", "dataset", "pending")
        revision = started["artifact"]["id"]
        blob = source.put_blob(b"pending final value")
        value = {
            "$cairn": 1,
            "format": "file",
            "version": 1,
            "blobs": [blob],
            "refs": [],
            "data": {"name": "pending.bin"},
        }
        with pytest.raises(Pending):
            source.finish(revision, value=value)
        assert check_repository(source, verify_blobs=True)["ok"]
        import_repository(store, export_repository(source, tmp_path / "pending-export"))
        restored = store.resolve(revision)
        assert restored["state"] == "closing"
        assert restored["intent"]["value"] == value
        assert check_repository(store, verify_blobs=True)["ok"]
        with store.open_blob(blob["digest"]) as stream:
            assert stream.read() == b"pending final value"
    finally:
        source.close()


def test_concurrent_artifacts_and_opposite_dependency_order(store):
    content = [store.put_blob(b"shared-a"), store.put_blob(b"shared-b")]
    barrier = threading.Barrier(4)

    def write(index):
        handle = store.start("parallel", "run", f"writer-{index}")
        revision, writer = handle["artifact"]["id"], handle["writer"]["id"]
        ordered = content if index % 2 else list(reversed(content))
        batch = [
            {
                "sequence": n,
                "kind": "record",
                "data": {
                    "sample": {
                        "$cairn": 1,
                        "format": "file",
                        "version": 1,
                        "blobs": [blob],
                        "refs": [],
                        "data": {"producer": index},
                    }
                },
            }
            for n, blob in enumerate(ordered, 1)
        ]
        barrier.wait(timeout=5)
        store.append_batch(revision, writer, batch)
        store.close_writer(revision, writer, len(batch))
        return store.finish(revision)

    with ThreadPoolExecutor(max_workers=4) as executor:
        publications = list(executor.map(write, range(4)))
    assert all(item["state"] == "finished" for item in publications)
    assert check_repository(store, verify_blobs=True)["ok"]
    barrier = threading.Barrier(4)

    def compete(_):
        handle = store.start("parallel", "run", "one-head")
        revision, writer = handle["artifact"]["id"], handle["writer"]["id"]
        store.close_writer(revision, writer, 0)
        barrier.wait(timeout=5)
        return store.finish(revision)

    with ThreadPoolExecutor(max_workers=4) as executor:
        publications = list(executor.map(compete, range(4)))
    assert len({item["id"] for item in publications}) == 4
    assert store.resolve("run/one-head@latest", project="parallel")["number"] == 4
    assert sorted(item["number"] for item in publications) == [1, 2, 3, 4]
    assert check_repository(store)["ok"]


def test_numeric_representation_survives_replay_publication_and_transfer(backend, store, tmp_path):
    values = {
        "negative_zero": -0.0,
        "large_float": 1e20,
        "small_float": 1e-200,
        "integer": 1,
        "float": 1.0,
    }
    handle = backend.start("numeric", "run", "representation")
    revision, writer = handle["artifact"]["id"], handle["writer"]["id"]
    batch = [{"sequence": 1, "kind": "record", "data": values}]
    backend.append_batch(revision, writer, batch)
    assert backend.append_batch(revision, writer, batch)["acknowledged"] == 1
    backend.close_writer(revision, writer, 1)
    version = backend.finish(revision, value=values)
    data = backend.query(version["id"])["items"][0]["data"]
    assert math.copysign(1, data["negative_zero"]) == -1
    assert type(data["large_float"]) is float
    assert type(data["float"]) is float
    assert type(data["integer"]) is int
    assert (
        len(
            backend.query(
                version["id"],
                where={
                    "path": "/large_float",
                    "op": "eq",
                    "value": 1e20,
                },
            )["items"]
        )
        == 1
    )
    assert check_repository(store)["ok"]
    exported = export_repository(store, tmp_path / "numeric-export")
    with contextlib.closing(Repository(tmp_path / "restored")) as restored:
        import_repository(restored, exported)
        assert check_repository(restored)["ok"]
        restored_data = restored.query(version["id"])["items"][0]["data"]
        assert math.copysign(1, restored_data["negative_zero"]) == -1
        assert type(restored_data["large_float"]) is float

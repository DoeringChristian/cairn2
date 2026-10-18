from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from test_storage_review import backends, postgres_backends  # noqa: F401

from cairn.model import CapacityError, Conflict, IntegrityError, NotFound, ValidationError, digest
from cairn.ops import check_repository, export_repository, import_repository, purge_artifact
from cairn.storage import Repository, annotation_members, artifacts


@pytest.fixture
def repo(tmp_path):
    with closing(Repository(tmp_path / "source")) as value:
        yield value


def create(repo, name="training", *, metadata=None, finished=False):
    result = repo.start("study", "runs", name)
    identifier, writer = result["artifact"]["id"], result["writer"]["id"]
    count = int(metadata is not None)
    if count:
        repo.append_batch(
            identifier, writer, [{"sequence": 1, "kind": "metadata", "data": metadata}]
        )
    if finished:
        repo.close_writer(identifier, writer, count)
        repo.finish(identifier)
    return identifier


def test_merge_patch_mutable_descriptors_and_immutable_content(repo):
    identifier = create(repo, metadata={"config": {"lr": 0.1}}, finished=True)
    before = repo.resolve(identifier)
    assert before["annotations"] == {}
    repo.annotate(identifier, {"tags": ["paper"], "review": {"status": "new", "owner": "alice"}})
    after = repo.annotate(
        identifier, {"tags": ["baseline"], "review": {"status": None, "note": "good"}}
    )
    assert after["annotations"] == {
        "tags": ["baseline"],
        "review": {"owner": "alice", "note": "good"},
    }
    assert after["annotations_etag"] == digest(after["annotations"])
    assert after["manifest"] == before["manifest"]
    assert after["metadata"] == before["metadata"]
    for value in [
        repo.resolve(identifier),
        repo.resolve("runs/training@v1", project="study"),
        repo.versions(identifier)[0],
        repo.list_artifacts()["items"][0],
    ]:
        assert value["annotations"] == after["annotations"]


def test_retry_cas_validation_capacity_and_delete(repo):
    identifier = create(repo)
    etag = repo.resolve(identifier)["annotations_etag"]
    result = repo.annotate(identifier, {"tags": ["paper"]}, expected_etag=etag, request_id="first")
    assert (
        repo.annotate(identifier, {"tags": ["paper"]}, expected_etag=etag, request_id="first")
        == result
    )
    with pytest.raises(Conflict):
        repo.annotate(identifier, {"review": "yes"}, expected_etag=etag)
    with pytest.raises(Conflict):
        repo.annotate(identifier, {"tags": ["different"]}, request_id="first")
    for bad in [None, [], "abc", {"bad": float("nan")}]:
        with pytest.raises(ValidationError):
            repo.annotate(identifier, bad)
    for bad in [{"text": "x" * 1048576}, {"values": list(range(10001))}]:
        with pytest.raises(CapacityError):
            repo.annotate(identifier, bad)
    assert repo.resolve(identifier)["annotations"] == {"tags": ["paper"]}
    repo.delete(identifier)
    with pytest.raises(NotFound):
        repo.annotate(identifier, {"tags": ["paper"]}, expected_etag=etag, request_id="first")


def test_parallel_merge_and_cas(repo):
    identifier = create(repo, finished=True)
    with closing(Repository(repo.path)) as other, ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda i: (repo if i % 2 else other).annotate(identifier, {str(i): i}), range(24)
            )
        )
        etag = repo.resolve(identifier)["annotations_etag"]

        def compete(i):
            try:
                (repo if i % 2 else other).annotate(identifier, {"winner": i}, expected_etag=etag)
                return True
            except Conflict:
                return False

        assert sum(pool.map(compete, range(8))) == 1
    assert len(repo.resolve(identifier)["annotations"]) == 25


def test_filters_pagination_and_index_membership(repo):
    ids = []
    for i in range(8):
        identifier = create(repo, str(i), metadata={"config": {"lr": i}}, finished=i % 2 == 0)
        repo.annotate(
            identifier,
            {"tags": ["paper"] if i % 2 == 0 else ["other"], "nested": {"a/b~c": [1, True, None]}},
        )
        ids.append(identifier)
    where = {
        "and": [
            {"path": "/metadata/config/lr", "op": "ge", "value": 2},
            {"path": "/annotations/tags", "op": "contains", "value": "paper"},
        ]
    }
    found, cursor = [], None
    while True:
        page = repo.list_artifacts(
            project="study", namespace="runs", state="finished", where=where, limit=1, cursor=cursor
        )
        found.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
        with pytest.raises(ValidationError):
            repo.list_artifacts(where=None, cursor=cursor)
    assert found == sorted([ids[2], ids[4], ids[6]])
    for value in [1.0, True, None]:
        assert (
            len(
                repo.list_artifacts(
                    where={"path": "/annotations/nested/a~1b~0c", "op": "contains", "value": value}
                )["items"]
            )
            == 8
        )
    repo.annotate(ids[0], {"tags": None})
    assert (
        len(
            repo.list_artifacts(
                where={"path": "/annotations/tags", "op": "contains", "value": "paper"}
            )["items"]
        )
        == 3
    )


def test_transfer_purge_and_index_integrity(repo, tmp_path):
    identifier = create(repo, finished=True)
    repo.annotate(identifier, {"tags": ["paper"], "review": "approved"})
    export_repository(repo, tmp_path / "export")
    with closing(Repository(tmp_path / "target")) as target:
        import_repository(target, tmp_path / "export")
        assert target.resolve(identifier)["annotations"] == repo.resolve(identifier)["annotations"]
        assert check_repository(target)["ok"]
        target.delete(identifier)
        purge_artifact(target, identifier)
        assert check_repository(target)["counts"]["cairn_annotation_members"] == 0
    with repo._transaction(write=True) as conn:
        conn.execute(annotation_members.delete())
    assert not check_repository(repo)["ok"]
    export_repository(repo, tmp_path / "corrupt")
    with closing(Repository(tmp_path / "invalid")) as target:
        with pytest.raises(IntegrityError):
            import_repository(target, tmp_path / "corrupt")
        assert target.list_artifacts()["items"] == []


@pytest.mark.parametrize("backend_fixture", ["backends", "postgres_backends"])
def test_annotations_across_transports(request, backend_fixture):
    direct, remote = request.getfixturevalue(backend_fixture)
    for i, writer in enumerate((direct, remote)):
        identifier = create(writer, str(i), metadata={"config": {"lr": 0.01}}, finished=True)
        writer.annotate(identifier, {"tags": ["paper"], "values": [1.0, True, -0.0, 1e20]})
        for reader in (direct, remote):
            assert identifier in [
                item["id"]
                for item in reader.list_artifacts(
                    where={
                        "and": [
                            {"path": "/metadata/config/lr", "op": "lt", "value": 0.02},
                            {"path": "/annotations/tags", "op": "contains", "value": "paper"},
                        ]
                    }
                )["items"]
            ]
            assert reader.resolve(identifier)["annotations"]["tags"] == ["paper"]
        assert check_repository(direct)["ok"]


def test_detect_annotation_hash_corruption(repo):
    identifier = create(repo)
    with repo._transaction(write=True) as conn:
        conn.execute(
            artifacts.update().where(artifacts.c.id == identifier).values(annotations_etag="0" * 64)
        )
    assert not check_repository(repo)["ok"]


def test_postgres_annotation_and_finish_receipt_lock_order(request, monkeypatch):
    from threading import Event

    repo, _ = request.getfixturevalue("postgres_backends")
    identifier = create(repo, finished=True)
    entered, finished = Event(), Event()
    original = repo._once

    def pause_annotation(conn, request_id, operation, payload, function):
        if operation == "annotate":
            entered.set()
            assert finished.wait(5), "Annotation must not hold the artifact lock before its receipt"
        return original(conn, request_id, operation, payload, function)

    monkeypatch.setattr(repo, "_once", pause_annotation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        annotation = pool.submit(
            repo.annotate, identifier, {"tags": ["paper"]}, request_id="shared"
        )
        assert entered.wait(5)

        def finish():
            try:
                return repo.finish(identifier, request_id="shared")
            finally:
                finished.set()

        publication = pool.submit(finish)
        assert publication.result(timeout=10)["id"] == identifier
        with pytest.raises(Conflict):
            annotation.result(timeout=10)
    # A rejected collision neither poisons the connection nor changes content.
    assert repo.annotate(identifier, {"tags": ["paper"]}, request_id="independent")[
        "annotations"
    ] == {"tags": ["paper"]}

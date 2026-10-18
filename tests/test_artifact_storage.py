import re
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa

from cairn.model import CapacityError, Conflict, Pending, ValidationError
from cairn.storage import Repository, artifacts, schema


@pytest.fixture
def repo(tmp_path):
    value = Repository(tmp_path)
    yield value
    value.close()


def start(repo, name="training"):
    response = repo.start("study", "runs", name)
    return response["artifact"]["id"], response["writer"]["id"]


def finish(repo, identifier, writer):
    repo.close_writer(identifier, writer, repo.writer(identifier, writer)["acknowledged"])
    return repo.finish(identifier)


def test_one_identity_and_append_only(repo):
    response = repo.start("study", "runs", "training", request_id="start")
    assert repo.start("study", "runs", "training", request_id="start") == response
    assert set(response) == {"artifact", "writer"}
    identifier, writer = response["artifact"]["id"], response["writer"]["id"]
    assert re.fullmatch("[0-9a-f]{32}", identifier)
    entries = [{"sequence": 1, "kind": "record", "data": {"loss": 0.3}}]
    assert repo.append_batch(identifier, writer, entries)["acknowledged"] == 1
    assert repo.resolve(identifier[:8])["state"] == "live"
    assert repo.resolve("runs/training@live", project="study")["id"] == identifier
    result = finish(repo, identifier, writer)
    assert result["id"] == identifier
    assert result["state"] == "finished"
    assert result["number"] == 1
    assert repo.finish(identifier) == result
    assert repo.append_batch(identifier, writer, entries)["acknowledged"] == 1
    with pytest.raises(Conflict):
        repo.append_batch(identifier, writer, [{"sequence": 2, "kind": "record", "data": {}}])
    with pytest.raises(Conflict):
        repo.register_writer(identifier)
    with pytest.raises(Conflict):
        repo.finish(identifier, value="different")
    assert repo.resolve("runs/training@v1", project="study")["id"] == identifier
    assert repo.query(identifier)["artifact_id"] == identifier
    assert not any("revision" in table or table == "cairn_versions" for table in schema.tables)


def test_finish_order_labels_and_aliases(repo):
    first, fw = start(repo)
    second, sw = start(repo)
    assert first != second
    with pytest.raises(Conflict):
        repo.resolve("runs/training@live", project="study")
    assert finish(repo, second, sw)["number"] == 1
    assert finish(repo, first, fw)["number"] == 2
    assert repo.resolve("runs/training", project="study")["id"] == first
    assert [row["id"] for row in repo.versions(first)] == [second, first]
    assert repo.versions("runs/training", project="study") == repo.versions(first)
    repo.set_alias(second, "best")
    assert repo.resolve("runs/training@best", project="study")["id"] == second
    with pytest.raises(Conflict):
        repo.set_alias(first, "best")
    repo.set_alias(first, "best", expected_id=second)
    repo.set_alias(second, "latest", expected_id=first)
    assert repo.resolve("runs/training@latest", project="study")["id"] == second
    with pytest.raises(ValidationError):
        repo.set_alias(first, "v3")


def test_multiple_producers_drain_before_seal(repo):
    identifier, first = start(repo)
    second = repo.register_writer(identifier)["id"]
    with pytest.raises(Pending):
        repo.finish(identifier, value={"result": 42})
    assert repo.resolve(identifier)["state"] == "closing"
    with pytest.raises(Conflict):
        repo.register_writer(identifier)
    for writer in (first, second):
        repo.append_batch(identifier, writer, [{"sequence": 1, "kind": "record", "data": {"x": 1}}])
        repo.close_writer(identifier, writer, 1)
    result = repo.finish(identifier, value={"result": 42})
    assert result["id"] == identifier
    assert result["cutoff"] == 2
    assert result["manifest"]["value"] == {"result": 42}


def test_dependencies_pin_finished_artifacts(repo):
    source, sw = start(repo, "source")
    target, tw = start(repo, "target")
    entry = [{"sequence": 1, "kind": "dependency", "data": {"artifact_id": source}}]
    with pytest.raises(ValidationError):
        repo.append_batch(target, tw, entry)
    finish(repo, source, sw)
    repo.append_batch(target, tw, entry)
    result = finish(repo, target, tw)
    assert result["manifest"]["dependencies"] == [{"artifact_id": source, "role": "input"}]
    assert repo.add_comment(target, {"line": 1}, "hello")["artifact_id"] == target
    assert len(repo.comments(target)) == 1
    repo.delete(source)
    assert repo.resolve(source)["id"] == source
    assert source not in [row["id"] for row in repo.list_artifacts()["items"]]


def test_capacity_failure_does_not_seal(repo, monkeypatch):
    from cairn import storage

    monkeypatch.setattr(storage, "PUBLICATION_VALUE_LIMIT", 10)
    identifier, writer = start(repo)
    repo.close_writer(identifier, writer, 0)
    with pytest.raises(CapacityError):
        repo.finish(identifier, value="too much text")
    assert repo.resolve(identifier)["state"] == "live"
    assert repo.finish(identifier, value=3)["manifest"]["value"] == 3


def test_cursor_stays_on_same_artifact_and_cutoff(repo):
    identifier, writer = start(repo)
    repo.append_batch(
        identifier, writer, [{"sequence": n, "kind": "record", "data": {"n": n}} for n in (1, 2)]
    )
    page = repo.query(identifier, limit=1)
    repo.append_batch(identifier, writer, [{"sequence": 3, "kind": "record", "data": {"n": 3}}])
    finish(repo, identifier, writer)
    tail = repo.query(identifier, limit=1, cursor=page["next_cursor"])
    assert tail["cutoff"] == 2
    assert tail["next_cursor"] is None
    assert tail["items"][0]["data"] == {"n": 2}


def test_concurrent_finish_assigns_unique_labels(repo):
    pairs = [start(repo) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda pair: finish(repo, *pair), pairs))
    assert sorted(row["number"] for row in results) == list(range(1, 9))
    assert repo.resolve("runs/training", project="study")["number"] == 8


def test_ambiguous_short_id(repo):
    first, _ = start(repo, "one")
    second, _ = start(repo, "two")
    # Synthetic collision preserves all FK users by creating a third unused row.
    with repo._transaction(write=True) as conn:
        row = dict(
            conn.execute(sa.select(artifacts).where(artifacts.c.id == second)).mappings().one()
        )
        row["id"] = first[:8] + ("f" * 24)
        conn.execute(artifacts.insert().values(**row))
    with pytest.raises(Conflict, match="ambiguous"):
        repo.resolve(first[:8])
    assert repo.resolve(first)["id"] == first


def test_finish_receipt_collision_does_not_freeze_artifact(repo):
    started = repo.start("study", "runs", "collision", request_id="shared")
    identifier, writer = started["artifact"]["id"], started["writer"]["id"]
    with pytest.raises(Conflict, match="Request ID"):
        repo.finish(identifier, value=42, request_id="shared")
    artifact = repo.resolve(identifier)
    assert artifact["state"] == "live"
    assert artifact["intent"] is None
    repo.close_writer(identifier, writer, 0)
    assert repo.finish(identifier, value=43, request_id="corrected")["manifest"]["value"] == 43


def test_pending_finish_receipt_can_complete_and_replay(repo):
    identifier, writer = start(repo)
    with pytest.raises(Pending):
        repo.finish(identifier, value=42, request_id="completion")
    assert repo.resolve(identifier)["state"] == "closing"
    with pytest.raises(Conflict):
        repo.finish(identifier, value=43, request_id="completion")
    repo.close_writer(identifier, writer, 0)
    result = repo.finish(identifier, value=42, request_id="completion")
    assert result["state"] == "finished"
    assert repo.finish(identifier, value=42, request_id="completion") == result
    assert len(repo.versions(identifier)) == 1

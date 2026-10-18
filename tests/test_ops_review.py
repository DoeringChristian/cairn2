"""Independent transfer and retention invariants, including adversarial export inputs."""

import hashlib
import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
import sqlalchemy as sa

from cairn.model import (
    CairnError,
    Conflict,
    IntegrityError,
    NotFound,
    Pending,
    Unsupported,
    canonical,
    dependencies,
)
from cairn.ops import (
    TRANSFER_TABLES,
    check_repository,
    collect_garbage,
    export_repository,
    import_repository,
    purge_artifact,
)
from cairn.storage import Repository, artifacts, blobs, names, writers


def start(repository, name):
    return repository.start("default", "run", name)


def append(repository, handle, kind, data):
    writer = repository.writer(handle["artifact"]["id"], handle["writer"]["id"])
    return repository.append_batch(
        handle["artifact"]["id"],
        handle["writer"]["id"],
        [{"sequence": writer["next_sequence"], "kind": kind, "data": data}],
    )


def finish(repository, handle, value=None):
    writer = repository.writer(handle["artifact"]["id"], handle["writer"]["id"])
    repository.close_writer(handle["artifact"]["id"], writer["id"], writer["next_sequence"] - 1)
    return repository.finish(handle["artifact"]["id"], value=value)


def typed_file(blob):
    return {
        "$cairn": 1,
        "version": 1,
        "format": "file",
        "data": {"name": "evidence.bin"},
        "blobs": [{**blob, "name": "evidence.bin"}],
        "refs": [],
    }


def rows(repository):
    with repository._transaction() as conn:
        return {
            table.name: [dict(row) for row in conn.execute(sa.select(table)).mappings()]
            for table in TRANSFER_TABLES
        }


def rewrite_table(export, table, mutate):
    manifest = json.loads((export / "repository.json").read_bytes())
    entry = manifest["tables"][table.name]
    path = export / entry["file"]
    content = [json.loads(line) for line in path.read_text().splitlines()]
    mutate(content)
    encoded = b"".join(canonical(row) + b"\n" for row in content)
    path.write_bytes(encoded)
    entry["sha256"] = hashlib.sha256(encoded).hexdigest()
    entry["rows"] = len(content)
    (export / "repository.json").write_bytes(canonical(manifest))


@pytest.fixture
def pair(tmp_path):
    source, destination = Repository(tmp_path / "source"), Repository(tmp_path / "destination")
    try:
        yield source, destination
    finally:
        source.close()
        destination.close()


def test_transfer_preserves_ids_lineage_values_aliases_comments_and_live_identity(pair, tmp_path):
    source, destination = pair
    evidence = source.put_blob(b"immutable evidence")
    upstream = start(source, "input")
    append(source, upstream, "record", {"input": typed_file(evidence)})
    upstream_version = finish(source, upstream, typed_file(evidence))
    downstream = start(source, "output")
    append(source, downstream, "metadata", {"label": "early"})
    append(
        source, downstream, "dependency", {"artifact_id": upstream_version["id"], "role": "input"}
    )
    append(source, downstream, "metadata", {"label": "final"})
    final = finish(source, downstream, {"phase": "final"})
    source.set_alias(final["id"], "stable")
    comment = source.add_comment(final["id"], {"path": "/phase"}, "This is retained evidence")
    live = start(source, "still-running")
    append(source, live, "record", {"step": 1})
    exported = export_repository(source, tmp_path / "export")
    imported = import_repository(destination, exported)
    assert imported["source_repository_id"] == source.info()["repository_id"]
    assert imported["repository_id"] != imported["source_repository_id"]
    for version in (upstream_version, final):
        assert destination.resolve(version["id"]) == source.resolve(version["id"])
    assert destination.resolve("run/output@stable")["id"] == final["id"]
    assert destination.comments(final["id"]) == [comment]
    assert destination.query(final["id"])["items"] == source.query(final["id"])["items"]
    assert destination.resolve(live["artifact"]["id"])["state"] == "live"
    assert destination.writer(live["artifact"]["id"], live["writer"]["id"])["closed"]
    with destination.open_blob(evidence["digest"]) as stream:
        assert stream.read() == b"immutable evidence"
    assert check_repository(destination, verify_blobs=True)["ok"]
    assert all(
        count == 0 for count in import_repository(destination, exported)["imported"].values()
    )
    resumed = destination.register_writer(live["artifact"]["id"])
    destination.append_batch(
        live["artifact"]["id"],
        resumed["id"],
        [{"sequence": 1, "kind": "record", "data": {"step": 2}}],
    )
    destination.close_writer(live["artifact"]["id"], resumed["id"], 1)
    assert destination.finish(live["artifact"]["id"])["id"] == live["artifact"]["id"]


def test_late_import_conflict_rolls_back_all_new_metadata_and_retry_succeeds(pair, tmp_path):
    source, destination = pair
    old = start(source, "existing")
    old_version = finish(source, old, {"value": "original"})
    original = export_repository(source, tmp_path / "original")
    import_repository(destination, original)
    added = start(source, "added")
    finish(source, added, typed_file(source.put_blob(b"added bytes")))
    changed = export_repository(source, tmp_path / "changed")
    before = rows(destination)

    def corrupt_version(items):
        next(row for row in items if row["id"] == old_version["id"])["manifest"]["value"] = {
            "value": "different"
        }

    rewrite_table(changed, artifacts, corrupt_version)
    with pytest.raises(Conflict):
        import_repository(destination, changed)
    assert rows(destination) == before
    correct = export_repository(source, tmp_path / "correct")
    import_repository(destination, correct)
    assert destination.resolve("run/added")["id"] == added["artifact"]["id"]
    assert check_repository(destination, verify_blobs=True)["ok"]


@pytest.mark.parametrize("corruption", ["head", "cutoff", "acknowledged", "lineage"])
def test_import_rejects_structurally_corrupt_metadata_atomically(pair, tmp_path, corruption):
    source, destination = pair
    first = start(source, "one")
    append(source, first, "record", {"loss": 1})
    first_version = finish(source, first)
    second = start(source, "two")
    finish(source, second)
    exported = export_repository(source, tmp_path / "export")
    if corruption == "head":
        rewrite_table(exported, names, lambda items: items[0].update(head="missing"))
    elif corruption == "cutoff":
        rewrite_table(
            exported,
            artifacts,
            lambda items: next(row for row in items if row["id"] == first_version["id"]).update(
                cutoff=100
            ),
        )
    elif corruption == "acknowledged":
        rewrite_table(
            exported,
            writers,
            lambda items: next(row for row in items if row["id"] == first["writer"]["id"]).update(
                acknowledged=100
            ),
        )
    else:
        rewrite_table(
            exported,
            artifacts,
            lambda items: next(row for row in items if row["id"] == first_version["id"]).update(
                namespace="wrong-namespace"
            ),
        )
    with pytest.raises(CairnError):
        import_repository(destination, exported)
    assert all(not table for table in rows(destination).values())


@pytest.mark.parametrize(
    "attack", ["table_traversal", "table_symlink", "object_symlink", "object_bytes"]
)
def test_source_paths_and_corrupt_bytes_cannot_import_metadata(pair, tmp_path, attack):
    source, destination = pair
    handle = start(source, "payload")
    blob = source.put_blob(b"protected bytes")
    finish(source, handle, typed_file(blob))
    exported = export_repository(source, tmp_path / "export")
    manifest = json.loads((exported / "repository.json").read_bytes())
    if attack.startswith("table"):
        entry = manifest["tables"][artifacts.name]
        outside = tmp_path / "outside.jsonl"
        shutil.copyfile(exported / entry["file"], outside)
        if attack == "table_traversal":
            entry["file"] = "../outside.jsonl"
            (exported / "repository.json").write_bytes(canonical(manifest))
        else:
            (exported / entry["file"]).unlink()
            (exported / entry["file"]).symlink_to(outside)
    else:
        path = exported / "objects" / blob["digest"]
        if attack == "object_symlink":
            outside = tmp_path / "outside.bin"
            outside.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(outside)
        else:
            path.write_bytes(b"corrupt bytes")
    with pytest.raises(CairnError):
        import_repository(destination, exported)
    assert all(not table for table in rows(destination).values())


def test_retained_incoming_reference_blocks_purge_until_dependent_is_purged(pair):
    source, _ = pair
    evidence = start(source, "evidence")
    blob = source.put_blob(b"retained")
    version = finish(source, evidence, typed_file(blob))
    downstream = start(source, "dependent")
    append(source, downstream, "dependency", {"artifact_id": version["id"], "role": "evidence"})
    finish(source, downstream)
    source.delete(evidence["artifact"]["id"])
    with pytest.raises(Conflict, match="references"):
        purge_artifact(source, evidence["artifact"]["id"])
    assert source.resolve(version["id"])["id"] == version["id"]
    source.delete(downstream["artifact"]["id"])
    with pytest.raises(Conflict):
        purge_artifact(source, evidence["artifact"]["id"])
    purge_artifact(source, downstream["artifact"]["id"])
    purge_artifact(source, evidence["artifact"]["id"])
    with source._transaction(write=True) as conn:
        conn.execute(blobs.update().values(lease_until=0))
    removed = collect_garbage(source, grace_seconds=0, dry_run=False)
    assert blob["digest"] in removed["removed"]
    with pytest.raises(NotFound):
        source.resolve(version["id"])


def test_export_failure_removes_pin_and_stage_and_can_retry(pair, tmp_path, monkeypatch):
    source, _ = pair
    handle = start(source, "payload")
    finish(source, handle, typed_file(source.put_blob(b"content")))
    original = source.open_blob

    def broken_open(digest):
        raise OSError("object store unavailable")

    monkeypatch.setattr(source, "open_blob", broken_open)
    with pytest.raises(OSError):
        export_repository(source, tmp_path / "export")
    assert not (tmp_path / "export").exists()
    assert not list(tmp_path.glob(".cairn-export-*"))
    assert not collect_garbage(source)["pinned"]
    monkeypatch.setattr(source, "open_blob", original)
    assert export_repository(source, tmp_path / "export").exists()


def test_import_detects_source_mutation_after_preflight(pair, tmp_path, monkeypatch):
    source, destination = pair
    handle = start(source, "immutable")
    finish(source, handle, typed_file(source.put_blob(b"evidence")))
    exported = export_repository(source, tmp_path / "export")
    original_put = destination.put_blob

    def mutate_source_after_upload(*args, **kwargs):
        result = original_put(*args, **kwargs)
        manifest = json.loads((exported / "repository.json").read_bytes())
        path = exported / manifest["tables"][artifacts.name]["file"]
        items = [json.loads(line) for line in path.read_text().splitlines()]
        items[0]["manifest"]["value"] = {"tampered_after_preflight": True}
        path.write_bytes(b"".join(canonical(row) + b"\n" for row in items))
        return result

    monkeypatch.setattr(destination, "put_blob", mutate_source_after_upload)
    with pytest.raises(IntegrityError):
        import_repository(destination, exported)
    assert all(not table for table in rows(destination).values())


def test_export_freezes_cutoff_while_writes_continue_and_pins_cleanup(pair, tmp_path, monkeypatch):
    source, destination = pair
    evidence = start(source, "retained")
    finish(source, evidence, typed_file(source.put_blob(b"evidence")))
    source.delete(evidence["artifact"]["id"])
    live = start(source, "still-running")
    append(source, live, "record", {"phase": "before"})
    entered, release = threading.Event(), threading.Event()
    original = source.open_blob

    @contextmanager
    def paused_open(digest):
        entered.set()
        assert release.wait(5)
        with original(digest) as stream:
            yield stream

    monkeypatch.setattr(source, "open_blob", paused_open)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(export_repository, source, tmp_path / "export")
        assert entered.wait(5)
        try:
            append(source, live, "record", {"phase": "after"})
            assert collect_garbage(source, grace_seconds=0, dry_run=False)["pinned"]
            with pytest.raises(Conflict, match="export"):
                purge_artifact(source, evidence["artifact"]["id"])
        finally:
            release.set()
        exported = future.result(timeout=5)
    import_repository(destination, exported)
    assert [row["data"]["phase"] for row in destination.query(live["artifact"]["id"])["items"]] == [
        "before"
    ]
    assert len(source.query(live["artifact"]["id"])["items"]) == 2
    assert not collect_garbage(source)["pinned"]


def test_garbage_collection_recovers_after_delete_failure_and_lease_expiry(pair, monkeypatch):
    source, _ = pair
    blob = source.put_blob(b"unreferenced")
    with source._transaction(write=True) as conn:
        conn.execute(blobs.update().values(lease_until=0))
    original = source.objects.delete

    def unavailable(key):
        raise OSError("object store unavailable")

    monkeypatch.setattr(source.objects, "delete", unavailable)
    with pytest.raises(OSError):
        collect_garbage(source, grace_seconds=0, dry_run=False)
    monkeypatch.setattr(source.objects, "delete", original)
    with source._transaction(write=True) as conn:
        conn.execute(blobs.update().values(lease_until=0))
    result = collect_garbage(source, grace_seconds=0, dry_run=False)
    assert result["removed"] == [blob["digest"]]
    assert collect_garbage(source, grace_seconds=0, dry_run=False)["removed"] == []


@pytest.mark.parametrize("dependency", ["blob", "version"])
def test_pending_finalization_intent_retains_its_payload_dependencies(pair, dependency):
    source, _ = pair
    upstream = start(source, "upstream")
    upstream_version = finish(source, upstream)
    blob = source.put_blob(b"final value awaiting another writer")
    active = start(source, "active")
    other = source.register_writer(active["artifact"]["id"])
    source.close_writer(active["artifact"]["id"], active["writer"]["id"], 0)
    value = typed_file(blob)
    value["refs"] = [{"artifact_id": upstream_version["id"], "role": "input"}]
    with pytest.raises(Pending):
        source.finish(active["artifact"]["id"], value=value)
    if dependency == "blob":
        with source._transaction(write=True) as conn:
            conn.execute(
                blobs.update().where(blobs.c.digest == blob["digest"]).values(lease_until=0)
            )
        collected = collect_garbage(source, grace_seconds=0, dry_run=False)
        assert blob["digest"] not in collected["removed"]
    else:
        source.delete(upstream["artifact"]["id"])
        with pytest.raises(Conflict):
            purge_artifact(source, upstream["artifact"]["id"])
    source.close_writer(active["artifact"]["id"], other["id"], 0)
    publication = source.finish(active["artifact"]["id"], value=value)
    assert publication["manifest"]["value"] == value


@pytest.mark.parametrize("field", ["$cairn", "version"])
@pytest.mark.parametrize("wrong_type", [True, 1.0])
def test_value_envelope_markers_require_json_integers(field, wrong_type):
    envelope = {"$cairn": 1, "version": 1, "format": "custom", "data": {}, "blobs": [], "refs": []}
    envelope[field] = wrong_type
    with pytest.raises(Unsupported):
        dependencies(envelope)


@pytest.mark.parametrize("mutation", ["live_intent", "invalid_outcome", "invalid_metadata"])
def test_import_rejects_inconsistent_finalization_intents(pair, tmp_path, mutation):
    source, destination = pair
    handle = start(source, "intent")
    if mutation != "live_intent":
        finish(source, handle)
    exported = export_repository(source, tmp_path / "intent-export")

    def corrupt(items):
        row = items[0]
        if mutation == "live_intent":
            row["intent"] = {"value": None, "metadata": None, "outcome": "completed"}
        elif mutation == "invalid_outcome":
            row["intent"]["outcome"] = "invented"
            row["manifest"]["outcome"] = "invented"
        else:
            row["intent"]["metadata"] = ["invalid"]

    rewrite_table(exported, artifacts, corrupt)
    with pytest.raises(IntegrityError):
        import_repository(destination, exported)
    assert all(not table for table in rows(destination).values())


def test_finish_metadata_projection_survives_transfer(pair, tmp_path):
    source, destination = pair
    handle = start(source, "metadata")
    append(source, handle, "metadata", {"stage": "working", "seed": 7})
    source.close_writer(handle["artifact"]["id"], handle["writer"]["id"], 1)
    result = source.finish(handle["artifact"]["id"], metadata={"stage": "done"})
    assert check_repository(source)["ok"]
    import_repository(destination, export_repository(source, tmp_path / "metadata-export"))
    assert destination.resolve(result["id"])["metadata"] == {"stage": "done", "seed": 7}

import contextvars

import pytest

import cairn
from cairn import (
    Client,
    Conflict,
    File,
    NotFound,
    NotPublished,
    Pending,
    Table,
    ValidationError,
)


@pytest.fixture
def client(tmp_path):
    with Client(tmp_path / "repo") as client:
        yield client


def test_default_init_config_and_arbitrary_records(tmp_path, monkeypatch):
    monkeypatch.setattr("cairn.sdk.time.time", lambda: 1720000000.125)
    config = {"optimizer": {"rate": 0.01}}
    run = cairn.init(
        name="training",
        repo=tmp_path / "repo",
        config=config,
        metadata={"owner": "research"},
    )
    assert run.namespace == "runs"
    try:
        config["optimizer"]["rate"] = 99
        cairn.log({"sample": "A", "split": "validation", "loss": 0.8})
        cairn.log(loss=0.5, step=2)
        cairn.track(Table([{"a": 1}]), {"label": "result"}, key="table")
        cairn.update({"status_note": "done"})
        result = cairn.finish(value={"best": 0.5})
        assert run.client._closed
        with Client(tmp_path / "repo") as reader_client:
            reader = reader_client.resolve(result["id"])
            manifest = reader.descriptor["manifest"]
            assert manifest["metadata"]["config"]["optimizer"]["rate"] == 0.01
            assert manifest["metadata"]["environment"]["python"]
            assert manifest["metadata"]["status_note"] == "done"
            records = reader.records()
            assert all(record["data"]["_wall_time"] == 1720000000.125 for record in records)
            assert "step" not in records[0]["data"]
            assert records[1]["data"]["step"] == 2
            assert records[2]["data"]["table"].load() == [{"a": 1}]
            assert reader.read() == {"best": 0.5}
        with pytest.raises(ValidationError):
            contextvars.Context().run(cairn.log, {"outside": "context"})
    finally:
        run.client.close()


def test_namespace_does_not_choose_run_convenience_behavior(client):
    plain = client.start("runs", "plain")
    assert plain.namespace == "runs"
    generic = plain.finish(value={"answer": 42})
    assert generic["manifest"]["metadata"] == {}
    assert client.resolve("runs/plain@latest").read() == {"answer": 42}

    run = client.run("training", config={"seed": 7})
    assert run.namespace == "runs"
    run.finish()
    metadata = client.resolve("runs/training@latest").descriptor["manifest"]["metadata"]
    assert metadata["config"] == {"seed": 7}
    assert metadata["environment"]["python"]


def test_finish_preserves_identity_and_pins_metadata_records_and_value(client):
    artifact = client.start("dataset", "observations")
    identity = artifact.id
    artifact.update({"description": "first"})
    artifact.log({"sample": "before"})
    assert client.backend.resolve(identity)["state"] == "live"
    with pytest.raises(NotPublished):
        client.resolve(identity)
    artifact.update({"description": "later"})
    artifact.log({"sample": "after"})
    result = artifact.finish(value={"summary": "final"})
    reader = client.resolve(identity)
    assert reader.id == result["id"] == identity
    assert reader.descriptor["state"] == "finished"
    assert reader.read() == {"summary": "final"}
    assert reader.descriptor["manifest"]["metadata"]["description"] == "later"
    assert len(reader.records()) == 2
    descriptor = reader.descriptor
    descriptor["manifest"]["value"] = "mutated copy"
    assert reader.read() == {"summary": "final"}
    with pytest.raises(Conflict):
        artifact.log({"sample": "too late"})


def test_capture_consumption_download_and_generic_kind(client, tmp_path):
    source = tmp_path / "input.txt"
    source.write_text("original")
    artifact = client.start("custom-result", "weights")
    artifact.add(source)
    source.write_text("mutated")
    publication = artifact.finish(value=File(b"weights", name="weights.bin"))
    reader = client.resolve(publication["id"])
    assert reader.read().load() == b"weights"
    assert reader.download(to=tmp_path / "saved") == tmp_path / "saved"
    assert (tmp_path / "saved/weights.bin").read_bytes() == b"weights"
    assert (tmp_path / "saved/input.txt").read_text() == "original"
    assert reader.files == ["input.txt"]
    assert reader.file("input.txt").load() == b"original"
    cached = reader.download("input.txt")
    assert cached == tmp_path / "cache/artifacts" / reader.id / "input.txt"
    assert cached.read_text() == "original"
    (tmp_path / "data/sub").mkdir(parents=True)
    (tmp_path / "data/sub/b.txt").write_text("nested")
    folder = client.start("datasets", "folder")
    folder.add(tmp_path / "data")
    folder.finish()
    root = tmp_path / "cache/artifacts" / folder.id
    assert client.resolve(folder).download("data") == root / "data"
    assert (root / "data/sub/b.txt").read_text() == "nested"
    downstream = client.run("consumer")
    consumed = downstream.use(reader, role="model")
    assert consumed.id == reader.id
    result = downstream.finish()
    assert {"artifact_id": reader.id, "role": "model"} in result["manifest"]["dependencies"]


def test_multiwriter_drain_and_exact_finalization_intent(client):
    first = client.start("run", "shared")
    second = client.attach(first.id)
    first.log({"producer": "first"})
    with pytest.raises(Pending):
        first.finish(value={"score": 1})
    with pytest.raises(Conflict):
        client.attach(first.id)
    second.log({"producer": "second"})
    second.close()
    with pytest.raises(Conflict):
        first.finish(value={"score": 2})
    result = first.finish(value={"score": 1})
    assert first.finish(value={"score": 1}) == result
    assert len(client.resolve(result["id"]).records()) == 2


def test_same_name_creates_distinct_artifacts_and_version_labels(client):
    first = client.start("model", "concurrent")
    second = client.start("model", "concurrent")
    assert first.id != second.id
    one = first.finish(value={"value": 1})
    two = second.finish(value={"value": 2})
    assert one["id"] == first.id
    assert two["id"] == second.id
    assert client.resolve("model/concurrent@v1").id == first.id
    assert client.resolve("model/concurrent@v2").id == second.id
    assert client.resolve("model/concurrent@latest").id == second.id
    assert client.resolve(first.id).read() == {"value": 1}


def test_context_failure_records_outcome_and_preserves_original_exception(client):
    artifact = client.run("failing")
    with pytest.raises(RuntimeError, match="original"), artifact:
        artifact.log({"progress": "before failure"})
        raise RuntimeError("original")
    reader = client.resolve("runs/failing")
    manifest = reader.descriptor["manifest"]
    assert manifest["outcome"] == "failed"
    assert manifest["metadata"]["exception"]["type"] == "RuntimeError"


def test_validation_keeps_writer_open_and_reads_do_not_invent_step(client):
    artifact = client.start("dataset", "validation")
    with pytest.raises(ValidationError):
        artifact.log({"x": 1}, x=2)
    with pytest.raises(ValidationError):
        artifact.finish(outcome="invalid")
    artifact.log({"x": 1})
    result = artifact.finish()
    assert client.resolve(result["id"]).records()[0]["data"]["x"] == 1


def test_log_preserves_explicit_wall_time_and_does_not_mutate_input(client, monkeypatch):
    monkeypatch.setattr("cairn.sdk.time.time", lambda: 1720000000.125)
    data = {"loss": 0.2}
    artifact = client.start("runs", "clock")
    artifact.log(data)
    artifact.log(loss=0.1, _wall_time=1700000000.5)
    artifact.log({"loss": 0.05, "_wall_time": 0})
    artifact.finish()
    records = client.resolve(artifact.id).records()
    assert data == {"loss": 0.2}
    assert [row["data"]["_wall_time"] for row in records] == [1720000000.125, 1700000000.5, 0]
    filtered = client.resolve(artifact.id).records(
        where={"path": "/_wall_time", "op": "gt", "value": 1710000000}
    )
    assert len(filtered) == 1 and filtered[0]["data"]["loss"] == 0.2


def test_artifact_context_add_restores_run_and_can_finish_value(tmp_path):
    def scenario():
        run = cairn.init(project="study", repo=tmp_path / "repo", provenance=False)
        with cairn.artifact("datasets", "samples") as dataset:
            assert dataset.client is not run.client
            cairn.add(Table([{"sample": 1}]), name="samples")
            with cairn.artifact("datasets", "nested") as nested:
                cairn.finish(value=2)
            cairn.log({"dataset-note": True})
        cairn.use(dataset, role="training-data")
        assert repr(dataset).startswith("<Artifact datasets/samples@v1 id=")
        assert run.version is None and dataset.version == 1
        cairn.log(loss=0.1)
        cairn.finish(value=3)
        with Client(tmp_path / "repo", project="study") as reader:
            assert reader.resolve(nested.id).read() == 2
            result = reader.resolve(dataset.id)
            assert (
                repr(result) == f"<Reader datasets/samples@v1 id={dataset.id[:12]} project='study'>"
            )
            assert result.version == 1
            assert result.descriptor["manifest"]["captures"]["samples"]
            assert len(result.records()) == 1
            assert reader.resolve(run.id).records()[0]["data"]["loss"] == 0.1
        assert not hasattr(cairn, "capture")
        assert not hasattr(dataset, "capture")

    contextvars.Context().run(scenario)


def test_artifact_failure_restores_context_and_standalone_discovery(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_REPO", str(tmp_path / "repo"))

    def scenario():
        with cairn.artifact("datasets", "parent", project="study") as parent:
            with (
                pytest.raises(ValueError, match="failed"),
                cairn.artifact("datasets", "child") as child,
            ):
                raise ValueError("failed")
            cairn.log(restored=True)
        assert parent.client._closed and child.client._closed
        with Client(tmp_path / "repo", project="study") as reader:
            assert reader.resolve(child.id).descriptor["manifest"]["outcome"] == "failed"
            assert reader.resolve(parent.id).records()[0]["data"]["restored"] is True
        with pytest.raises(ValidationError):
            cairn.log(outside=True)

    contextvars.Context().run(scenario)


def test_implicit_reports_after_finish(tmp_path):
    from cairn.reports import build, publish

    def scenario():
        run = cairn.init(project="study", repo=tmp_path / "repo", provenance=False)
        cairn.finish(value=1)
        source = tmp_path / "report.qmd"
        source.write_text("---\ncairn:\n  inputs:\n    run: " + run.id + "\n---\n# Example\n")
        result = build(source, tmp_path / "report")
        published = publish(result.path, name="example")
        with Client(tmp_path / "repo", project="study") as reader:
            report = reader.resolve(published["id"])
            assert report.descriptor["project"] == "study"
            assert report.descriptor["manifest"]["dependencies"][0]["artifact_id"] == run.id

    contextvars.Context().run(scenario)


def test_remove_falls_back_and_gc_purges_only_unreferenced(client):
    first = client.start("runs", "train")
    first.finish()
    second = client.start("runs", "train")
    second.finish()
    consumer = client.run("consumer", provenance=False)
    consumer.use(first)
    consumer.finish()
    assert client.remove("runs/train@latest")["id"] == second.id
    assert client.resolve("runs/train@latest").id == first.id
    assert client.remove(first)["retained_by"] == [consumer.id]
    assert client.resolve(first.id).version == 1
    with pytest.raises(NotPublished):
        client.resolve("runs/train@latest")
    assert client.gc(grace_seconds=0)["artifacts"] == [second.id]
    assert client.gc(grace_seconds=3600)["artifacts"] == []
    assert client.gc(apply=True, grace_seconds=0)["artifacts"] == [second.id]
    with pytest.raises(NotFound):
        client.resolve(second.id)
    client.remove(consumer)
    purged = client.gc(apply=True, grace_seconds=0)["artifacts"]
    assert sorted(purged) == sorted([first.id, consumer.id])
    with pytest.raises(NotFound):
        client.resolve(first.id)


def test_provenance_files_are_reserved_and_listed_separately(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with Client(tmp_path / "repo") as client:
        run = client.run("sections", capture_source=False)
        with pytest.raises(ValidationError, match="reserved"):
            run.add(b"x", name="provenance/fake.txt")
        run.add(b"notes", name="notes.txt")
        run.finish()
        reader = client.resolve(run)
        assert reader.files == ["notes.txt"]
        assert reader.provenance
        assert all(name.startswith("provenance/") for name in reader.provenance)
        root = reader.download()
        assert [path.name for path in root.iterdir()] == ["notes.txt"]
        assert reader.download("provenance") == root / "provenance"
        assert all((root / name).is_file() for name in reader.provenance)


def test_init_uses_configured_project_and_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAIRN_REPO", raising=False)
    (tmp_path / ".cairn.toml").write_text('project = "configured"\nrepo = "store"\n')
    run = cairn.init("trained", provenance=False)
    assert (run.project, run.client.repo) == ("configured", str(tmp_path / "store"))
    cairn.finish()

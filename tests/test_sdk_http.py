import threading
import time
from contextlib import closing

import httpx
import pytest
from test_http import serve

import cairn
from cairn import Client, Conflict, File, Pending
from cairn.storage import Repository
from cairn.transport import HTTPRepository


def test_remote_journal_recovery_lost_ack_and_immutable_file(tmp_path, monkeypatch):
    repository = Repository(tmp_path / "repository")
    try:
        with serve(repository) as url:
            outbox = tmp_path / "outbox"
            source = tmp_path / "captured.txt"
            source.write_text("captured before disconnection")
            client = Client(url, token="test-token", outbox=outbox)
            artifact = client.run("recover", config={"seed": 7})
            artifact.flush(timeout=5)

            def offline():
                raise httpx.ConnectError("injected disconnection")

            with client.delivery._dispatch:
                monkeypatch.setattr(client.backend, "info", offline)
                artifact.add(source)
                logged_after = time.time()
                for index in range(50):
                    artifact.log({"sample": index, "split": "test"})
                logged_before = time.time()
                visible = client.backend.query(artifact.id, kind="record")
                assert visible["items"] == []
            with pytest.raises(Pending):
                artifact.flush(timeout=0.03)
            assert client.delivery.pending() == 51
            writer_id = artifact.writer_id
            client.close()
            source.write_text("source was edited after log returned")

            append = HTTPRepository.append_batch
            lost = []

            def lose_first_ack(self, *args, **kwargs):
                result = append(self, *args, **kwargs)
                if not lost:
                    lost.append(True)
                    raise httpx.ReadError("reply lost after actual HTTP append committed")
                return result

            monkeypatch.setattr(HTTPRepository, "append_batch", lose_first_ack)
            with Client(url, token="test-token", outbox=outbox) as recovered:
                handles = recovered.recover()
                assert [handle.writer_id for handle in handles] == [writer_id]
                publication = handles[0].finish(value={"complete": True}, timeout=5)
                reader = recovered.resolve(publication["id"])
                assert reader.read() == {"complete": True}
                records = reader.records()
                assert [record["data"]["sample"] for record in records] == list(range(50))
                assert all(
                    logged_after <= row["data"]["_wall_time"] <= logged_before for row in records
                )
                assert reader.download("captured.txt", to=tmp_path / "restored").read_text() == (
                    "captured before disconnection"
                )
                assert reader.descriptor["manifest"]["metadata"]["config"] == {"seed": 7}
                assert recovered.delivery.pending() == 0
                assert lost
    finally:
        repository.close()


def test_remote_multiwriter_pending_and_conflict(tmp_path):
    repository = Repository(tmp_path / "repository")
    try:
        with (
            serve(repository) as url,
            Client(url, token="test-token", outbox=tmp_path / "first") as first,
            Client(url, token="test-token", outbox=tmp_path / "second") as second,
        ):
            main = first.start("run", "parallel")
            worker = second.attach(main.id)
            main.log({"producer": 1})
            with pytest.raises(Pending):
                main.finish(value={"answer": 42}, timeout=5)
            with pytest.raises(Conflict):
                main.finish(value={"answer": 43}, timeout=5)
            worker.log({"producer": 2})
            worker.close(timeout=5)
            publication = main.finish(value={"answer": 42}, timeout=5)
            reader = first.resolve(publication["id"])
            assert {row["data"]["producer"] for row in reader.records()} == {1, 2}
            assert reader.read() == {"answer": 42}
    finally:
        repository.close()


def test_default_contexts_release_delivery_threads_and_http_connections(tmp_path):
    before = {thread for thread in threading.enumerate() if thread.name == "cairn-delivery"}
    with closing(Repository(tmp_path / "repository")) as repository, serve(repository) as url:
        clients = []
        for index in range(5):
            with cairn.init(
                name=f"owned-{index}",
                repo=url,
                token="test-token",
                outbox=tmp_path / "outbox",
            ) as artifact:
                clients.append(artifact.client)
                cairn.log({"sample": index})
            assert artifact.result is not None
            assert artifact.finish() == artifact.result
        assert all(client._closed for client in clients)
        assert all(client.backend._client.is_closed for client in clients)
        assert all(not client.delivery._thread.is_alive() for client in clients)
    after = {thread for thread in threading.enumerate() if thread.name == "cairn-delivery"}
    assert after == before


def test_owned_pending_context_can_retry_after_resource_cleanup(tmp_path):
    with (
        closing(Repository(tmp_path / "repository")) as repository,
        serve(repository) as url,
        Client(url, token="test-token", outbox=tmp_path / "worker") as client,
    ):
        artifact = cairn.init(
            name="owned-pending", repo=url, token="test-token", outbox=tmp_path / "owner"
        )
        worker = client.attach(artifact.id)
        original_client = artifact.client
        with pytest.raises(Pending), artifact:
            artifact.log({"producer": "owner"})
        assert original_client._closed
        assert not original_client.delivery._thread.is_alive()
        assert not client._closed
        with pytest.raises(Conflict):
            artifact.finish(value="a different intent")
        assert artifact.client is original_client
        worker.log({"producer": "worker"})
        worker.close(timeout=5)
        publication = artifact.finish(timeout=5)
        assert artifact.client is not original_client
        assert artifact.client._closed
        assert not artifact.client.delivery._thread.is_alive()
        assert not client._closed
        assert len(client.resolve(publication["id"]).records()) == 2


def test_owned_completed_file_finish_retry_uses_no_connection(tmp_path):
    with closing(Repository(tmp_path / "repository")) as repository, serve(repository) as url:
        artifact = cairn.init(repo=url, token="test-token", outbox=tmp_path / "outbox")
        value = File(b"durable bytes", name="value.bin")
        publication = artifact.finish(value=value, timeout=5)
        assert artifact.client._closed
        assert artifact.client.backend._client.is_closed
    # The server has shut down, and the successful retry is entirely local.
    assert artifact.finish(value=value) == publication
    with pytest.raises(Conflict):
        artifact.finish(value=File(b"different bytes", name="value.bin"))


@pytest.mark.parametrize("shared_pending", [False, True])
@pytest.mark.parametrize("default_outbox", [False, True])
def test_remote_reads_never_open_journal_or_resume_pending_delivery(
    tmp_path, monkeypatch, shared_pending, default_outbox
):
    from cairn.delivery import Delivery

    with closing(Repository(tmp_path / "repository")) as repository, serve(repository) as url:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        options = {} if default_outbox else {"outbox": tmp_path / "outbox"}
        with Client(url, token="test-token", **options) as probe:
            outbox = probe._outbox
            assert probe.delivery is None
            assert not outbox.exists()
        started = repository.start("default", "datasets", "published")
        identity = started["artifact"]["id"]
        repository.close_writer(identity, started["writer"]["id"], 0)
        version = repository.finish(identity, value={"answer": 42})["id"]
        if shared_pending:
            with closing(HTTPRepository(url, token="test-token")) as backend:
                journal = Delivery(backend, outbox, url, background=False)
                pending = journal.start(
                    "start", {"project": "default", "namespace": "runs", "name": "pending"}
                )
                journal.append(pending["writer"]["id"], "record", {"pending": True})
                journal.close()
            journal_before = (outbox / "journal.sqlite3").read_bytes()

        def forbidden_delivery(*args, **kwargs):
            pytest.fail("Reading must not initialize delivery")

        with monkeypatch.context() as patch:
            patch.setattr("cairn.sdk.Delivery", forbidden_delivery)
            with Client(url, token="test-token", **options) as client:
                assert client.delivery is None
                reader = client.resolve(version)
                assert reader.read() == {"answer": 42}
                assert reader.records() == []
                assert client.flush() == {"pending": 0}
                assert client.delivery is None
        if shared_pending:
            assert (outbox / "journal.sqlite3").read_bytes() == journal_before
            assert repository.query(pending["artifact"]["id"])["items"] == []
            with Client(url, token="test-token", **options) as client:
                writers = client.recover()
                assert [writer.writer_id for writer in writers] == [pending["writer"]["id"]]
                client.flush(timeout=5)
            assert len(repository.query(pending["artifact"]["id"])["items"]) == 1
        else:
            assert not outbox.exists()


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("field", ["value", "metadata", "merged_metadata"])
def test_capacity_rejected_finish_can_be_corrected(tmp_path, remote, field):
    from cairn import CapacityError

    with (
        closing(Repository(tmp_path / "repository")) as repository,
        serve(repository) as url,
        Client(
            url if remote else tmp_path / "repository",
            token="test-token",
            outbox=tmp_path / "outbox",
        ) as client,
    ):
        artifact = client.start("runs", "correct-finalization")
        if field == "merged_metadata":
            artifact.update({"existing": "x" * 600_000})
            artifact.flush(timeout=5)
            invalid = {"metadata": {"additional": "y" * 600_000}}
        elif field == "metadata":
            invalid = {"metadata": {"oversized": "x" * 1024**2}}
        else:
            invalid = {"value": "x" * 1024**2}
        with pytest.raises(CapacityError):
            artifact.finish(**invalid, timeout=5)
        assert client.backend.resolve(artifact.id)["intent"] is None
        assert artifact._finish_args is None
        publication = artifact.finish(value={"corrected": True}, timeout=5)
        assert client.resolve(publication["id"]).read() == {"corrected": True}


@pytest.mark.parametrize("remote", [False, True])
def test_capacity_cleanup_network_failure_preserves_original_error(tmp_path, monkeypatch, remote):
    from cairn import CapacityError

    with (
        closing(Repository(tmp_path / "repository")) as repository,
        serve(repository) as url,
        Client(
            url if remote else tmp_path / "repository",
            token="test-token",
            outbox=tmp_path / "outbox",
        ) as client,
    ):
        artifact = client.start("runs", "uncertain-cleanup")
        artifact.update({"existing": "x" * 600_000})
        artifact.flush(timeout=5)
        metadata = {"additional": "y" * 600_000}

        def disconnected(*args, **kwargs):
            raise httpx.ConnectError("verification unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(client.backend, "resolve", disconnected)
            with pytest.raises(CapacityError):
                artifact.finish(metadata=metadata, timeout=5)
        assert artifact._finish_args is not None
        with pytest.raises(Conflict):
            artifact.finish(value="changed before verification", timeout=5)
        with pytest.raises(CapacityError):
            artifact.finish(metadata=metadata, timeout=5)
        assert artifact._finish_args is None
        assert artifact.finish(value="corrected", timeout=5)["state"] == "finished"


def test_top_level_artifact_inherits_remote_run_and_restores_it(tmp_path):
    import contextvars

    with closing(Repository(tmp_path / "repo")) as repository, serve(repository) as url:

        def scenario():
            run = cairn.init(
                project="study",
                repo=url,
                token="test-token",
                outbox=tmp_path / "outbox",
                provenance=False,
            )
            with cairn.artifact("datasets", "samples") as dataset:
                cairn.add(cairn.Table([{"sample": 1}]), name="samples")
            assert not run.client._closed
            cairn.use(dataset.id, role="training-data")
            cairn.log(loss=0.2)
            print("remote output")
            cairn.finish()
            descriptor = repository.resolve(dataset.id)
            assert descriptor["project"] == "study"
            assert descriptor["manifest"]["captures"]["samples"]
            assert (
                repository.resolve(run.id)["manifest"]["dependencies"][0]["artifact_id"]
                == dataset.id
            )
            with Client(url, project="study", token="test-token") as client:
                assert client.resolve(run.id).console() == "remote output\n"

        contextvars.Context().run(scenario)


def test_remove_and_gc_through_server(tmp_path):
    repository = Repository(tmp_path / "repository")
    try:
        with (
            serve(repository) as url,
            Client(url, token="test-token", outbox=tmp_path / "outbox") as client,
        ):
            artifact = client.start("datasets", "remote")
            artifact.finish(timeout=5)
            assert client.remove("datasets/remote@latest")["id"] == artifact.id
            assert client.gc(grace_seconds=0)["artifacts"] == [artifact.id]
            assert client.gc(apply=True, grace_seconds=0)["artifacts"] == [artifact.id]
            assert client.gc(grace_seconds=0)["artifacts"] == []
    finally:
        repository.close()


def test_state_dir_selects_default_outbox(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CAIRN_STATE_DIR", str(tmp_path / "state"))
    with Client("http://127.0.0.1:9", token="test-token") as client:
        assert client._outbox.parent == tmp_path / "state/outbox"

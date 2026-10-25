import copy
import hashlib
import io
import multiprocessing
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cairn.delivery import Delivery
from cairn.media import File, encode
from cairn.model import CapacityError, Conflict, Pending


class Remote:
    """A fault-injectable contract endpoint, with acknowledgements after commit."""

    timeout = 0.1

    def __init__(self):
        self.identity = "repository-one"
        self.offline = False
        self.lose_append_reply = False
        self.lose_start_reply = False
        self.starts = {}
        self.writers = {}
        self.entries = {}
        self.blobs = {}
        self.finished = {}
        self.intents = {}
        self.lock = threading.RLock()

    def info(self):
        if self.offline:
            raise ConnectionError("offline")
        return {"repository_id": self.identity}

    def start(self, project, namespace, name, *, request_id):
        with self.lock:
            if request_id in self.starts:
                return copy.deepcopy(self.starts[request_id])
            number = len(self.starts) + 1
            artifact = {
                "id": f"artifact-{number}",
                "namespace": namespace,
                "name": name,
                "project": project,
                "entity": "artifact",
                "state": "live",
            }
            writer = {
                "id": f"writer-{number}",
                "artifact_id": artifact["id"],
                "closed": False,
                "next_sequence": 1,
            }
            self.writers[writer["id"]] = writer
            self.entries[writer["id"]] = []
            result = {"artifact": artifact, "writer": copy.deepcopy(writer)}
            self.starts[request_id] = result
            if self.lose_start_reply:
                self.lose_start_reply = False
                raise ConnectionError("reply lost after start committed")
            return copy.deepcopy(result)

    def append_batch(self, artifact_id, writer_id, entries):
        self.info()
        with self.lock:
            stored = self.entries[writer_id]
            for entry in entries:
                sequence = entry["sequence"]
                if sequence <= len(stored):
                    assert stored[sequence - 1] == entry
                else:
                    assert sequence == len(stored) + 1
                    stored.append(copy.deepcopy(entry))
            if self.lose_append_reply:
                self.lose_append_reply = False
                raise ConnectionError("reply lost after append committed")
            return {"acknowledged": len(stored), "cutoff": len(stored)}

    def has_blobs(self, digests):
        return {digest: digest in self.blobs for digest in digests}

    def put_blob(self, source, *, digest, size):
        content = Path(source).read_bytes()
        assert hashlib.sha256(content).hexdigest() == digest
        assert len(content) == size
        self.blobs[digest] = content
        return {"digest": digest, "size": size}

    def open_blob(self, digest):
        return io.BytesIO(self.blobs[digest])

    def close_writer(self, artifact_id, writer_id, final_sequence):
        assert len(self.entries[writer_id]) == final_sequence
        self.writers[writer_id]["closed"] = True
        return self.writers[writer_id]

    def finish(self, artifact_id, *, request_id, **args):
        if artifact_id in self.intents and self.intents[artifact_id] != args:
            raise Conflict("different intent")
        self.intents[artifact_id] = copy.deepcopy(args)
        if any(not w["closed"] and w["artifact_id"] == artifact_id for w in self.writers.values()):
            raise Pending("other producers remain open", details={"open_writers": ["other"]})
        if artifact_id not in self.finished:
            self.finished[artifact_id] = {
                "id": artifact_id,
                "state": "finished",
                "manifest": args,
            }
        return self.finished[artifact_id]


def begin(delivery):
    return delivery.start("start", {"project": "default", "namespace": "runs", "name": "test"})


@pytest.mark.parametrize("schema", ["1", "2", "999", None])
def test_incompatible_outbox_schema_is_rejected_without_mutation(tmp_path, schema):
    remote = Remote()
    delivery = Delivery(remote, tmp_path, "https://example.test", background=False)
    writer = begin(delivery)["writer"]["id"]
    value = encode(File(b"retained pending bytes"), delivery.stage_blob)
    delivery.append(writer, "record", {"file": value})
    delivery.close()
    with sqlite3.connect(tmp_path / "journal.sqlite3") as connection:
        if schema is None:
            connection.execute("DELETE FROM meta WHERE key='schema_version'")
        else:
            connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (schema,))
    connection.close()
    before = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and not path.name.endswith(("-wal", "-shm"))
    }
    with pytest.raises(Conflict, match="schema"):
        Delivery(remote, tmp_path, "https://example.test", background=False)
    after = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and not path.name.endswith(("-wal", "-shm"))
    }
    # A read-only SQLite connection can create WAL shared-memory bookkeeping.
    assert after == before
    assert remote.entries[writer] == []


def test_lost_ack_restart_staged_bytes_and_binding(tmp_path):
    remote = Remote()
    journal = tmp_path / "journal"
    source = tmp_path / "source.txt"
    source.write_bytes(b"captured bytes")
    delivery = Delivery(remote, journal, "https://example.test", background=False)
    writer = begin(delivery)["writer"]["id"]
    value = encode(File(source), delivery.stage_blob)
    delivery.append(writer, "record", {"file": value})
    source.write_bytes(b"later mutations")
    remote.offline = True
    with pytest.raises(Pending):
        delivery.flush(writer, timeout=0.01)
    assert delivery.pending(writer) == 1
    delivery.close()
    # Reopening offline is allowed only for the existing bound repository.
    delivery = Delivery(remote, journal, "https://example.test", background=False)
    assert delivery.recover()[0]["writer"]["id"] == writer
    remote.offline = False
    remote.lose_append_reply = True
    delivery.flush(writer, timeout=1)
    assert len(remote.entries[writer]) == 1
    assert remote.blobs[value["blobs"][0]["digest"]] == b"captured bytes"
    assert not list((journal / "blobs").iterdir())
    assert delivery.pending() == 0
    delivery.close()
    with pytest.raises(Conflict, match="destination"):
        Delivery(remote, journal, "https://another.test", background=False)
    remote.identity = "replacement-repository"
    with pytest.raises(Conflict, match="different repository"):
        Delivery(remote, journal, "https://example.test", background=False)


def test_creation_response_loss_recovers_same_writer(tmp_path):
    remote = Remote()
    delivery = Delivery(remote, tmp_path, "https://example.test", background=False)
    remote.lose_start_reply = True
    with pytest.raises(ConnectionError):
        begin(delivery)
    delivery.close()
    delivery = Delivery(remote, tmp_path, "https://example.test", background=False)
    responses = delivery.recover()
    assert len(responses) == len(remote.starts) == 1
    delivery.append(responses[0]["writer"]["id"], "record", {"loss": 3})
    delivery.flush()
    delivery.close()


def test_atomic_sequence_allocation_across_clients(tmp_path):
    remote = Remote()
    first = Delivery(remote, tmp_path, "https://example.test", background=False, batch_size=7)
    writer = begin(first)["writer"]["id"]
    second = Delivery(remote, tmp_path, "https://example.test", background=False, batch_size=7)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda index: (first if index % 2 else second).append(
                    writer, "record", {"index": index}
                ),
                range(100),
            )
        )
    first.flush()
    assert [entry["sequence"] for entry in remote.entries[writer]] == list(range(1, 101))
    assert {entry["data"]["index"] for entry in remote.entries[writer]} == set(range(100))
    first.close()
    second.close()


def test_capacity_failure_does_not_allocate_sequence(tmp_path):
    remote = Remote()
    delivery = Delivery(
        remote, tmp_path, "https://example.test", background=False, max_bytes=8192, max_entries=2
    )
    writer = begin(delivery)["writer"]["id"]
    with pytest.raises(CapacityError):
        delivery.stage_blob(b"x" * 8193)
    assert not list((tmp_path / "blobs").iterdir())
    delivery.append(writer, "record", {"value": 1})
    delivery.append(writer, "record", {"value": 2})
    with pytest.raises(CapacityError):
        delivery.append(writer, "record", {"value": 3})
    delivery.flush()
    assert delivery.append(writer, "record", {"value": 3})["sequence"] == 3
    delivery.flush()
    delivery.close()


def test_oversize_record_is_rejected_before_allocating_a_durable_sequence(tmp_path):
    remote = Remote()
    delivery = Delivery(remote, tmp_path, "https://example.test", background=False)
    writer = begin(delivery)["writer"]["id"]
    with pytest.raises(CapacityError, match="1 MiB"):
        delivery.append(writer, "record", {"text": "x" * 1024**2})
    assert delivery.pending() == 0
    assert delivery.append(writer, "record", {"text": "deliverable"})["sequence"] == 1
    delivery.flush()
    assert len(remote.entries[writer]) == 1
    delivery.close()


def test_finish_intent_survives_pending_and_restart(tmp_path):
    remote = Remote()
    delivery = Delivery(remote, tmp_path, "https://example.test", background=False)
    response = begin(delivery)
    writer, artifact = response["writer"]["id"], response["artifact"]["id"]
    remote.writers["other"] = {"id": "other", "artifact_id": artifact, "closed": False}
    remote.entries["other"] = []
    delivery.append(writer, "record", {"loss": 0.4})
    args = {
        "value": {"score": 0.4},
        "metadata": None,
        "outcome": "completed",
    }
    operation = delivery.operation(writer, "finish", args)
    with pytest.raises(Pending):
        delivery.flush(writer, operation_id=operation)
    with pytest.raises(Conflict):
        delivery.append(writer, "record", {})
    with pytest.raises(Conflict):
        delivery.operation(writer, "finish", {**args, "outcome": "failed"})
    delivery.close()
    remote.close_writer(artifact, "other", 0)
    delivery = Delivery(remote, tmp_path, "https://example.test", background=False)
    assert delivery.recover()[0]["writer"]["id"] == writer
    repeated = delivery.operation(writer, "finish", args)
    assert repeated == operation
    result = delivery.flush(writer, operation_id=repeated)
    assert result["manifest"]["value"] == {"score": 0.4}
    assert len(remote.finished) == 1
    delivery.close()
    delivery = Delivery(remote, tmp_path, "https://example.test", background=False)
    assert delivery.recover()[0]["result"] == result
    assert delivery.prune() == 1
    assert delivery.recover() == []
    delivery.close()


def test_replaced_repository_cannot_receive_queued_data(tmp_path):
    remote = Remote()
    delivery = Delivery(remote, tmp_path, "https://example.test", background=False)
    writer = begin(delivery)["writer"]["id"]
    delivery.append(writer, "record", {"must": "stay local"})
    remote.identity = "different"
    with pytest.raises(Conflict):
        delivery.flush()
    assert delivery.pending() == 1
    assert remote.entries[writer] == []
    delivery.close()


def test_flush_timeout_also_bounds_waiting_for_background_dispatch(tmp_path):
    remote = Remote()
    delivery = Delivery(remote, tmp_path, "https://example.test", background=False)
    writer = begin(delivery)["writer"]["id"]
    delivery.append(writer, "record", {"x": 1})
    entered = threading.Event()
    release = threading.Event()

    def hold_dispatch():
        with delivery._dispatch:
            entered.set()
            release.wait(5)

    thread = threading.Thread(target=hold_dispatch)
    thread.start()
    entered.wait(5)
    start = time.monotonic()
    try:
        with pytest.raises(Pending):
            delivery.flush(writer, timeout=0.02)
        assert time.monotonic() - start < 0.3
    finally:
        release.set()
        thread.join()
    delivery.flush()
    delivery.close()


def _crash_with_durable_record(path, writer_id, source):
    delivery = Delivery(Remote(), path, "https://example.test", background=False)
    encoded = encode(File(source), delivery.stage_blob)
    delivery.append(writer_id, "record", {"file": encoded})
    (Path(path) / "blobs/.stage-abandoned").write_bytes(b"interrupted uncommitted upload")
    os._exit(0)


def test_abrupt_process_exit_keeps_committed_entry_and_staged_bytes(tmp_path):
    remote = Remote()
    journal = tmp_path / "journal"
    source = tmp_path / "source.txt"
    source.write_bytes(b"before crash")
    delivery = Delivery(remote, journal, "https://example.test", background=False)
    writer = begin(delivery)["writer"]["id"]
    delivery.close()
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_with_durable_record, args=(journal, writer, source)
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 0
    source.write_bytes(b"after crash")
    delivery = Delivery(remote, journal, "https://example.test", background=False)
    assert not (journal / "blobs/.stage-abandoned").exists()
    assert delivery.pending() == 1
    delivery.flush()
    digest = remote.entries[writer][0]["data"]["file"]["blobs"][0]["digest"]
    assert remote.blobs[digest] == b"before crash"
    delivery.close()


def test_records_logged_together_reach_the_repository_in_one_batch(tmp_path):
    remote = Remote()
    batches = []
    original = remote.append_batch

    def counted(artifact_id, writer_id, entries):
        batches.append(len(entries))
        return original(artifact_id, writer_id, entries)

    remote.append_batch = counted
    delivery = Delivery(remote, tmp_path, "https://example.test")
    try:
        writer = begin(delivery)["writer"]["id"]
        time.sleep(0.5)
        for step in range(20):
            delivery.append(writer, "record", {"step": step})
        deadline = time.monotonic() + 5
        while sum(batches) < 20 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert batches == [20]
    finally:
        delivery.close()

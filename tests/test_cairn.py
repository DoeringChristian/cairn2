import io
import math
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest
import uvicorn

import cairn
from cairn import cairn_pb2 as pb
from cairn import values
from cairn.__main__ import main
from cairn.outbox import Outbox
from cairn.server import create_app
from cairn.store import SEGMENT_RECORDS, Repository


@pytest.fixture
def serve():
    running = []

    def start(repository, port=0):
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        server = uvicorn.Server(uvicorn.Config(create_app(repository, token="secret"), log_level="warning"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        thread.start()
        while not server.started:
            time.sleep(0.01)

        def stop():
            server.should_exit = True
            thread.join()
            listener.close()

        running.append(stop)
        return listener.getsockname()[1], stop

    yield start
    for stop in running:
        stop()


@pytest.fixture(params=["local", "remote"])
def client(request, tmp_path, serve):
    if request.param == "local":
        with cairn.Client(tmp_path / "repo", project="demo") as found:
            yield found
        return
    repository = Repository(tmp_path / "repo", link_roots=[tmp_path / "linked"])
    port, _ = serve(repository)
    with cairn.Client(f"http://127.0.0.1:{port}", project="demo", token="secret", state=tmp_path / "state") as found:
        yield found
    repository.close()


def test_every_value_survives(client):
    record = {"null": None, "yes": True, "int": 2**63 - 1, "low": -(2**63), "float": 0.1, "nan": math.nan, "inf": -math.inf,
              "text": "é\n", "bytes": b"\0\xff", "list": [1, [2.0, {"a": None}]], "map": {"a/b": {"c": 1}}, "empty": {}, "none": []}
    with client.run("values") as run:
        run.log(record)
    found = client.get("runs/values").last(*record)
    assert math.isnan(found.pop("nan")) and record.pop("nan")
    assert found == record
    assert [type(found[k]) for k in record] == [type(v) for v in record.values()]
    for bad in ({"a": 2**63}, {"a": object()}, {"": 1}, {1: 2}, {"_mine": 1}):
        with client.artifact("runs", "bad") as run, pytest.raises(cairn.Invalid):
            run.log(bad)


def test_runs_keep_records_files_folders_and_references(client, tmp_path):
    folder = tmp_path / "out"
    (folder / "a").mkdir(parents=True)
    (folder / "a/x.txt").write_text("x")
    (folder / "y.bin").write_bytes(b"\0y")
    with client.artifact("datasets", "scenes", {"source": "lab"}) as data:
        data.add(folder, "raw")
    assert client.get("datasets/scenes").config == {"source": "lab"}
    with client.run("grids/train", config={"lr": 1e-3, "net": {"w": 64}}) as run:
        assert run.use("datasets/scenes", role="train").id == data.id
        for step in range(5):
            run.log(step=step, loss=1 / (step + 1))
        run.log({"mesh.ply": cairn.File(b"ply"), "figures": cairn.Directory(folder), "faces": 3, "t": cairn.Table([{"a": 1}])})
        run.log({"later/z.txt": folder / "a/x.txt", "again": folder})
    found = client.get("runs/grids/train@v1")
    assert found.last("faces", "step", "gone") == {"faces": 3, "step": 4} and set(found.last()) == set(found.keys())
    assert (found.version, found.finished, found.outcome) == (1, True, "completed")
    assert found.config == {"lr": 1e-3, "net": {"w": 64}}
    assert found["_config/net/w"] == 64 and found["faces"] == 3 and found["t"].load() == [{"a": 1}]
    assert found.series("loss", x="step")[:2] == [(0.0, 1.0), (1.0, 0.5)]
    assert [f.path for f in found.files()] == ["again/a/x.txt", "again/y.bin", "figures/a/x.txt", "figures/y.bin", "later/z.txt", "mesh.ply", "t/data"]
    assert found.read("mesh.ply") == b"ply" and found.read("figures/a/x.txt") == b"x" and found.read("t") == b'{"a": 1}\n'
    assert found["mesh.ply"].read() == b"ply"
    assert [(p, r.id) for p, r in found.uses()] == [("_use/train", data.id)]
    assert [r.id for _, r in client.get("datasets/scenes").used_by()] == [run.id]
    assert len(found.download(tmp_path / "copy")) == 7
    assert (tmp_path / "copy/figures/a/x.txt").read_text() == "x"
    with pytest.raises(cairn.NotFound):
        client.get("runs/grids/other")
    with pytest.raises(KeyError):
        found["missing"]


def test_logging_a_key_again_replaces_its_whole_value(client):
    with client.run("keys") as run:
        run.log({"out": {"a": 1, "old.txt": cairn.File(b"old")}, "loss": 5.0, "x": 1})
        run.log({"out": {"b": 2, "new.txt": cairn.File(b"new")}, "loss": 2.0, "x": "text"})
        run.log({"loss": 3.0})
    found = client.get("runs/keys")
    assert set(found["out"]) == {"b", "new.txt"} and found["loss"] == 3.0 and found["x"] == "text"
    assert [f.path for f in found.files()] == ["out/new.txt"]
    for gone in ("out/a", "out/old.txt"):
        assert not client.find(where=[gone])
    assert not client.find(where=[("x", "=", 1)]) and client.find(where=[("x", "=", "text"), ("out/b", "=", 2)])
    assert client.find(where=[("min(loss)", "=", 2.0), ("max(loss)", "=", 5.0), ("loss", "=", 3.0)])
    assert [record["loss"] for _, record in found.records(having="loss")] == [5.0, 2.0, 3.0]
    assert [record["x"] for _, record in found.records(having="x")] == [1, "text"]


def test_a_field_given_twice_is_refused(client):
    with client.run("twice") as run:
        run.log({"loss": 0.3}, step=10)
        with pytest.raises(cairn.Invalid, match="twice: step"):
            run.log({"loss": 0.3, "step": 9}, step=10)
    assert client.get("runs/twice").series("step") == [10.0]


def test_searches(client):
    for i, rate in enumerate((0.1, 0.01, 0.001)):
        with client.run(f"sweep/{i}", config={"lr": rate, "net": "mlp", "big": i == 0}) as run:
            run.log(psnr=20.0 + i)
            run.log(psnr=25.0 - i)
            run.log({f"figures/error_{i}.png": cairn.File(b"png%d" % i), "note": "x" * 1000})
    with client.run("other") as run:
        run.log(psnr=math.nan)

    def names(*args, **kwargs):
        return [item.name for item in client.find(*args, **kwargs)]

    assert names("runs/sweep/*") == ["sweep/2", "sweep/1", "sweep/0"] and names("runs/*") == ["other"]
    assert len(names("runs/**")) == 4 and names("runs/sweep/1") == ["sweep/1"] and names(text="weep/2") == ["sweep/2"]
    assert names(where=[("_config/lr", "<=", 0.01)], order="_config/lr") == ["sweep/2", "sweep/1"]
    assert names("runs/sweep/*", order="-psnr") == ["sweep/0", "sweep/1", "sweep/2"]
    assert names("runs/sweep/*", order="-max(psnr)", limit=1) == ["sweep/0"]
    assert names(where=[("max(psnr)", ">", 23.5), ("_config/net", "contains", "ml")], order="psnr") == ["sweep/1", "sweep/0"]
    assert names(where=[("_config/big", "=", True)]) == ["sweep/0"] and names(where=[("psnr", "=", math.nan)]) == ["other"]
    assert names(where=[("_config/lr", "!=", 0.1)], order="_config/lr") == ["sweep/2", "sweep/1"]
    assert names(where=[("nothing", "=", 1)]) == [] and names(order="nothing")[0] == "other"
    found = client.find("runs/sweep/*", values=["psnr", "min(psnr)", "_config/lr", "gone"], order="_config/lr", limit=1, offset=1)
    assert found.total == 3 and found[0].values == {"psnr": 24.0, "min(psnr)": 21.0, "_config/lr": 0.01}
    paths = [f.path for f in client.files(text="ERROR_")]
    assert sorted(paths) == [f"figures/error_{i}.png" for i in range(3)]
    assert [f.reference for f in client.files("figures/error_1.png")] == ["runs/sweep/1@v1#figures/error_1.png"]
    assert len(client.files("figures/*.png")) == 3 and client.files("*.png") == [] and len(client.files(text="g")) == 3
    assert client.files(text="error_1")[0].read() == b"png1"
    assert [f.path for f in client.files("**/error_2.*")] == ["figures/error_2.png"]
    assert names(where=["max(psnr) > 23.5", "_config/net contains 'ml'"], order="psnr") == ["sweep/1", "sweep/0"]
    assert names(where=["_config/big = true"]) == ["sweep/0"] and names(where=["psnr = nan"]) == ["other"] and len(names(where=["psnr"])) == 4
    assert names(where=["_config/net = mlp", "_config/lr = 1e-3"]) == ["sweep/2"] and names(where=["figures/error_1.png"]) == ["sweep/1"]
    for wrong in (("psnr", "~", 1), ("psnr", "contains", 1), "psnr<1", "psnr <1", "", "max(tag:x) = 1"):
        with pytest.raises(cairn.Invalid):
            client.find(where=[wrong])


def test_long_runs_read_the_same_across_segments(tmp_path):
    total = 2 * SEGMENT_RECORDS + 300
    with cairn.Client(tmp_path / "repo", project="demo") as client:
        run = client.run("long")
        writer = client.backend
        records = [values.record({"step": i, "loss": -float(i), **({"image": {"i": i}} if i % 500 == 0 else {})}) for i in range(total)]
        for start in range(0, total, 700):
            writer.append(pb.Append(artifact=run.id, writer="w", sequence=start + 1, records=records[start : start + 700]))
        live = client.get(run.id)
        expected = [(float(i), -float(i)) for i in range(total)]
        assert live.series("loss", x="step") == expected
        assert live.series("loss", after=total - 4) == [-float(i) for i in range(total - 5, total)]
        assert [r["step"] for _, r in live.records(having="image")] == list(range(0, total, 500))
        assert [o for o, _ in live.records()] == list(range(1, total + 2))
        assert [o for o, _ in live.records(after=SEGMENT_RECORDS + 1)][0] == SEGMENT_RECORDS + 2
        assert len(list(live.records(having="loss"))) == total
        writer.leave(pb.Leave(artifact=run.id, writer="w"))
        run.finish()
        done = client.get(run.id)
        assert done.series("loss", x="step") == expected and done["loss"] == -float(total - 1)
        assert writer.check(content=True) == []
        tables = ("keys", "marks", "latest", "columns", "files", "holds", "refs")
        dump = {t: sorted(map(repr, writer._db().execute(f"SELECT * FROM {t}"))) for t in tables}
        writer.rebuild()
        assert dump == {t: sorted(map(repr, writer._db().execute(f"SELECT * FROM {t}"))) for t in tables}


def test_batches_can_be_sent_again(tmp_path):
    repository = Repository(tmp_path)
    identifier = repository.open(pb.Open(project="p", namespace="runs", name="a", request_id="r")).id
    assert repository.open(pb.Open(project="p", namespace="runs", name="a", request_id="r")).id == identifier
    one, two, three = (values.record({"i": i}) for i in range(3))
    def send(sequence, *records):
        return repository.append(pb.Append(artifact=identifier, writer="w", sequence=sequence, records=records)).records

    assert send(1, one, two) == 2 and send(1, one, two) == 2 and send(2, two, three) == 3
    with pytest.raises(cairn.Pending):
        send(5, one)
    for bad in (b"\xff\xff", values.record({"a": pb.Blob(digest=b"x" * 32)})):
        with pytest.raises((cairn.Invalid, cairn.Pending)):
            send(4, bad)
    assert [r["i"] for _, r in cairn.Client(tmp_path, project="p").get(identifier).records()] == [0, 1, 2]
    finish = pb.Finish(artifact=identifier, writer="w", sequence=4, record=values.record({"_outcome": "completed"}))
    assert repository.finish(finish).version == repository.finish(finish).version == 1
    with pytest.raises(cairn.Conflict):
        send(5, one)
    repository.close()


def test_finishing_waits_for_other_processes(tmp_path):
    with cairn.Client(tmp_path, project="demo") as client:
        run = client.run("shared")
        other = client.attach(run.id)
        other.log(x=1)
        with pytest.raises(cairn.Pending):
            run.finish()
        other.leave()
        assert run.finish().version == 1
        stuck = client.run("stuck")
        assert client.get(stuck.id).finish("incomplete", gone=True).outcome == "incomplete"
        with pytest.raises(cairn.Conflict):
            stuck.log(x=1)


def test_errors_fail_the_run(client):
    with pytest.raises(RuntimeError), client.run("failing") as run:
        run.log(x=1)
        raise RuntimeError("boom")
    with pytest.raises(cairn.NotFound, match="newest is v1"):
        client.get("runs/failing")
    found = client.get("runs/failing@v1")
    assert not found.completed and found.outcome == "failed" and found["_error"] == {"type": "RuntimeError", "message": "boom"}


def test_names_tags_aliases_and_deletion(client):
    for i in range(2):
        with client.run("a") as run:
            run.log(i=i)
    first, second = client.get("runs/a@v1"), client.get("runs/a@latest")
    assert second.version == 2 and [r.version for r in client.find("runs/a", latest=True)] == [2]
    with pytest.raises(RuntimeError), client.run("a"):
        raise RuntimeError
    assert client.get("runs/a").version == 2 and [r.version for r in client.find("runs/a", latest=True)] == [2]
    assert [r.version for r in client.find("runs/a", state="completed")] == [2, 1] and len(client.find("runs/a", state="finished")) == 3
    client.delete("runs/a@v3")
    client.tag(first, "paper", topic="sdf", n=3)
    client.tag("runs/a@v2", "paper", topic="mesh", ok=True)
    client.tag(first, n=4.5)
    client.untag("runs/a@v2", "ok", "never")
    assert first.tags == {"paper": None, "topic": "sdf", "n": 4.5} and second.tags == {"paper": None, "topic": "mesh"}

    def versions(*where, **more):
        return [r.version for r in client.find("runs/a", where=where, **more)]

    assert versions("tag:paper") == [2, 1] and versions("tag:topic = sdf") == [1] and versions("tag:n > 4") == [1] and versions("tag:gone") == []
    assert versions("tag:topic contains es", "tag:paper") == [2] and versions(order="tag:topic") == [2, 1] and versions("tag:n = '4.5'") == []
    client.alias(first, "best", expected=None)
    with pytest.raises(cairn.Conflict):
        client.alias(second, "best", expected=None)
    client.alias(second, "best", expected=first)
    client.alias(second, "other")
    assert (first.aliases, second.aliases) == ([], ["best", "other"]) and second.tags == {"paper": None, "topic": "mesh"}
    assert client.get("runs/a@best").version == 2 and versions("alias:best") == [2]
    assert {k: r.version for k, r in client.aliases("runs/a").items()} == {"best": 2, "other": 2}
    client.unalias("runs/a", "other")
    client.unalias("runs/a", "other")
    with client.run("b") as run:
        pass
    client.alias("runs/b", "best")
    assert list(client.aliases("runs/a")) == ["best"] == list(client.aliases(second))
    client.tag(client.run("open"), "running")
    for wrong in (lambda: client.alias(second, "v3"), lambda: client.alias(second, "latest"), lambda: client.tag(second), lambda: client.tag(second, "@best"),
                  lambda: client.tag(second, "a b"), lambda: client.tag(second, x=[1]), lambda: client.tag(second, "x", x=1), lambda: client.aliases("runs/a@v1"),
                  lambda: client.alias("runs/open@live", "best")):
        with pytest.raises(cairn.Invalid):
            wrong()
    records = len(client.get(first.id))
    assert records == 3
    with client.run("user") as run:
        run.use("runs/a@v1")
    client.delete(first)
    with pytest.raises(cairn.NotFound):
        client.get("runs/a@v1")
    assert [r.version for _, r in client.get("runs/user").uses()] == [1] and [r.version for r in client.find("runs/a")] == [2]
    assert [r.version for r in client.find("runs/a", state="deleted")] == [3, 1]
    assert client.restore(first).version == 1 and client.get("runs/a@v1").tags["paper"] is None and len(client.find("runs/a")) == 2
    with pytest.raises(cairn.NotFound):
        client.restore("0" * 32)


def test_collection_keeps_what_records_hold(tmp_path):
    with cairn.Client(tmp_path, project="demo") as client:
        with client.run("kept") as run:
            run.log({"f": cairn.File(b"one")})
            run.log({"f": cairn.File(b"two")})
        with client.run("gone") as run:
            run.log({"f": cairn.File(b"three"), "g": [cairn.File(b"one")]})
        client.delete("runs/gone")
        assert client.backend.collect(grace=-1) == 1
        kept = client.get("runs/kept")
        assert [r["f"].read() for _, r in kept.records(having="f")] == [b"one", b"two"]
        assert client.files(text="g") == [] and client.backend.check(content=True) == []


def test_files_may_change_after_log_unless_promised(client, tmp_path):
    path = tmp_path / "model.pt"
    with client.run("mutable") as run:
        path.write_bytes(b"first")
        run.log({"a": path})
        path.write_bytes(b"second")
        run.log({"a": cairn.File(path, immutable=True)})
    found = client.get("runs/mutable")
    assert [r["a"].read() for _, r in found.records(having="a")] == [b"first", b"second"]


def test_links_leave_content_in_place_and_never_lie(client, tmp_path):
    (tmp_path / "linked").mkdir()
    inside, outside = tmp_path / "linked/a.bin", tmp_path / "b.bin"
    inside.write_bytes(b"inside")
    outside.write_bytes(b"outside")
    with client.artifact("datasets", "scan") as data:
        data.log({"a": cairn.File(inside, link=True), "b": cairn.File(outside, link=True)})
    found, stored = client.get("datasets/scan"), tmp_path / "repo/blobs"
    copies = [p.read_bytes() for p in stored.rglob("*") if p.is_file()]
    assert copies == ([b"outside"] if client.remote else [])
    assert (found.read("a"), found.read("b")) == (b"inside", b"outside")
    with pytest.raises(cairn.CairnError):
        client.backend.link_blob(pb.LinkBlob(digest=b"0" * 32, size=6, path=str(inside)))
    inside.write_bytes(b"CHANGED")
    with pytest.raises(cairn.NotFound):
        client.get("datasets/scan").read("a")
    again = tmp_path / "copy.bin"
    again.write_bytes(b"inside")
    digest = bytes.fromhex(found["a"].digest)
    client.backend.put(digest, io.BytesIO(b"inside"))
    assert client.get("datasets/scan").read("a") == b"inside"


def test_the_server_checks_tokens_content_and_messages(tmp_path, serve):
    import httpx

    port, _ = serve(Repository(tmp_path / "repo"))
    url = f"http://127.0.0.1:{port}"
    with cairn.Client(url, token="wrong", state=tmp_path / "s") as client, pytest.raises(cairn.Unauthorized):
        client.find()
    auth = {"Authorization": "Bearer secret"}
    assert httpx.put(f"{url}/v1/content/{'0' * 64}", content=b"x", headers=auth).status_code == 400
    assert httpx.get(f"{url}/v1/content/{'0' * 64}", headers=auth).status_code == 404
    assert httpx.get(f"{url}/v1/content/..%2f..%2fmeta", headers=auth).status_code in (400, 404)
    assert httpx.post(f"{url}/v1/find", content=b"\xff", headers=auth).status_code == 400
    assert httpx.post(f"{url}/v1/rebuild", content=b"", headers=auth).status_code == 404
    assert list((tmp_path / "repo/blobs").rglob("tmp*")) == []


JOB = """
import sys, cairn
client = cairn.Client(sys.argv[1], project="demo", token="secret", state=sys.argv[2])
run = client.run("job")
for i in range(200): run.log(i=i)
run.log({"f": cairn.File(b"content")})
print("logged", flush=True)
sys.stdin.read()
"""


def test_records_survive_a_lost_server_and_a_killed_process(tmp_path, serve):
    repository = Repository(tmp_path / "repo")
    port, stop = serve(repository)
    url, state = f"http://127.0.0.1:{port}", tmp_path / "state"
    with cairn.Client(url, project="demo", token="secret", state=state) as client:
        run = client.run("job")
        run.flush()
        stop()
        for i in range(50):
            run.log(i=i)
        with pytest.raises(cairn.Pending):
            run.flush(timeout=0.5)
        serve(repository, port)
        run.flush(timeout=20)
        assert run.finish(timeout=20).series("i") == [float(i) for i in range(50)]
    process = subprocess.Popen([sys.executable, "-c", JOB, url, str(state)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env={**os.environ, "PYTHONPATH": os.getcwd()})
    assert process.stdout.readline() == b"logged\n"
    process.send_signal(signal.SIGKILL)
    process.wait()
    with cairn.Client(url, project="demo", token="secret", state=state) as client:
        assert client.recover() == []
        [left] = client.find("runs/job", state="live")
        assert left.series("i") == [float(i) for i in range(200)] and left.read("f") == b"content"
        assert left.finish("incomplete", gone=True).version == 2 and left.version == 2 and left.finished


def test_a_refused_record_is_reported_and_stops_only_its_artifact(tmp_path, serve):
    port, _ = serve(Repository(tmp_path / "repo"))
    with cairn.Client(f"http://127.0.0.1:{port}", project="demo", token="secret", state=tmp_path / "state") as client:
        bad, good = client.run("bad"), client.run("good")
        client.flush()
        client.get(bad.id).finish("incomplete", gone=True)
        bad.log(x=1)
        with pytest.raises(cairn.Conflict, match="finished"):
            client.flush(timeout=10)
        with pytest.raises(cairn.Conflict):
            bad.log(x=2)
        good.log(x=1)
        assert good.finish(timeout=10).outcome == "completed"


def test_changed_records_are_noticed(tmp_path):
    with cairn.Client(tmp_path, project="demo") as client:
        with client.run("a") as run:
            run.log(x=1)
        db = client.backend._db()
        db.execute("UPDATE segments SET sha = zeroblob(32)")
        assert client.backend.check() and pytest.raises(cairn.Corrupt, lambda: list(client.get("runs/a").records()))


def test_many_threads_and_processes_log_at_once(tmp_path):
    code = "import sys, cairn as c\nwith c.Client(sys.argv[1], project='p') as k:\n  with k.run('a') as r:\n    for i in range(100): r.log(i=i)\n"
    jobs = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], env={**os.environ, "PYTHONPATH": os.getcwd()}) for _ in range(4)]
    assert [j.wait() for j in jobs] == [0] * 4
    with cairn.Client(tmp_path, project="p") as client:
        assert sorted(r.version for r in client.find("runs/a")) == [1, 2, 3, 4]
        run = client.run("threads")
        threads = [threading.Thread(target=lambda: [run.log(i=i) for i in range(100)]) for _ in range(4)]
        [t.start() for t in threads], [t.join() for t in threads]
        assert len(run.finish().series("i")) == 400 and client.backend.check() == []


def test_the_outbox_never_sends_a_changed_file(tmp_path, serve):
    port, stop = serve(Repository(tmp_path / "repo"))
    path = tmp_path / "f.bin"
    path.write_bytes(b"one")
    with cairn.Client(f"http://127.0.0.1:{port}", project="demo", token="secret", state=tmp_path / "state") as client:
        run = client.run("a")
        run.flush()
        with client.outbox._sending:
            run.log({"f": cairn.File(path, immutable=True)})
            time.sleep(0.02)
            path.write_bytes(b"two")
        with pytest.raises(cairn.Conflict, match="changed"):
            run.flush(timeout=5)
    assert Outbox and stop


def test_command_line(tmp_path, capsys):
    base = ["--repo", str(tmp_path / "repo"), "--project", "demo"]
    (tmp_path / "in").mkdir()
    (tmp_path / "in/a.txt").write_text("a")
    assert main([*base, "add", "datasets/d", str(tmp_path / "in")]) == 0
    assert capsys.readouterr().out == "datasets/d@v1\n"
    with cairn.Client(tmp_path / "repo", project="demo") as client, client.run("r", config={"lr": 0.5}) as run:
        run.use("datasets/d")
        run.log(loss=math.nan)
    for command, text in ((["find", "--where", "_config/lr = 0.5", "--show", "loss"], "runs/r@v1\tloss=nan"), (["files", "--text", "a.txt"], "datasets/d@v1#in/a.txt"),
                          (["show", "--json", "runs/r"], '"loss": "nan"'), (["records", "runs/r"], '"ordinal": 1'), (["links", "datasets/d"], "used by\truns/r@v1"), (["check"], "ok")):
        assert main([*base, *command]) == 0
        assert text in capsys.readouterr().out
    assert main([*base, "download", "datasets/d", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out/in/a.txt").read_text() == "a"
    assert main([*base, "show", "runs/none"]) == 1
    capsys.readouterr()

    def cli(*args, code=0):
        assert main([*base, *args]) == code
        return capsys.readouterr().out

    assert cli("tag", "runs/r", "paper", "topic=sdf", "n=3", "odd=a=b", "empty=") == ""
    assert cli("tag", "list", "runs/r") == 'empty=""\nn=3\nodd="a=b"\npaper\ntopic="sdf"\n'
    cli("tag", "remove", "runs/r", "odd", "empty", "topic")
    cli("tag", "add", "runs/r@v1", "n=4")
    assert cli("tag", "list", "runs/r") == "n=4\npaper\n"
    assert cli("find", "--where", "tag:paper", "--where", "tag:n = 4") == "runs/r@v1\n"
    cli("alias", "runs/r@v1", "best", "--expected", "none")
    cli("alias", "add", "runs/r", "best", "--expected", "none", code=1)
    cli("alias", "runs/r", "best", "--expected", "runs/r@v1")
    assert cli("alias", "list", "runs/r") == "best\truns/r@v1\n" and '"_outcome"' in cli("show", "runs/r@best")
    shown = cli("show", "runs/r")
    assert all(part in shown for part in ("artifact:  runs/r@v1", "state:     completed", "aliases:   best", "tags:      n=4, paper", "records:   4", '"lr": 0.5'))
    cli("alias", "remove", "runs/r", "best")
    assert cli("alias", "list", "runs/r") == ""
    for wrong in (["tag", "runs/r"], ["tag", "list"], ["tag", "runs/r", "@x"], ["tag", "runs/r", "a=1", "a=2"], ["alias", "runs/r", "latest"], ["alias", "runs/r", "a", "b"], ["find", "--where", "a<1"]):
        cli(*wrong, code=1)


def test_typed_values_come_back_as_their_type(client, tmp_path):
    np = pytest.importorskip("numpy")
    pixels = np.arange(24, dtype=np.uint8).reshape(4, 2, 3)
    (tmp_path / "d").mkdir()
    (tmp_path / "d/a.txt").write_text("a")
    with client.run("types") as run:
        run.log({"image": cairn.Image(pixels, view=3), "gray": cairn.Image(np.array([[0.0, 1.0]])), "grid": [cairn.Image(pixels)], "array": cairn.Array(pixels * 1.5),
                 "table": cairn.Table([{"a": 1, "b": "x\u2028y"}, {"a": None}]), "folder": cairn.Directory(tmp_path / "d"), "own": {"_type": "mine", "x": 1}})
        for wrong in (cairn.Image(pixels.astype(np.int32)), cairn.Array(np.array([object()]))):
            with pytest.raises(TypeError):
                run.log({"wrong": wrong})
    found = client.get("runs/types")
    image = found["image"]
    assert isinstance(image, cairn.Image) and image.meta == {"view": 3, "width": 2, "height": 4} and (image.array() == pixels).all()
    assert found["gray"].array().tolist() == [[0, 255]] and (found["grid"][0].array() == pixels).all()
    assert (found["array"].load() == pixels * 1.5).all() and found["array"].load().dtype == np.float64
    assert found["table"].load() == [{"a": 1, "b": "x\u2028y"}, {"a": None}]
    assert dict(found["folder"])["a.txt"].read() == b"a" and found["folder/a.txt"].read() == b"a"
    assert found["own"] == {"_type": "mine", "x": 1} and (cairn.Image(pixels).array() == pixels).all()
    with client.run("again") as run:
        run.use(found)
        run.log({k: found[k] for k in ("image", "table", "folder", "array")})
    again = client.get("runs/again")
    assert (again["image"].array() == pixels).all() and again["image"].meta == image.meta and again["folder/a.txt"].read() == b"a"
    with cairn.Client(tmp_path / "other", project="o") as other, other.run("copy") as run:
        run.log({"image": image, "file": found["folder"]["a.txt"]})
    with cairn.Client(tmp_path / "other", project="o") as other:
        assert (other.get("runs/copy")["image"].array() == pixels).all() and other.get("runs/copy").read("file") == b"a"


def test_readers_are_current(client):
    run = client.run("live")
    run.log(i=0)
    run.flush()
    found = client.get(run.id)
    assert (found.finished, found.version, len(found), found["i"]) == (False, None, 2, 0) and "@live" in found.ref
    run.log(i=1)
    run.finish()
    assert (found.finished, found.version, len(found), found["i"], found.ref) == (True, 1, 4, 1, "runs/live@v1")
    client.tag(run, a=1)
    assert found.tags == {"a": 1}


def test_following_ends_with_the_artifact_and_misses_nothing(client):
    run = client.run("followed")
    run.flush()
    seen, having = [], []
    reader = client.get(run.id)
    threads = [threading.Thread(target=lambda: seen.extend(o for o, _ in reader.follow(interval=0.005))),
               threading.Thread(target=lambda: having.extend(r["b"] for _, r in client.get(run.id).follow(having="b", interval=0.01)))]
    for thread in threads:
        thread.start()
    for i in range(1500):
        run.log({"a": i} if i % 100 else {"b": i})
    run.finish()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    assert seen == list(range(1, 1503)) and having == list(range(0, 1500, 100))
    assert list(reader.follow(after=1501)) and len(list(reader.follow())) == 1502


def test_stages_depend_only_on_completed_artifacts(client):
    stage = client.run("stage")
    stage.flush()
    with client.run("next") as run:
        for depend in (lambda: run.use(stage.id), lambda: run.use("runs/stage@live"), lambda: run.log(x=cairn.Ref(stage))):
            with pytest.raises(cairn.Invalid, match="live"):
                depend()
        with pytest.raises(cairn.NotFound):
            run.use("runs/stage")
        # The server holds to it too, whatever a client sends.
        raw = client.backend.open(pb.Open(project="demo", namespace="runs", name="raw")).id
        with pytest.raises(cairn.Invalid, match="live"):
            client.backend.append(pb.Append(artifact=raw, writer="raw", sequence=1, records=[values.record({"x": pb.Ref(artifact=stage.id)})]))
        assert len(client.get(stage.id)) == 1
        stage.finish("failed")
        with pytest.raises(cairn.NotFound, match="completed"):
            run.use("runs/stage")
        for depend in (lambda: run.use("runs/stage@v1"), lambda: run.log(x=cairn.Ref(stage))):
            with pytest.raises(cairn.Invalid, match="did not complete"):
                depend()
        with client.run("resumed") as resumed:
            assert resumed.use("runs/stage@v1", "checkpoint", incomplete=True).outcome == "failed"
            resumed.log(x=cairn.Ref(stage, incomplete=True))
            resumed.flush()
            resumed.log(again=resumed["x"])
            live = client.run("live")
            live.flush()
            with pytest.raises(cairn.Invalid, match="live"):
                resumed.use(live.id, incomplete=True)
        assert {r.id for _, r in client.get("runs/resumed").uses()} == {stage.id}
        done = client.run("stage").finish()
        run.use("runs/stage")
        run.log(x=cairn.Ref("runs/stage@v2"))
    assert [r.id for _, r in client.get("runs/next").uses()] == [done.id] * 2


def test_older_repositories_are_refused_untouched(tmp_path):
    (tmp_path / "metadata.sqlite3").write_bytes(b"old")
    with pytest.raises(cairn.Invalid, match="older Cairn"):
        cairn.Client(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["metadata.sqlite3"]


def test_one_artifact_reads_and_writes(client):
    with client.run("both", {"lr": 1}) as run:
        run.log(loss=0.5)
        run.flush()
        assert run["loss"] == 0.5 and run.config == {"lr": 1} and run.series("loss") == [0.5] and not run.finished and run.version is None
        assert "loss" in run and "gone" not in run and len(run) == 2 and bool(run)
        reading = client.get(run.id)
        for write in (lambda: reading.log(x=1), lambda: reading.add(b"x", "x"), lambda: reading.use("runs/none")):
            with pytest.raises(cairn.CairnError):
                write()
        with pytest.raises(TypeError):
            list(run)
    assert (run.finished, run.version, run.ref, run.outcome, len(run)) == (True, 1, "runs/both@v1", "completed", 3)
    assert abs(run.created_at - time.time()) < 60 and run.created_at <= run.finished_at and isinstance(run.heard_at, float)
    with pytest.raises(cairn.Conflict):
        run.log(x=1)


def test_a_later_spelling_of_a_path_replaces_the_earlier_in_searches(client):
    with client.run("paths") as run:
        run.log({"a/b": 1})
        run.log({"a": {"b": 2}})
    found = client.get("runs/paths")
    assert found["a/b"] == 1 and found["a"] == {"b": 2} and [r.name for r in client.find(where=["a/b = 2"])] == ["paths"]


def program(code, *args, **options):
    return subprocess.Popen([sys.executable, "-c", "import sys, os, time, cairn as cairn\n" + code, *map(str, args)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env={**os.environ, "PYTHONPATH": os.getcwd(), "CAIRN_TOKEN": "secret"}, **options)


@pytest.mark.parametrize("kind", ["local", "remote"])
def test_what_a_program_leaves_open_is_finished_when_it_ends(tmp_path, serve, kind):
    repository = Repository(tmp_path / "repo")
    repo = f"http://127.0.0.1:{serve(repository)[0]}" if kind == "remote" else tmp_path / "repo"
    start = f"run = cairn.Client({str(repo)!r}, project='demo', token='secret', state={str(tmp_path / 'state')!r}).run('r')\nrun.log(x=1)\n"
    cases = {"": "completed", "sys.exit(0)": "completed", "sys.exit(3)": "failed", "1 / 0": "failed", "raise KeyboardInterrupt": "cancelled",
             "print('up', flush=True); time.sleep(60)": "cancelled", "\nif os.fork() == 0: sys.exit(0)\nos.wait(); time.sleep(0.2); run.log(x=2)": "completed"}
    with cairn.Client(tmp_path / "repo", project="demo") as client:
        for version, (code, outcome) in enumerate(cases.items(), 1):
            process = program(start + code)
            if "sleep(60)" in code:
                assert process.stdout.readline() == b"up\n"
                process.terminate()
            _, errors = process.communicate(timeout=30)
            found = client.get(f"runs/r@v{version}")
            assert (found.outcome, b"could not" in errors, found.completed) == (outcome, False, outcome == "completed"), errors
            assert (found.get("_error") is None) == (outcome == "completed")
        shared = client.run("shared")
        program(f"cairn.Client({str(tmp_path / 'repo')!r}).attach({shared.id!r}).log(x=1)").communicate(timeout=30)
        assert shared.finish()["x"] == 1
    repository.close()


def test_a_closed_client_leaves_nothing_open(tmp_path):
    with cairn.Client(tmp_path, project="demo") as client:
        client.run("a")
    with pytest.raises(RuntimeError), cairn.Client(tmp_path, project="demo") as client:
        client.run("b")
        raise RuntimeError
    with cairn.Client(tmp_path, project="demo") as client:
        assert [(r.name, r.outcome) for r in client.find("runs/*")] == [("b", "failed"), ("a", "completed")]
        for error, outcome in ((SystemExit(0), "completed"), (SystemExit(2), "failed"), (KeyboardInterrupt(), "cancelled")):
            with pytest.raises(type(error)), client.run("c") as run:
                raise error
            assert run.outcome == outcome


def test_the_console_of_a_run(tmp_path):
    code = """
with cairn.Client(sys.argv[1], project='demo') as client:
    with client.run('loud') as run:
        print('printed')
        os.system('echo from a child')
        os.write(1, b'\\xc3')
        with client.run('inner'): print('inner')
    with client.run('second'): print('again', file=sys.stderr)
    with client.run('off', console=False): print('off')
    with client.artifact('datasets', 'd'): print('data')
print('after')
"""
    out, errors = program(code, tmp_path).communicate(timeout=30)
    assert out == b"printed\nfrom a child\n\xc3inner\noff\ndata\nafter\n" or out.count(b"\n") == 6
    assert b"again" in errors
    with cairn.Client(tmp_path, project="demo") as client:
        assert "from a child\n" in (text := client.get("runs/loud").console()) and "printed\n" in text and "inner\n" in text and "after" not in text
        assert client.get("runs/second").console() == "again\n" and client.get("runs/loud").outcome == "completed"
        assert [client.get(ref).console() for ref in ("runs/inner", "runs/off", "datasets/d")] == [""] * 3


def test_large_content_goes_in_pieces_and_continues(tmp_path, serve, monkeypatch):
    from cairn import remote

    monkeypatch.setattr(remote, "PIECE", 1 << 20)
    repository = Repository(tmp_path / "repo")
    port, stop = serve(repository)
    data = os.urandom((1 << 20) * 5 + 17)
    (tmp_path / "big.bin").write_bytes(data)
    sent, send = [], remote.Remote._send

    def failing(self, method, path, **options):
        if method == "PUT":
            sent.append(options["params"]["offset"])
            if len(sent) == 3:
                raise cairn.Pending("broke off")
            if len(sent) == 5:
                repository._uploads.clear()  # as after a restart
        return send(self, method, path, **options)

    monkeypatch.setattr(remote.Remote, "_send", failing)
    with cairn.Client(f"http://127.0.0.1:{port}", project="demo", token="secret", state=tmp_path / "state") as client:
        with client.run("big") as run:
            run.add(tmp_path / "big.bin")
        assert client.get("runs/big").read("big.bin") == data
        assert sent == [0, 1 << 20, 2 << 20, 2 << 20, 3 << 20, 4 << 20, 5 << 20]
        assert list((tmp_path / "repo").rglob("*.part")) == []
        import hashlib

        other = hashlib.sha256(b"abc").digest()
        for pieces in ([(0, b"abX", 3)], [(0, b"abcd", 3)]):
            with pytest.raises(cairn.Invalid):
                for offset, piece, size in pieces:
                    repository.put_part(other, io.BytesIO(piece), offset, size)
        repository.put_part(other, io.BytesIO(b"ab"), 0, 3)
        with pytest.raises(cairn.Conflict) as caught:
            repository.put_part(other, io.BytesIO(b"abc"), 0, 3)
        assert caught.value.details["received"] == 2
        have = client.backend.have(pb.Have(digests=[other]))
        assert list(have.received) == [2]
        repository.put_part(other, io.BytesIO(b"c"), 2, 3)
        repository.put_part(other, io.BytesIO(b"abc"), 0, 3)
        assert repository.locate(other).read_bytes() == b"abc" and repository.check(content=True) == []


def test_the_outbox_does_not_fill_the_disk(tmp_path, serve, monkeypatch):
    from cairn import outbox

    monkeypatch.setattr(outbox, "PATIENCE", 1)
    repository = Repository(tmp_path / "repo")
    port, stop = serve(repository)
    with cairn.Client(f"http://127.0.0.1:{port}", project="demo", token="secret", state=tmp_path / "state") as client:
        client._limit = 1_000_000
        run = client.run("slow", console=False)
        for i in range(30):  # a server that takes it only slows logging down
            run.log({"f": cairn.File(os.urandom(200_000)), "i": i})
        run.flush()
        stop()
        logged = 30
        with pytest.raises(cairn.Full, match="wait for http"):
            for i in range(30, 60):
                run.log({"f": cairn.File(os.urandom(200_000)), "i": i})
                logged += 1
        assert 34 <= logged <= 37
        assert sum(p.stat().st_size for p in client.outbox.folder.rglob("*") if p.is_file()) < 2_000_000
        serve(repository, port)
        run.flush(timeout=20)
        run.log(i=99)
        assert len(run.finish(timeout=20).series("i")) == logged + 1


def test_provenance_is_kept_and_can_be_turned_off(client, tmp_path, monkeypatch):
    import gzip
    import tarfile

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), "-c", "user.email=a@example.invalid", "-c", "user.name=a", *args], text=True).strip()

    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr("sys.orig_argv", ["python", "train.py", "--token", "hidden", "--epochs", "2"])
    git("init", "-q")
    (root / "train.py").write_text("rate = 1\n")
    (root / "uv.lock").write_text("version = 1\n")
    git("add", ".")
    git("commit", "-qm", "first")
    (root / "train.py").write_text("rate = 2\n")
    (root / ".env").write_text("SECRET=1\n")

    def made(name, **options):
        with client.run(name, {"lr": 1}, {"why": "test"}, **options):
            (root / "train.py").write_text("rate = 3\n")
        (root / "train.py").write_text("rate = 2\n")
        return client.get(f"runs/{name}")

    def names(run, archive):
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(run.read(f"_about/{archive}")))) as found:
            return {m.name: found.extractfile(m).read() for m in found}

    assert made("off").get("_about") == {"why": "test"}
    monkeypatch.delenv("CAIRN_PROVENANCE")
    client._provenance = True
    run = made("on")
    about = run["_about"]
    assert about["git"]["head"] == git("rev-parse", "HEAD") and about["git"]["dirty"] and about["why"] == "test" and about["python"]
    assert about["command"] == ["python", "train.py", "--token", "<redacted>", "--epochs", "2"] and about["cwd"] == str(root)
    assert b"+rate = 2" in run.read("_about/git.diff") and names(run, "source.tar.gz") == {"train.py": b"rate = 2\n", "uv.lock": b"version = 1\n"}
    assert b'"pytest"' in run.read("_about/packages.json")
    assert list(run.files()) == [] and len(list(run.files("_about/**"))) == 3 and client.files(text="git.diff") == []
    assert client.find("runs/on", where=[f"_about/git/head = {git('rev-parse', 'HEAD')}"])
    bare = made("bare", capture_source=False)
    assert bare["_about"]["git"]["head"] and [f.path for f in bare.files("_about/**")] == ["_about/lockfiles.tar.gz", "_about/packages.json"]
    assert made("none", provenance=False)["_about"] == {"why": "test"}
    with client.artifact("datasets", "d") as data, client.artifact("datasets", "e", provenance=True) as other:
        pass
    assert data.get("_about") is None and other["_about"]["git"]["head"]
    (root / ".cairn.toml").write_text("provenance = false\ncapture_source = false\n")
    with cairn.Client(tmp_path / "other") as quiet:
        assert (quiet._provenance, quiet._source) == (False, False) and quiet.run("r").finish().get("_about") is None


@pytest.mark.parametrize("kind", ["local", "remote"])
def test_logging_from_scripts(tmp_path, serve, monkeypatch, capsys, kind):
    repository = Repository(tmp_path / "repo")
    monkeypatch.setenv("CAIRN_TOKEN", "secret")
    monkeypatch.setenv("CAIRN_STATE_DIR", str(tmp_path / "state"))
    repo = f"http://127.0.0.1:{serve(repository)[0]}" if kind == "remote" else str(tmp_path / "repo")

    def cli(*args, code=0):
        assert main(["--repo", repo, "--project", "demo", *args]) == code
        return capsys.readouterr().out.strip()

    run = cli("start", "runs/job", "lr=0.1", "name=mlp")
    (tmp_path / "out.txt").write_text("content")
    cli("log", run, "loss=0.5", "step=1")
    cli("log", "runs/job@live", "loss=0.25", "step=2", "note=a b=c", "--file", f"out.txt={tmp_path / 'out.txt'}")
    for wrong in ([], ["loss"], ["loss=1", "loss=2"], ["_x=1"], ["a=1", "--file", "a=b"]):
        cli("log", run, *wrong, code=1)
    assert cli("finish", run) == "runs/job@v1"
    cli("log", run, "loss=1", code=1)
    other = cli("start", "runs/job")
    assert cli("finish", other, "--outcome", "failed") == "runs/job@v2"
    with cairn.Client(tmp_path / "repo", project="demo") as client:
        found = client.get("runs/job")
        assert found.series("loss", x="step") == [(1.0, 0.5), (2.0, 0.25)] and found.config == {"lr": 0.1, "name": "mlp"}
        assert found["note"] == "a b=c" and found.read("out.txt") == b"content" and client.get("runs/job@v2").outcome == "failed"
    assert not list((tmp_path / "state").rglob("outbox.sqlite3"))
    repository.close()


def test_projects_and_the_token_of_a_server(tmp_path, monkeypatch, capsys):
    from cairn.__main__ import new_token
    from cairn.client import settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAIRN_TOKEN", raising=False)
    token = new_token()
    assert (tmp_path / ".cairn/token").stat().st_mode & 0o777 == 0o600 and settings()["token"] == token
    inside = tmp_path / "project"
    inside.mkdir()
    monkeypatch.chdir(inside)
    assert settings()["token"] == token
    (inside / ".cairn.toml").write_text('repo = "http://elsewhere"\n')
    assert "token" not in settings()
    (inside / "mine").write_text("own\n")
    (inside / ".cairn.toml").write_text('repo = "http://elsewhere"\ntoken_file = "mine"\n')
    assert settings()["token"] == "own"
    for project, name in (("p", "a"), ("p", "b"), ("q", "a")):
        with cairn.Client(tmp_path / "repo", project=project) as client:
            client.run(name).finish()
    with cairn.Client(tmp_path / "repo") as client:
        client.artifact("datasets", "d").finish()
        assert {k: v["artifacts"] for k, v in client.projects().items()} == {"default": {"datasets": 1}, "p": {"runs": 2}, "q": {"runs": 1}}
    capsys.readouterr()
    assert main(["--repo", str(tmp_path / "repo"), "projects"]) == 0 and "p\truns=2\n" in capsys.readouterr().out


def test_the_protocol_is_frozen():
    """What programs and repositories hold stays readable: fields are only ever added, under
    new numbers, and then here too."""
    from pathlib import Path

    from google.protobuf.descriptor_pb2 import FileDescriptorProto

    from cairn.operations import ANSWERS, REQUESTS

    file = FileDescriptorProto.FromString(pb.DESCRIPTOR.serialized_pb)
    lines = [f"{message.name} {field.number} {field.name} {field.type} {field.label} {field.type_name}".rstrip() for message in file.message_type for field in message.field]
    lines += [f"{name}({REQUESTS[name].DESCRIPTOR.name}) {ANSWERS[name].DESCRIPTOR.name}" for name in sorted(REQUESTS)]
    frozen = Path(__file__).with_name("protocol_v1.txt").read_text().splitlines()
    assert sorted(lines) == frozen and REQUESTS.keys() == ANSWERS.keys()


FROZEN = (
    "0a070a016e120208010a070a0162120210010a070a0169120218030a0e0a0166120921000000000000e03f0a090a017412042a02c3a90a080a017912"
    "033201ff0a0b0a016c12063a040a0218020a100a016d120b42090a070a016b120218020a3a0a04626c6f6212324a300a20000102030405060708090a"
    "0b0c0d0e0f101112131415161718191a1b1c1d1e1f10051a0a746578742f706c61696e0a0d0a03726566120652040a026162"
)


def test_records_keep_their_bytes():
    blob = pb.Blob(digest=bytes(range(32)), size=5, media_type="text/plain")
    record = {"n": None, "b": True, "i": -2, "f": 0.5, "t": "é", "y": b"\xff", "l": [1], "m": {"k": 1}, "blob": blob, "ref": pb.Ref(artifact="ab")}
    data = bytes.fromhex(FROZEN)
    assert values.record(record) == data
    read = values.read(data)
    assert {k: read[k] for k in record if k not in ("blob", "ref")} == {k: v for k, v in record.items() if k not in ("blob", "ref")}
    assert read["blob"] == blob and read["ref"].artifact == "ab"

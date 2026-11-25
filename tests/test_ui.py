import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from starlette.testclient import TestClient
from test_cairn import serve  # noqa: F401

import cairn
from cairn import cairn_pb2 as pb
from cairn.server import MEDIA
from cairn.store import Repository
from cairn_ui import reports as module
from cairn_ui.__main__ import main
from cairn_ui.reports import Reports, merge
from cairn_ui.server import create_app
from cairn_ui.workspaces import Workspaces

LAYOUT = {"version": 1, "id": "i", "name": "n", "search": "", "versions": "all", "status": "", "selected": None,
          "sections": [{"id": "s", "title": "S", "collapsed": False}], "cards": []}


@pytest.fixture(params=["local", "remote"])
def pages(request, tmp_path, serve):  # noqa: F811
    repository = Repository(tmp_path / "repo")
    where = tmp_path / "repo" if request.param == "local" else f"http://127.0.0.1:{serve(repository)[0]}"
    with cairn.Client(where, project="demo", token="secret", state=tmp_path / "state") as client:
        with client.artifact("datasets", "d") as data:
            data.log({"x": 1})
        with client.run("r", {"lr": 0.5}) as run:
            run.use(data)
            for i in range(25_000 if request.param == "local" else 300):
                run.log(i=i)
            run.log({"index.html": cairn.File(b"<b>", "text/html"), "a.svg": cairn.File(b"<svg/>", "image/svg+xml"), "v.mp4": cairn.File(bytes(range(256)) * 100, "video/mp4")})
        workspaces = Workspaces(tmp_path / "ui.sqlite3")
        with TestClient(create_app(client, workspaces, Reports(tmp_path / "reports", tmp_path / "ui.sqlite3"), token="login"), base_url="http://here") as browser:
            browser.ids = {"run": run.id, "data": data.id}
            yield browser
        workspaces.close()
    repository.close()


def ask(browser, name, message, answer):
    response = browser.post(f"/ui-api/do/{name}", content=message.SerializeToString(), headers={"content-type": MEDIA})
    assert response.status_code == 200, response.text
    return answer.FromString(response.content)


def login(browser):
    assert browser.post("/ui-api/login", json={"token": "login"}).status_code == 200


def test_pages_need_a_login_and_changes_the_same_origin(pages):
    for path in ("/", "/p/demo", "/favicon.svg"):
        assert pages.get(path).status_code == 200
    for path in ("/ui-api/session", "/ui-api/workspaces", f"/ui-api/lineage/{pages.ids['run']}/1", f"/ui-api/files/{pages.ids['run']}/index.html", f"/ui-api/content/{'0' * 64}"):
        assert pages.get(path).status_code == 401
    assert pages.post("/ui-api/do/projects", content=b"", headers={"content-type": MEDIA}).status_code == 401
    assert pages.post("/ui-api/login", json={"token": "wrong"}).status_code == 401
    login(pages)
    assert ask(pages, "projects", pb.Projects(), pb.ProjectList).items[0].artifacts == {"datasets": 1, "runs": 1}
    tag = pb.Label(artifact=pages.ids["run"])
    tag.set.entries.add(key="t").value.null = True
    evil = {"content-type": MEDIA, "origin": "http://evil"}
    assert pages.post("/ui-api/do/label", content=tag.SerializeToString(), headers=evil).status_code == 401
    assert pages.put("/ui-api/workspaces", json={}, headers={"origin": "http://evil"}).status_code == 401
    assert pages.post(f"/ui-api/stop/{pages.ids['run']}", json={}, headers={"origin": "http://evil"}).status_code == 401
    assert ask(pages, "label", tag, pb.Artifact).tags.entries[0].key == "t"
    for name in ("append", "open", "finish", "link_blob", "have", "nothing"):
        assert pages.post(f"/ui-api/do/{name}", content=b"", headers={"content-type": MEDIA}).status_code == 404
    assert pages.post("/ui-api/do/find", content=b"", headers={"content-type": "text/plain"}).status_code == 400
    assert pages.post("/ui-api/logout", json={}).status_code == 200 and pages.get("/ui-api/session").status_code == 401


def test_records_come_as_one_stream(pages):
    login(pages)
    after = {pages.ids["run"]: 5, pages.ids["data"]: 0}
    data, found, at = pages.post("/ui-api/observations", json={"after": after}).content, {}, 0
    while data[at] != 2:
        kind, size = data[at], int.from_bytes(data[at + 1 : at + 5], "big")
        content, at = data[at + 5 : at + 5 + size], at + 5 + size
        if kind == 0:
            current = pb.Artifact.FromString(content)
            found[current.id] = []
        else:
            found[current.id] += pb.RecordPage.FromString(content).ordinals
    assert list(found) == list(after) and found[pages.ids["data"]] == [1, 2, 3] and at + 5 == len(data)
    assert found[pages.ids["run"]] == list(range(6, current.records + 1 if current.id == pages.ids["run"] else len(found[pages.ids["run"]]) + 6))
    for wrong in ({}, {"after": []}, {"after": {"x": -1}}):
        assert pages.post("/ui-api/observations", json=wrong).status_code == 400
    assert pages.post("/ui-api/observations", json={"after": {"0" * 32: 0}}).status_code == 404


def test_content_never_runs_as_a_page_of_this_site(pages):
    login(pages)
    run = pages.ids["run"]
    files = {f.path: f for f in ask(pages, "files", pb.Files(artifact=run), pb.FoundFiles).items}
    for path in ("index.html", "a.svg"):
        for response in (pages.get(f"/ui-api/content/{files[path].blob.digest.hex()}?type={files[path].blob.media_type}"), pages.get(f"/ui-api/files/{run}/{path}")):
            policy = response.headers["content-security-policy"]
            assert response.status_code == 200 and policy.startswith("sandbox; default-src 'none'") and response.headers["x-content-type-options"] == "nosniff"
    video = f"/ui-api/content/{files['v.mp4'].blob.digest.hex()}?type=video/mp4"
    part = pages.get(video, headers={"range": "bytes=256-511"})
    assert part.status_code == 206 and part.content == bytes(range(256)) and part.headers["content-type"] == "video/mp4" and "25600" in part.headers["content-range"]
    assert len(pages.get(video).content) == 25600
    url = pages.get(f"/ui-api/viewer-tokens/{run}").json()["url"]
    pages.cookies.clear()
    shown = pages.get(url + "index.html")
    assert shown.text == "<b>" and shown.headers["content-type"].startswith("text/html") and "sandbox allow-scripts" in shown.headers["content-security-policy"]
    assert pages.get(url + "none.html").status_code == 404 and pages.get(url.replace("viewers/", "viewers/x") + "index.html").status_code == 401


def test_lineage_and_stopping(pages):
    login(pages)
    graph = pages.get(f"/ui-api/lineage/{pages.ids['data']}/1").json()
    run = graph["nodes"][pages.ids["run"]]
    assert graph["nodes"][pages.ids["data"]]["consumers"] == [pages.ids["run"]] and (run["name"], run["status"], run["kind"], run["inputs"]) == ("runs/r", "completed", "run", None)
    assert pages.get(f"/ui-api/lineage/{pages.ids['data']}/9").status_code == 400
    ask(pages, "delete", pb.Delete(artifact=pages.ids["data"]), pb.Artifact)
    assert pages.get(f"/ui-api/lineage/{pages.ids['run']}/1").json()["nodes"][pages.ids["data"]]["deleted"]


def test_workspaces(pages, tmp_path):
    login(pages)
    saved = pages.put("/ui-api/workspaces", json={"project": "demo", "name": "n", "layout": LAYOUT, "expected_revision": 0}).json()
    assert saved["revision"] == 1 and pages.get(f"/ui-api/workspaces/{saved['id']}").json()["layout"] == LAYOUT
    again = {"project": "demo", "name": "n", "layout": LAYOUT, "expected_revision": 0}
    assert pages.put("/ui-api/workspaces", json=again).status_code == 409
    assert pages.put("/ui-api/workspaces", json={**again, "expected_revision": 1}).json()["revision"] == 2
    assert pages.put("/ui-api/workspaces", json={**again, "layout": {**LAYOUT, "odd": 1}}).status_code == 400
    assert pages.put("/ui-api/workspaces", json={"project": "demo"}).status_code == 400
    assert [w["name"] for w in pages.get("/ui-api/workspaces?project=demo").json()] == ["n"] and pages.get("/ui-api/workspaces?project=other").json() == []


@pytest.fixture
def running(tmp_path, monkeypatch):
    """A cairn-ui on a port, for commands and browsers."""
    (tmp_path / "state").mkdir()
    started = []

    def start(port=0):
        client = cairn.Client(tmp_path / "repo", project="p")
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        app = create_app(client, Workspaces(tmp_path / "state/ui.sqlite3"), Reports(tmp_path / "state/reports", tmp_path / "state/ui.sqlite3"), token="login")
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=1))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        thread.start()
        while not server.started:
            time.sleep(0.01)

        def stop():
            server.should_exit = True
            thread.join()
            listener.close()
            client.close()

        started.append(stop)
        monkeypatch.setenv("CAIRN_UI", f"http://127.0.0.1:{listener.getsockname()[1]}")
        monkeypatch.setenv("CAIRN_TOKEN", "login")
        return listener.getsockname()[1], stop

    yield start
    for stop in started:
        stop()


def test_workspaces_by_command(running, tmp_path, capsys, monkeypatch):
    running()
    (tmp_path / "layout.json").write_text(json.dumps(LAYOUT))

    def cli(*args, code=None):
        assert main(["workspace", *args, "--project", "p"]) == code
        return capsys.readouterr().out

    write = ["write", "n", "--file", str(tmp_path / "layout.json"), "--expected", "0"]
    assert json.loads(cli(*write))["revision"] == 1
    cli(*write, code=1)
    assert json.loads(cli("show", "n"))["layout"] == LAYOUT and [w["name"] for w in json.loads(cli("list"))] == ["n"]
    assert cli("delete", "n") == "" and json.loads(cli("list")) == []
    cli("show", "n", code=1)
    monkeypatch.setenv("CAIRN_TOKEN", "wrong")
    cli("list", code=1)


def test_reports_by_command(running, tmp_path, capsys):
    running()

    def cli(*args, code=None):
        assert main(["report", *args, "--project", "p/q"]) == code
        return capsys.readouterr().out

    def text(name, content):
        (tmp_path / name).write_text(content)
        return str(tmp_path / name)

    cli("write", "a/plan", "--file", text("one", "# Plan\n\né first\nsecond 😀\n"))
    assert cli("show", "a/plan") == "# Plan\n\né first\nsecond 😀\n" and cli("list") == "a/plan\n"
    stored = tmp_path / "state/reports/p%2Fq/a/plan.md"
    assert stored.read_text() == cli("show", "a/plan")
    cli("edit", "a/plan", "--old", "😀", "--new", "done 🎉")
    cli("edit", "a/plan", "--old", "missing", "--new", "x", code=1)
    cli("edit", "a/plan", "--old", "", "--new", "x", code=1)
    base = text("base", cli("show", "a/plan"))
    cli("edit", "a/plan", "--old", "# Plan", "--new", "# The plan")
    cli("write", "a/plan", "--base", base, "--file", text("two", "# Plan\n\né first\nsecond done 🎉\nthird\n"))
    assert cli("show", "a/plan") == "# The plan\n\né first\nsecond done 🎉\nthird\n" == stored.read_text()
    stored.write_text(stored.read_text() + "by another program\n")
    assert cli("show", "a/plan").endswith("third\nby another program\n")
    for wrong in ("../x", "a/../b", ".hidden", "a//b", "a:b"):
        cli("write", wrong, "--file", base, code=1)
    cli("delete", "a/plan")
    cli("show", "a/plan", code=1)
    assert cli("list") == "" and list((tmp_path / "state/reports").rglob("*.md")) == []


@pytest.mark.parametrize("base, ours, theirs, merged", [
    ("a\nb\nc\n", "a\nB\nc\n", "a\nb\nc\nd\n", "a\nB\nc\nd\n"), ("a\nb\n", "x\na\nb\n", "a\nb2\n", "x\na\nb2\n"), ("a\n", "a\n", "", ""),
    ("a\nb\n", "a\nours\n", "a\ntheirs\n", "a\nours\ntheirs\n"), ("a\n", "a\nsame\n", "a\nsame\n", "a\nsame\n"), ("", "ours\n", "theirs\n", "ours\ntheirs\n"),
    ("1\n2\n3\n4\n5\n", "1\n3\n4\nfive\n", "one\n2\n3\n4\n5\n6\n", "one\n3\n4\nfive\n6\n")])
def test_merging(base, ours, theirs, merged):
    assert merge(base, ours, theirs) == merged and merge(base, base, theirs) == theirs and merge(base, ours, base) == ours


class Person:
    """A browser with a report open."""

    def __init__(self, port, epoch, doc=None):
        from pycrdt import Doc, Text, create_sync_message
        from websockets.sync.client import connect

        self.doc = doc or Doc()
        self.text = self.doc.get("text", type=Text)
        self.socket = connect(f"ws://127.0.0.1:{port}/ui-api/live/report?project=p&name=r&epoch={epoch}", additional_headers={"Authorization": "Bearer login"}, origin=f"http://127.0.0.1:{port}")
        self.socket.send(create_sync_message(self.doc))

    def settle(self, seconds=0.3):
        from pycrdt import handle_sync_message

        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            try:
                data = self.socket.recv(timeout=left)
            except TimeoutError:
                break
            if data[0] == 0 and (reply := handle_sync_message(data[1:], self.doc)):
                self.socket.send(reply)
        return str(self.text)

    def type(self, at, text):
        from pycrdt import create_update_message

        before = self.doc.get_state()
        self.text.insert(at, text)
        self.socket.send(create_update_message(self.doc.get_update(before)))


def test_people_and_agents_write_at_the_same_time(running, tmp_path, monkeypatch):
    import httpx
    from websockets.exceptions import ConnectionClosed, InvalidStatus

    monkeypatch.setattr(module, "SAVE", 0.05)
    port, stop = running()
    http = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": "Bearer login"})
    made = http.post("/ui-api/report?project=p&name=r", json={"text": "one\ntwo\n"}).json()
    assert http.post("/ui-api/report?project=p&name=r", json={}).status_code == 409
    anna, ben = Person(port, made["epoch"]), Person(port, made["epoch"])
    assert anna.settle() == ben.settle() == "one\ntwo\n"
    anna.type(0, "A")
    ben.type(len(b"one\ntwo\n"), "B\n")
    assert http.post("/ui-api/report?project=p&name=r", json={"old": "two", "new": "zwei"}).json()["text"]
    path = tmp_path / "state/reports/p/r.md"
    assert anna.settle() == ben.settle() == "Aone\nzwei\nB\n" == path.read_text()
    path.write_text("Aone\nzwei\nB\nfrom vim\n")
    ben.type(0, "B")
    assert anna.settle(2) == ben.settle() == "BAone\nzwei\nB\nfrom vim\n"
    # The server goes; what is typed meanwhile is there when it comes back.
    stop()
    ben.socket.close()
    anna.text.insert(0, "offline ")
    path.write_text(path.read_text() + "while away\n")
    running(port)
    anna = Person(port, made["epoch"], anna.doc)
    anna.settle()
    text = http.get("/ui-api/report?project=p&name=r").json()["text"]
    assert text == anna.settle() == "offline BAone\nzwei\nB\nfrom vim\nwhile away\n"
    # A report that is another one now is not joined, and nobody joins without the token.
    for epoch, headers in (("other", {}), (made["epoch"], {})):
        from websockets.sync.client import connect

        address = f"ws://127.0.0.1:{port}/ui-api/live/report?project=p&name=r&epoch={epoch}"
        with pytest.raises((ConnectionClosed, InvalidStatus)):
            if epoch == "other":
                headers = {"Authorization": "Bearer login"}
            with connect(address, additional_headers=headers, origin=f"http://127.0.0.1:{port}") as other:
                other.recv(timeout=2)
    with pytest.raises((ConnectionClosed, InvalidStatus)):
        with connect(f"ws://127.0.0.1:{port}/ui-api/live/report?project=p&name=r&epoch={made['epoch']}", additional_headers={"Authorization": "Bearer login"}, origin="http://evil") as other:
            other.recv(timeout=2)
    http.close()
    anna.socket.close()


def test_sizes_of_images_and_videos(tmp_path):
    import io
    import struct

    Pillow = pytest.importorskip("PIL.Image")
    from cairn_ui.sizes import size_of

    def size(data, media):
        return size_of(lambda at, count: data[at : at + count], len(data), media)

    for kind in ("PNG", "JPEG", "GIF", "WEBP"):
        for options in ({}, {"lossless": True}) if kind == "WEBP" else ({},):
            out = io.BytesIO()
            Pillow.new("RGB", (123, 45)).save(out, kind, **options)
            assert size(out.getvalue(), "image/x") == (123, 45)
    assert size(b'<?xml version="1.0"?><svg width="72pt" height="36pt">', "image/svg+xml") == (96, 48)
    assert size(b"<svg viewBox='0 0 30.4 10' xmlns='x'>", "image/svg+xml") == (30, 10) and size(b"<svg>", "image/svg+xml") is None
    clip = (Path(__file__).parents[1] / "examples/showcase/assets/wave-evolution.mp4").read_bytes()
    assert min(size(clip, "video/mp4")) > 100
    for broken in (b"", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF8", b"RIFF1234WEBP", clip[:40], struct.pack(">I4s", 8, b"ftyp") + struct.pack(">I4s", 0, b"moov")):
        assert size(broken, "image/x") is None and size(broken, "video/mp4") is None


def test_a_report_says_the_size_of_what_it_shows(pages):
    login(pages)
    text = "![v](cairn:runs/r@v1#v.mp4) ![s](cairn:runs/r@v1#a.svg) ![n](cairn:runs/none@v1#x.png) ![m](cairn:runs/r@v1#missing) [l](cairn:runs/r@v1#a.svg)"
    assert pages.put("/ui-api/report?project=demo&name=sized", json={"text": text}).status_code == 200
    found = pages.get("/ui-api/report/files?project=demo&name=sized").json()
    assert found.keys() == {"runs/r@v1#v.mp4", "runs/r@v1#a.svg"} and found["runs/r@v1#v.mp4"]["media_type"] == "video/mp4" and found["runs/r@v1#v.mp4"]["size"] == 25600
    assert pages.get("/ui-api/report/files?project=demo&name=none").status_code == 404


def test_reports_need_a_login_and_the_same_origin(pages):
    assert pages.get("/ui-api/reports?project=p").status_code == 401 and pages.put("/ui-api/report?project=p&name=r", json={"text": ""}).status_code == 401
    login(pages)
    assert pages.put("/ui-api/report?project=p&name=r", json={"text": "x"}, headers={"origin": "http://evil"}).status_code == 401
    assert pages.put("/ui-api/report?project=p&name=r", json={"text": "x"}).json()["text"] == "x"
    assert pages.put("/ui-api/report?project=p&name=r", content="{}", headers={"content-type": "text/plain"}).status_code == 400
    assert pages.put("/ui-api/report?project=p&name=r", json={"text": 1}).status_code == 400
    assert [r["name"] for r in pages.get("/ui-api/reports?project=p").json()] == ["r"] and pages.get("/ui-api/report?project=p&name=none").status_code == 404
    assert pages.delete("/ui-api/report?project=p&name=r", headers={"origin": "http://evil"}).status_code == 401
    assert pages.delete("/ui-api/report?project=p&name=r").status_code == 200 and pages.get("/ui-api/reports?project=p").json() == []


def test_the_schema_of_the_pages_is_the_protocol():
    path = Path(__file__).parents[1] / "tools/ui_schema.py"
    spec = importlib.util.spec_from_file_location("ui_schema", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.TARGET.read_text() == module.schema()


def test_the_command_serves(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = {**os.environ, "PYTHONPATH": os.getcwd()}
    environment.pop("CAIRN_TOKEN", None)
    server = subprocess.Popen([sys.executable, "-m", "cairn_ui", "--port", str(port)], cwd=tmp_path, env=environment, stderr=subprocess.PIPE)
    try:
        import httpx

        for _ in range(100):
            try:
                assert httpx.get(f"http://127.0.0.1:{port}/ui-api/session").status_code == 401
                break
            except httpx.TransportError:
                time.sleep(0.1)
        token = (tmp_path / ".cairn/token").read_text().strip()
        assert httpx.post(f"http://127.0.0.1:{port}/ui-api/login", json={"token": token}).status_code == 200
    finally:
        server.terminate()
        output = server.communicate()[1].decode()
    assert token not in output

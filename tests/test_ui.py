import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from test_cairn import serve  # noqa: F401

import cairn
from cairn import cairn_pb2 as pb
from cairn.server import MEDIA
from cairn.store import Repository
from cairn_ui.__main__ import main
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
        with TestClient(create_app(client, workspaces, token="login"), base_url="http://here") as browser:
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


def test_workspaces_by_command(tmp_path, capsys):
    base = ["--state", str(tmp_path / "ui.sqlite3"), "workspace"]
    (tmp_path / "layout.json").write_text(json.dumps(LAYOUT))
    assert main([*base, "write", "n", "--project", "p", "--file", str(tmp_path / "layout.json"), "--expected", "0"]) == 0
    assert main([*base, "write", "n", "--project", "p", "--file", str(tmp_path / "layout.json"), "--expected", "0"]) == 1
    capsys.readouterr()
    assert main([*base, "show", "n", "--project", "p"]) == 0 and json.loads(capsys.readouterr().out)["layout"] == LAYOUT
    assert main([*base, "delete", "n", "--project", "p"]) == 0 and main([*base, "show", "n", "--project", "p"]) == 1
    capsys.readouterr()
    assert main([*base, "list", "--project", "p"]) == 0 and json.loads(capsys.readouterr().out) == []


def test_the_schema_of_the_pages_is_the_protocol():
    path = Path(__file__).parents[1] / "tools/ui_schema.py"
    spec = importlib.util.spec_from_file_location("ui_schema", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.TARGET.read_text() == module.schema()


def test_the_command_serves(tmp_path):
    import socket

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

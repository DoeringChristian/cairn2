import json
import os
import signal
import subprocess
import sys
import time
from contextlib import closing

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from test_http import serve

from cairn import Conflict, Pending
from cairn.cli import main
from cairn.server import create_app
from cairn.storage import Repository, writers

JOB = """
import os, sys, time, cairn
from cairn import Client
def ready():
    print("ready", file=sys.__stdout__, flush=True)
def hold():
    ready()
    time.sleep(60)
"""
INIT = "run = cairn.init('job', capture_source=False)\nrun.log(step=0)\n"
WITH = "with cairn.init('job', capture_source=False) as run:\n    run.log(step=0)\n    hold()\n"
CLIENT = "run = Client().run('job', capture_source=False)\nrun.log(step=0)\n"
FORK = "child = os.fork()\nif not child: hold()\nos.kill(child, 15)\nos.waitpid(child, 0)\n"


def run(tmp_path, code, kill=None, **env):
    values = {"CAIRN_REPO": str(tmp_path / "repo"), "CAIRN_STATE_DIR": str(tmp_path / "state")}
    process = subprocess.Popen(
        [sys.executable, "-c", JOB + code],
        env={**os.environ, "CAIRN_PROJECT": "demo", **values, **env},
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if kill:
        assert "ready" in process.stdout.readline()
        process.send_signal(kill)
    process.communicate(timeout=50)
    return process.returncode


def job(repo):
    [found] = repo.list_artifacts(project="demo", namespace="runs")["items"]
    return found["state"], (found["manifest"] or {}).get("outcome")


@pytest.mark.parametrize(
    ("code", "kill", "ended"),
    [
        (WITH, signal.SIGTERM, ("finished", "cancelled")),
        (INIT + "hold()", signal.SIGTERM, ("finished", "cancelled")),
        (INIT + "hold()", signal.SIGINT, ("finished", "failed")),
        (INIT + "raise RuntimeError('boom')", None, ("finished", "failed")),
        (INIT + "sys.exit(1)", None, ("finished", "failed")),
        (INIT, None, ("finished", "completed")),
        (INIT + FORK, None, ("finished", "completed")),
        (INIT + "hold()", signal.SIGKILL, ("live", None)),
        (CLIENT, None, ("live", None)),
    ],
)
def test_runs_end_with_their_process(tmp_path, code, kill, ended):
    run(tmp_path, code, kill)
    with closing(Repository(tmp_path / "repo")) as repo:
        assert job(repo) == ended


def test_an_application_keeps_its_own_sigterm_handler(tmp_path):
    handler = "import signal\nsignal.signal(15, lambda *_: os._exit(7))\n"
    assert run(tmp_path, handler + INIT + "hold()", signal.SIGTERM) == 7


def test_remote_runs_end_with_their_process(tmp_path):
    with closing(Repository(tmp_path / "remote")) as repo, serve(repo) as url:
        run(tmp_path, INIT + "hold()", signal.SIGTERM, CAIRN_REPO=url, CAIRN_TOKEN="test-token")
        assert job(repo) == ("finished", "cancelled")
        assert repo.liveness(project="demo") == []


def silence(repo, stamp):
    with repo._transaction(write=True) as conn:
        conn.execute(writers.update().values(heartbeat=stamp))
        conn.execute(sa.text("UPDATE cairn_artifacts SET created_at = '2026-01-01T00:00:00+00:00'"))


def test_silent_processes_lose_contact_and_can_be_stopped(tmp_path):
    with closing(Repository(tmp_path)) as repo:
        started = repo.start("demo", "runs", "job")
        identifier, writer = started["artifact"]["id"], started["writer"]["id"]
        [seen] = repo.liveness(project="demo")
        assert (seen["heartbeat"], seen["lost"]) == (False, False)
        assert repo.heartbeat(identifier, writer) == {"open": True}
        with pytest.raises(Conflict, match="was alive"):
            repo.stop(identifier)
        silence(repo, "2026-01-01T00:00:00+00:00")
        [seen] = repo.liveness(project="demo")
        assert seen["lost"] and seen["last_seen"].startswith("2026-01-01")
        repo.heartbeat(identifier, writer)
        assert not repo.liveness(project="demo")[0]["lost"]
        assert repo.stop(identifier, force=True)["manifest"]["outcome"] == "incomplete"
        assert repo.stop(identifier)["state"] == "finished"
        assert repo.heartbeat(identifier, writer) == {"open": False}


def test_stopping_keeps_a_requested_completion(tmp_path):
    with closing(Repository(tmp_path)) as repo:
        identifier = repo.start("demo", "runs", "job")["artifact"]["id"]
        with pytest.raises(Pending):
            repo.finish(identifier, value={"loss": 1}, outcome="failed")
        manifest = repo.stop(identifier)["manifest"]
        assert (manifest["outcome"], manifest["value"]) == ("failed", {"loss": 1})


def test_cli_and_browsers_stop_runs(tmp_path, capsys):
    with closing(Repository(tmp_path)) as repo:
        repo.start("demo", "runs", "a")
        other = repo.start("demo", "runs", "b")["artifact"]["id"]
        base = ["--repo", str(tmp_path), "--project", "demo", "artifact"]
        assert main([*base, "stop", "runs/a@live"]) == 0
        assert json.loads(capsys.readouterr().out)["manifest"]["outcome"] == "incomplete"
        with TestClient(create_app(repo, token="test-token", ui=True)) as browser:
            path = f"/ui-api/artifacts/{other}/stop"
            assert browser.post(path, json={}).status_code == 401
            browser.post("/ui-api/login", json={"token": "test-token"})
            assert browser.get("/v1/liveness?project=demo").json()[0]["artifact_id"] == other
            assert browser.post(path, json={}).json()["state"] == "finished"


def test_heartbeats_reach_the_server(tmp_path, monkeypatch):
    from cairn import Client

    monkeypatch.setenv("CAIRN_STATE_DIR", str(tmp_path / "state"))
    with closing(Repository(tmp_path / "remote")) as repo, serve(repo) as url:
        with (
            Client(url, token="test-token", project="demo") as client,
            client.run("job", provenance=False, capture_output=False),
        ):
            deadline = time.monotonic() + 5
            while not (seen := repo.liveness(project="demo")) or not seen[0]["heartbeat"]:
                assert time.monotonic() < deadline
                time.sleep(0.05)
        assert job(repo) == ("finished", "completed")

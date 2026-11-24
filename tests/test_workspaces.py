import io
import json
import sqlite3
from contextlib import closing

import pytest
from test_http import serve

from cairn import Client, Conflict, NotFound, Unsupported, ValidationError
from cairn.cli import main
from cairn.ops import export_repository, import_repository
from cairn.storage import Repository


def layout(name="training", search="workspace-demo/", **changes):
    card = {
        "id": "loss",
        "title": "Loss",
        "kind": "scalar",
        "section": "overview",
        "width": 6,
        "height": 360,
        "filters": [{"field": "split", "op": "eq", "value": "test"}],
        "filterMode": "all",
        "x": "step",
        "y": "loss",
        "value": "",
        "split": ["@run"],
        "pointOrder": {"field": "step", "descending": False},
        "lineOrder": "label",
        "marks": "lines",
        "logX": False,
        "logY": True,
        "mode": "gallery",
        "axis": "step",
        "matching": "exact",
        "columns": 2,
        "pageSize": 6,
        "caption": "",
        "fit": "contain",
        "linkAxes": False,
        "aggregation": {"center": "mean", "band": "std", "showMembers": False},
    }
    value = {
        "version": 1,
        "id": "layout-1",
        "name": name,
        "search": search,
        "versions": "latest",
        "status": "",
        "selected": None,
        "sections": [{"id": "overview", "title": "Overview", "collapsed": False}],
        "cards": [card],
        "view": "together",
    }
    return {**value, **changes}


def test_workspaces_keep_every_revision_and_refuse_stale_writes(tmp_path):
    with closing(Repository(tmp_path)) as repo:
        first = repo.write_workspace("demo", "training", layout(), expected_revision=0)
        assert first["revision"] == 1
        assert repo.write_workspace("demo", "training", layout())["revision"] == 1
        with pytest.raises(Conflict) as stale:
            repo.write_workspace("demo", "training", layout(search="x"), expected_revision=0)
        assert stale.value.details == {"revision": 1}
        written = repo.write_workspace(
            "demo", "training", layout(search="x"), expected_revision=1, author="agent"
        )
        assert written == {"id": first["id"], "project": "demo", "name": "training", "revision": 2}
        assert repo.write_workspace("other", "training", layout())["id"] != first["id"]
        [summary] = repo.workspaces(project="demo")
        assert (summary["name"], summary["revision"], summary["author"]) == ("training", 2, "agent")
        assert "layout" not in summary
        assert repo.workspace(first["id"])["layout"]["search"] == "x"
        repo.delete_workspace(first["id"])
        assert repo.workspaces(project="demo") == []
        with pytest.raises(NotFound):
            repo.workspace(first["id"])


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"name": "other"}, "differs from the workspace"),
        ({"version": 2}, "version must be 1"),
        ({"colour": "red"}, "unknown keys colour"),
        ({"sections": []}, "sections must be a nonempty list"),
        ({"versions": "some"}, "versions is latest or all"),
        ({"cards": [{**layout()["cards"][0], "kind": "bar"}]}, "kinds are scalar"),
        ({"cards": [{**layout()["cards"][0], "section": "gone"}]}, "names no section"),
        ({"cards": [{**layout()["cards"][0], "width": 13}]}, "width must be an integer"),
        ({"cards": [{**layout()["cards"][0], "titel": "x"}]}, "unknown keys titel"),
        ({"cards": [layout()["cards"][0]] * 2}, "card IDs repeat"),
    ],
)
def test_layouts_are_checked_as_the_workspace_page_checks_them(tmp_path, change, message):
    with closing(Repository(tmp_path)) as repo, pytest.raises(ValidationError, match=message):
        repo.write_workspace("demo", "training", layout(**change))


def test_workspaces_survive_export_and_import(tmp_path):
    with closing(Repository(tmp_path / "source")) as repo:
        repo.write_workspace("demo", "training", layout())
        repo.write_workspace("demo", "training", layout(search="x"))
        export_repository(repo, tmp_path / "export")
    with closing(Repository(tmp_path / "target")) as repo:
        import_repository(repo, tmp_path / "export")
        [summary] = repo.workspaces(project="demo")
        assert summary["revision"] == 2
        assert repo.workspace(summary["id"])["layout"] == layout(search="x")


def test_schema_10_repositories_gain_workspaces(tmp_path):
    with closing(Repository(tmp_path)) as repo:
        report = repo.create_report("demo", "study")
    with closing(sqlite3.connect(tmp_path / "metadata.sqlite3")) as conn, conn:
        conn.execute("DROP TABLE cairn_workspace_layouts")
        conn.execute("DROP TABLE cairn_workspaces")
        conn.execute("ALTER TABLE cairn_writers DROP COLUMN heartbeat")
        conn.execute("UPDATE cairn_meta SET value = '10' WHERE key = 'schema_version'")
    with pytest.raises(Unsupported, match="cairn repo migrate"):
        Repository(tmp_path)
    with closing(Repository(tmp_path, migrate=True)) as repo:
        assert repo.report(report["id"])["name"] == "study"
        assert repo.write_workspace("demo", "training", layout())["revision"] == 1


def test_clients_write_workspaces_by_name_over_http(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_STATE_DIR", str(tmp_path / "state"))
    repository = Repository(tmp_path / "repository")
    try:
        with serve(repository) as url, Client(url, token="test-token", project="demo") as client:
            written = client.write_workspace("training", layout(), expected_revision=0, by="agent")
            assert written["revision"] == 1
            with pytest.raises(Conflict):
                client.write_workspace("training", layout(search="x"), expected_revision=0)
            with pytest.raises(ValidationError, match="unknown keys"):
                client.write_workspace("training", layout(colour="red"))
            assert client.workspace("training")["layout"] == layout()
            assert [item["name"] for item in client.workspaces()] == ["training"]
            client.delete_workspace("training")
            with pytest.raises(NotFound):
                client.workspace("training")
    finally:
        repository.close()


def test_workspace_cli_writes_layouts_from_json(tmp_path, capsys, monkeypatch):
    base = ["--repo", str(tmp_path / "repo"), "--project", "demo", "workspace"]
    source = tmp_path / "layout.json"
    value = layout()
    del value["name"]
    source.write_text(json.dumps(value))
    assert main([*base, "write", "training", str(source), "--revision", "0", "--by", "agent"]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == 1
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(layout(search="x"))))
    assert main([*base, "write", "training", "--revision", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == 2
    assert main([*base, "show", "training"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert (shown["revision"], shown["layout"]["search"]) == (2, "x")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(shown)))
    assert main([*base, "write", "training", "--revision", "2"]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == 2
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    assert main([*base, "write", "training"]) != 0
    assert "not JSON" in capsys.readouterr().err
    assert main([*base, "list"]) == 0
    assert [item["name"] for item in json.loads(capsys.readouterr().out)] == ["training"]
    assert main([*base, "delete", "training"]) == 0
    capsys.readouterr()
    assert main([*base, "list"]) == 0
    assert json.loads(capsys.readouterr().out) == []

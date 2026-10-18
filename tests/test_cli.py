import json
import subprocess
import sys
from pathlib import Path

import pytest

from cairn.cli import main


def invoke(capsys, repo, *args):
    assert main(["--repo", str(repo), *args]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    return json.loads(output.out)


def test_cli_artifact_lifecycle_query_and_repository_transfer(tmp_path, capsys):
    repo = tmp_path / "repo"
    started = invoke(capsys, repo, "init", "training", "--config", '{"rate":0.1}')
    revision, writer = started["artifact"]["id"], started["writer"]["id"]
    invoke(capsys, repo, "log", revision, writer, '{"step":1,"loss":0.25}')
    invoke(capsys, repo, "artifact", "update", revision, writer, '{"title":"Experiment"}')
    publication = invoke(capsys, repo, "artifact", "finish", revision, "--writer", writer)
    version = publication
    assert version["namespace"] == "runs"
    inspected = invoke(capsys, repo, "inspect", "runs/training@latest")
    assert inspected["id"] == version["id"]
    assert inspected["manifest"]["metadata"]["config"] == {"rate": 0.1}
    queried = invoke(
        capsys,
        repo,
        "query",
        version["id"],
        "--kind",
        "record",
        "--where",
        '{"path":"/loss","op":"lt","value":0.5}',
    )
    assert queried["items"][0]["data"]["loss"] == 0.25
    assert invoke(capsys, repo, "repo", "check", "--verify-blobs")["ok"]
    bundle = tmp_path / "transfer"
    invoke(capsys, repo, "repo", "export", str(bundle))
    restored = tmp_path / "restored"
    invoke(capsys, restored, "repo", "import", str(bundle))
    assert invoke(capsys, restored, "inspect", version["id"])["id"] == version["id"]


def test_cli_arbitrary_namespace_nested_name_and_default_namespace(tmp_path, capsys):
    repo = tmp_path / "repo"
    default = invoke(capsys, repo, "artifact", "start", "model")
    assert default["artifact"]["namespace"] == "artifacts"
    opened = invoke(
        capsys, repo, "artifact", "start", "training/epoch-03", "--namespace", "checkpoints"
    )
    publication = invoke(
        capsys,
        repo,
        "artifact",
        "finish",
        opened["artifact"]["id"],
        "--writer",
        opened["writer"]["id"],
        "--value",
        '{"score":0.9}',
    )
    resolved = invoke(capsys, repo, "inspect", "checkpoints/training/epoch-03@v1")
    assert resolved["id"] == publication["id"]
    listed = invoke(capsys, repo, "artifact", "list", "--namespace", "checkpoints")
    assert [entry["id"] for entry in listed["items"]] == [opened["artifact"]["id"]]
    assert main(["--repo", str(repo), "inspect", "checkpoints:training/epoch-03@latest"]) == 1
    assert json.loads(capsys.readouterr().err)["error"]


def test_report_new_default_and_machine_readable_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["report", "new"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert Path(created["path"]).name == "NewReport.qmd"
    assert not (tmp_path / ".cairn/repo").exists()
    assert main(["report", "new"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert "already exists" in error["message"]


def test_python_module_cli_help():
    result = subprocess.run(
        [sys.executable, "-m", "cairn", "--help"], capture_output=True, text=True, check=True
    )
    assert "report" in result.stdout and "server" in result.stdout


def test_cli_uses_shared_destination_discovery(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAIRN_URL", raising=False)
    monkeypatch.setenv("CAIRN_REPO", str(tmp_path / "environment-repo"))
    assert main(["info"]) == 0
    assert json.loads(capsys.readouterr().out)["repository_id"]
    assert (tmp_path / "environment-repo/metadata.sqlite3").is_file()
    assert not (tmp_path / ".cairn").exists()
    monkeypatch.delenv("CAIRN_REPO")
    (tmp_path / ".cairn").write_text('{"repo": "configured-repo"}')
    assert main(["info"]) == 0
    assert json.loads(capsys.readouterr().out)["repository_id"]
    assert (tmp_path / "configured-repo/metadata.sqlite3").is_file()


def test_cli_report_build_publish_copy_export(tmp_path, capsys):
    repo = tmp_path / "repo"
    source = invoke(capsys, repo, "report", "new", str(tmp_path / "report.qmd"))["path"]
    result = invoke(
        capsys,
        repo,
        "report",
        "build",
        source,
        "--output",
        str(tmp_path / "build"),
        "--publish",
        "--name",
        "cli-report",
    )
    artifact_id = result["publication"]["id"]
    assert invoke(capsys, repo, "inspect", "reports/cli-report@latest")["id"] == artifact_id
    assert result["manifest"]["status"] == "complete"
    copied = invoke(capsys, repo, "report", "copy", artifact_id, str(tmp_path / "copy"))
    assert Path(copied["path"]).is_file()
    exported = invoke(capsys, repo, "report", "export", artifact_id, str(tmp_path / "export"))
    assert (Path(exported["path"]) / "index.html").is_file()


def test_cli_annotations_and_metadata_discovery(tmp_path, capsys):
    repo = tmp_path / "repo"
    opened = invoke(capsys, repo, "init", "training", "--config", '{"lr":0.1}')
    artifact, writer = opened["artifact"]["id"], opened["writer"]["id"]
    invoke(capsys, repo, "artifact", "finish", artifact, "--writer", writer)
    annotated = invoke(
        capsys,
        repo,
        "artifact",
        "annotate",
        "runs/training@latest",
        '{"tags":["paper"],"review":"draft"}',
        "--request-id",
        "cli-annotate",
    )
    assert annotated["annotations"] == {"tags": ["paper"], "review": "draft"}
    listed = invoke(
        capsys,
        repo,
        "artifact",
        "list",
        "--namespace",
        "runs",
        "--state",
        "finished",
        "--where",
        '{"and":[{"path":"/metadata/config/lr","op":"lt","value":0.2},{"path":"/annotations/tags","op":"contains","value":"paper"}]}',
    )
    assert [item["id"] for item in listed["items"]] == [artifact]
    changed = invoke(
        capsys,
        repo,
        "artifact",
        "annotate",
        artifact,
        '{"tags":null,"review":"approved"}',
        "--expected-etag",
        annotated["annotations_etag"],
    )
    assert changed["annotations"] == {"review": "approved"}
    assert (
        invoke(
            capsys,
            repo,
            "artifact",
            "list",
            "--where",
            '{"path":"/annotations/tags","op":"contains","value":"paper"}',
        )["items"]
        == []
    )


@pytest.mark.parametrize("missing", ["uvicorn", "fastapi"])
def test_missing_server_dependencies_explain_installation(tmp_path, monkeypatch, capsys, missing):
    import builtins

    original = builtins.__import__

    def without_server(name, *args, **kwargs):
        if name == ("uvicorn" if missing == "uvicorn" else "server"):
            raise ModuleNotFoundError(f"No module named {missing!r}", name=missing)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_server)
    assert main(["--repo", str(tmp_path / "repo"), "server"]) != 0
    error = json.loads(capsys.readouterr().err)
    assert "uv run --extra all cairn server" in error["message"]
    assert not (tmp_path / "repo").exists()

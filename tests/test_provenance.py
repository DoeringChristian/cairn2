"""Provenance must describe the bytes used, survive publication, and be optional."""

import io
import json
import subprocess
import tarfile
from contextlib import closing

import pytest
from test_http import serve

import cairn
from cairn import Client
from cairn.storage import Repository


def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], stderr=subprocess.PIPE, text=True
    ).strip()


@pytest.fixture
def source_tree(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    git(root, "init", "-q")
    git(root, "config", "user.email", "provenance-test@example.invalid")
    git(root, "config", "user.name", "Provenance Test")
    (root / ".gitignore").write_text("ignored.py\n.env\n")
    (root / "train.py").write_text("rate = 0.1\n")
    (root / "uv.lock").write_text("version = 1\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "initial source")
    commit = git(root, "rev-parse", "HEAD")
    (root / "train.py").write_text("rate = 0.2\n")
    (root / "helper.py").write_text("def score(): return 42\n")
    (root / "ignored.py").write_text("secret = 'ignored credential'\n")
    (root / ".env").write_text("TOKEN=private-token\n")
    (root / "outside.py").symlink_to(tmp_path / "private.py")
    (tmp_path / "private.py").write_text("secret = 'outside credential'\n")
    return root, commit


@pytest.mark.parametrize("entrypoint", ["init", "run"])
def test_disable_provenance_preserves_user_metadata_and_config(tmp_path, monkeypatch, entrypoint):
    monkeypatch.chdir(tmp_path)
    if entrypoint == "init":
        writer = cairn.init(
            name="minimal",
            repo=tmp_path / "repo",
            provenance=False,
            config={"seed": 12},
            metadata={"owner": "test"},
        )
        try:
            published = writer.finish()
        finally:
            writer.client.close()
    else:
        with Client(tmp_path / "repo") as client:
            writer = client.run(
                "minimal", provenance=False, config={"seed": 12}, metadata={"owner": "test"}
            )
            published = writer.finish()
    manifest = published["manifest"]
    assert manifest["metadata"] == {"config": {"seed": 12}, "owner": "test"}
    assert not manifest["captures"]


def unpack(payload):
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        assert all(member.isfile() for member in archive.getmembers())
        return {member.name: archive.extractfile(member).read() for member in archive.getmembers()}


def test_working_tree_snapshot_and_package_inventory(source_tree):
    from cairn.provenance import collect

    root, commit = source_tree
    metadata, captures = collect()
    provenance = metadata["provenance"]
    assert provenance["git"]["head"] == commit
    assert provenance["git"]["dirty"] is True
    files = unpack(captures["provenance/source.tar.gz"])
    assert files == {
        ".gitignore": b"ignored.py\n.env\n",
        "train.py": b"rate = 0.2\n",
        "helper.py": b"def score(): return 42\n",
        "uv.lock": b"version = 1\n",
    }
    assert b"+rate = 0.2" in captures["provenance/git.diff"]
    assert b"-rate = 0.1" in captures["provenance/git.diff"]
    assert "outside.py" in {entry["path"] for entry in provenance["omissions"]}
    packages = json.loads(captures["provenance/packages.json"])
    assert any(p["name"].lower() == "pytest" and p["version"] for p in packages)
    assert metadata["environment"]["cwd"] == str(root)
    assert metadata["environment"]["python"]
    assert metadata["environment"]["command"]


def test_source_optout_keeps_git_packages_and_dependency_lock(source_tree):
    from cairn.provenance import collect

    _, commit = source_tree
    metadata, captures = collect(capture_source=False)
    assert metadata["provenance"]["git"]["head"] == commit
    assert "provenance/source.tar.gz" not in captures
    assert "provenance/git.diff" not in captures
    assert json.loads(captures["provenance/packages.json"])
    assert unpack(captures["provenance/lockfiles.tar.gz"]) == {"uv.lock": b"version = 1\n"}


def test_no_git_captures_entry_script_and_lockfile(tmp_path, monkeypatch):
    from cairn import provenance

    monkeypatch.chdir(tmp_path)
    (tmp_path / "train.py").write_text("print('training')\n")
    (tmp_path / "requirements.txt").write_text("numpy==2.0.0\n")
    (tmp_path / "unrelated.py").write_text("unrelated = True\n")
    monkeypatch.setattr("sys.argv", [str(tmp_path / "train.py"), "--epochs", "2"])

    def unavailable(*args, **kwargs):
        raise FileNotFoundError("git unavailable")

    monkeypatch.setattr(provenance, "_git", unavailable)
    metadata, captures = provenance.collect()
    assert metadata["provenance"]["git"] is None
    assert metadata["provenance"]["errors"]
    assert unpack(captures["provenance/source.tar.gz"]) == {
        "train.py": b"print('training')\n",
        "requirements.txt": b"numpy==2.0.0\n",
    }


@pytest.mark.parametrize("remote", [False, True])
def test_published_run_provenance_preserves_start_time_bytes(source_tree, tmp_path, remote):
    root, commit = source_tree
    with (
        closing(Repository(tmp_path / "repo")) as repository,
        serve(repository) as url,
        Client(
            url if remote else tmp_path / "repo",
            token="test-token",
            outbox=tmp_path / "outbox",
        ) as client,
    ):
        run = client.run("training", config={"rate": 0.2})
        (root / "train.py").write_text("rate = 999\n")
        run.log({"loss": 1})
        publication = run.finish(timeout=10)
        reader = client.get(publication["id"])
        metadata = reader.metadata
        assert metadata["provenance"]["git"]["head"] == commit
        assert metadata["config"] == {"rate": 0.2}
        archive = reader.download("provenance/source.tar.gz", to=tmp_path / "saved")
        assert unpack(archive.read_bytes())["train.py"] == b"rate = 0.2\n"
        record = next(reader.records())
        assert record["loss"] == 1
        assert isinstance(record["_wall_time"], float)
        if remote:
            assert client.delivery.pending() == 0


def test_capture_bounds_are_reported_and_command_credentials_redacted(source_tree, monkeypatch):
    from cairn import provenance

    monkeypatch.setattr(provenance, "FILE_COUNT", 1)
    monkeypatch.setattr(
        "sys.orig_argv",
        ["python", "train.py", "--token", "secret-value", "--password=private", "--epochs", "2"],
    )
    monkeypatch.setenv("PROVENANCE_TEST_SECRET", "never-record-this-env")
    metadata, captures = provenance.collect()
    assert len(unpack(captures["provenance/source.tar.gz"])) == 1
    assert metadata["provenance"]["complete"] is False
    assert metadata["provenance"]["source"]["omitted"] >= 2
    command = metadata["environment"]["command"]["argv"]
    assert command == [
        "python",
        "train.py",
        "--token",
        "<redacted>",
        "--password=<redacted>",
        "--epochs",
        "2",
    ]
    assert "never-record-this-env" not in json.dumps(metadata)
    assert all(b"never-record-this-env" not in value for value in captures.values())

import json
import os
from contextlib import closing

import pytest
from test_http import serve

from cairn import Client, IntegrityError, Unsupported
from cairn.cli import main
from cairn.ops import check_repository
from cairn.storage import Repository


def measurements(root, note="gonio"):
    (root / "scans").mkdir(parents=True)
    (root / "scans/a.bin").write_bytes(note.encode() * 4096)
    (root / "notes.txt").write_text(note)
    return root


def stored(repo):
    return [path for path in (repo / "objects").rglob("*") if path.is_file()]


def test_local_links_keep_files_in_place(tmp_path):
    data = measurements(tmp_path / "gonio")
    with Client(tmp_path / "repo") as client:
        with client.start("datasets", "gonio") as dataset:
            dataset.add(data, link=True)
        assert stored(tmp_path / "repo") == []
        reader = client.get("datasets/gonio")
        folder = reader.download("gonio", to=tmp_path / "copy")
        assert (folder / "scans/a.bin").resolve() == data / "scans/a.bin"
        assert (folder / "notes.txt").read_text() == "gonio"
        assert check_repository(client.backend)["ok"]
        (data / "notes.txt").write_text("edited")
        with pytest.raises(IntegrityError, match="changed since it was linked"):
            reader.download("gonio", to=tmp_path / "again")
        assert (
            "changed since it was linked" in check_repository(client.backend)["errors"][0]["error"]
        )
        client.remove("datasets/gonio")
        client.gc(apply=True, grace_seconds=0)
    assert (data / "scans/a.bin").read_bytes() == b"gonio" * 4096


def test_servers_link_only_under_their_roots_and_serve_the_files(tmp_path):
    data = measurements(tmp_path / "gonio")
    elsewhere = measurements(tmp_path / "elsewhere", "other")
    repository = Repository(tmp_path / "repo", link_roots=[tmp_path / "gonio"])
    with (
        closing(repository),
        serve(repository) as url,
        Client(url, token="test-token", outbox=tmp_path / "outbox") as client,
    ):
        with client.start("datasets", "gonio") as dataset:
            dataset.add(data, link=True)
        assert stored(tmp_path / "repo") == []
        escape = str(data / ".." / "elsewhere/notes.txt")
        with pytest.raises(Unsupported, match="outside its link roots"):
            client.backend.link_blob(escape)
        with (
            pytest.warns(UserWarning, match="uploading a copy instead"),
            client.start("datasets", "elsewhere") as copied,
        ):
            copied.add(elsewhere, link=True)
        assert len(stored(tmp_path / "repo")) == 2
        folder = client.get("datasets/gonio").download("gonio", to=tmp_path / "download")
        assert not (folder / "scans/a.bin").is_symlink()
        assert (folder / "scans/a.bin").read_bytes() == b"gonio" * 4096


def test_cli_links_files(tmp_path, capsys):
    data = measurements(tmp_path / "gonio")
    repo = str(tmp_path / "repo")
    assert main(["--repo", repo, "add", "datasets/gonio", str(data), "--link"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "finished"
    assert stored(tmp_path / "repo") == []
    with Client(repo) as client:
        path = client.get("datasets/gonio").download("gonio")
    assert os.path.realpath(path / "notes.txt") == str(data / "notes.txt")

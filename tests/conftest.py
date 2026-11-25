import pytest


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CAIRN_PROVENANCE", "0")
    for name in ("CAIRN_REPO", "CAIRN_PROJECT", "CAIRN_TOKEN"):
        monkeypatch.delenv(name, raising=False)

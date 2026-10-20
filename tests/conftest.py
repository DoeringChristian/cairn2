import pytest


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("CAIRN_STATE_DIR", raising=False)
    monkeypatch.delenv("CAIRN_PROJECT", raising=False)

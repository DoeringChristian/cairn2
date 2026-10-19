import pytest


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_CACHE_DIR", str(tmp_path / "cache"))

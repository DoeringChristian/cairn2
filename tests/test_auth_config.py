import concurrent.futures
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from cairn import model
from cairn.config import ensure_token, resolve_project, resolve_repo, resolve_token, startup_urls
from cairn.server import create_app


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    for name in ("CAIRN_TOKEN", "CAIRN_TOKEN_FILE", "CAIRN_REPO", "CAIRN_PROJECT"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)


def test_token_precedence_and_parent_project(tmp_path, monkeypatch):
    user = tmp_path / "home/.config/cairn"
    user.mkdir(parents=True)
    (user / "token").write_text("user-token\n")
    assert resolve_token() == "user-token"
    project = tmp_path / "project"
    (project / ".cairn").mkdir(parents=True)
    (project / ".cairn/token").write_text("project-token\n")
    child = project / "nested"
    child.mkdir()
    assert resolve_token(cwd=child) == "project-token"
    (project / "env-token").write_text("file-token\n")
    monkeypatch.setenv("CAIRN_TOKEN_FILE", "../env-token")
    assert resolve_token(cwd=child) == "file-token"
    monkeypatch.setenv("CAIRN_TOKEN", "env-token")
    assert resolve_token(cwd=child) == "env-token"
    assert resolve_token("explicit-token", cwd=child) == "explicit-token"


def test_toml_settings_merge_and_keep_tokens_with_their_repo(tmp_path):
    project = tmp_path / "project"
    (project / ".cairn").mkdir(parents=True)
    (project / ".cairn/token").write_text("local-token\n")
    (project / "private-token").write_text("secret\n")
    config = project / ".cairn.toml"
    config.write_text('project = "outer"\nrepo = "data"\ntoken_file = "private-token"\n')
    nested = project / "nested"
    nested.mkdir()
    assert resolve_repo(cwd=nested) == str(project / "data")
    assert resolve_token(cwd=nested) == "secret"
    assert resolve_project(cwd=nested) == "outer"
    config.write_text('project = "outer"\nrepo = "data"\n')
    assert resolve_token(cwd=nested) == "local-token"
    (nested / ".cairn.toml").write_text('project = "inner"\n')
    assert (resolve_project(cwd=nested), resolve_repo(cwd=nested)) == (
        "inner",
        str(project / "data"),
    )
    (nested / ".cairn.toml").write_text('repo = "https://example.test"\n')
    assert (resolve_project(cwd=nested), resolve_repo(cwd=nested)) == (
        "outer",
        "https://example.test",
    )
    assert resolve_token(cwd=nested) is None


def test_project_precedence(tmp_path, monkeypatch):
    assert resolve_project() == "default"
    (tmp_path / ".cairn.toml").write_text('project = "configured"\n')
    assert resolve_project() == "configured"
    monkeypatch.setenv("CAIRN_PROJECT", "environment")
    assert resolve_project() == "environment"
    assert resolve_project("explicit") == "explicit"


def test_token_creation_is_private_atomic_and_stable(tmp_path):
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda _: ensure_token(cwd=tmp_path), range(32)))
    assert len(set(tokens)) == 1
    assert len(tokens[0]) >= 32
    path = tmp_path / ".cairn/token"
    assert path.stat().st_mode & 0o777 == 0o600
    assert resolve_token(cwd=tmp_path) == tokens[0]
    assert list((tmp_path / ".cairn").iterdir()) == [path]


def test_invalid_configuration_is_actionable_without_secret_leak(tmp_path, monkeypatch):
    with pytest.raises(model.ValidationError):
        resolve_token("")
    with pytest.raises(model.ValidationError) as caught:
        resolve_token("secret\ninjected")
    assert "secret" not in str(caught.value)
    with pytest.raises(model.ValidationError):
        resolve_token("bad\x00token")
    monkeypatch.setenv("CAIRN_TOKEN_FILE", "missing")
    with pytest.raises(model.ValidationError, match="Cannot read token file"):
        resolve_token()
    monkeypatch.delenv("CAIRN_TOKEN_FILE")
    (tmp_path / ".cairn.toml").write_text('repo = "data"\nurl = "https://example.test"\n')
    with pytest.raises(model.ValidationError, match="Unknown settings.*url"):
        resolve_repo()
    (tmp_path / ".cairn.toml").write_text("repo = [")
    with pytest.raises(model.ValidationError, match="Cannot read configuration"):
        resolve_repo()
    (tmp_path / ".cairn.toml").unlink()
    (tmp_path / ".cairn").write_text("{}")
    with pytest.raises(model.ValidationError, match="must be a folder.*cairn.toml"):
        ensure_token()


def test_repo_precedence(tmp_path, monkeypatch):
    assert resolve_repo() == str(tmp_path / ".cairn/repo")
    monkeypatch.setenv("CAIRN_REPO", "local")
    assert resolve_repo() == str(tmp_path / "local")
    monkeypatch.setenv("CAIRN_REPO", "https://example.test/cairn/")
    assert resolve_repo() == "https://example.test/cairn"
    assert resolve_repo("explicit") == str(tmp_path / "explicit")


def test_startup_urls_include_hostname_and_never_query_tokens(monkeypatch):
    monkeypatch.setattr("cairn.config.socket.gethostname", lambda: "labhost")
    assert startup_urls("0.0.0.0", 9898) == ["http://localhost:9898/", "http://labhost:9898/"]
    assert startup_urls("::", 9898) == ["http://localhost:9898/", "http://labhost:9898/"]
    assert startup_urls("::1", 9898) == ["http://[::1]:9898/"]
    link = startup_urls("127.0.0.1", 9898, token="a+b/c=")[0]
    assert urlsplit(link).query == ""
    assert urlsplit(link).fragment == "token=a%2Bb%2Fc%3D"


class InfoRepository:
    closed = False

    def info(self):
        return {
            "repository_id": "test",
            "schema_version": 2,
            "protocol_version": model.PROTOCOL_VERSION,
        }

    def close(self):
        self.closed = True


def test_server_authentication_health_and_lifecycle():
    repository = InfoRepository()
    with pytest.raises(model.ValidationError, match="token"):
        create_app(repository)
    with TestClient(create_app(repository, token="test-secret")) as client:
        assert client.get("/health").json() == {
            "status": "ok",
            "protocol_version": model.PROTOCOL_VERSION,
        }
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Cookie": "token=test-secret"}):
            response = client.get("/v1/info", headers=headers)
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "unauthorized"
            assert response.headers["www-authenticate"] == "Bearer"
            assert "test-secret" not in response.text
        assert client.get("/v1/info?token=test-secret").status_code == 401
        client.headers["Authorization"] = "Bearer test-secret"
        assert client.get("/v1/info").json()["repository_id"] == "test"
        assert client.get("/v1/capabilities").json()["authentication"] == "bearer"
        assert (
            client.get("/v1/capabilities").json()["protocol_version"] == model.PROTOCOL_VERSION == 4
        )
        assert client.get("/v1/info?token=test-secret").status_code == 400
        assert client.post("/rpc", json={"method": "close"}).status_code == 404
        assert client.get("/docs").status_code == 404
        method_error = client.patch("/v1/artifacts")
        assert method_error.status_code == 405
        assert method_error.json()["error"]["code"] == model.Unsupported.code
    assert not repository.closed


@pytest.mark.parametrize(
    "path,body",
    [
        ("/v1/artifacts", {"project": "p", "namespace": "runs", "name": "n", "config": {}}),
        ("/v1/artifacts", {"project": "p", "namespace": "runs", "name": "n", "metadata": {}}),
        ("/v1/artifacts", {"project": 1, "namespace": "runs", "name": "n"}),
        ("/v1/artifacts", {"project": "p", "type": "run", "name": "n"}),
        ("/v1/artifacts", {"project": "p", "namespace": "runs", "type": "run", "name": "n"}),
        (
            "/v1/artifacts/r/writers/w/entries",
            {"entries": [{"sequence": True, "kind": "record", "data": {}}]},
        ),
        (
            "/v1/artifacts/r/writers/w/entries",
            {"entries": [{"sequence": 1, "kind": "record", "data": {}, "unknown": 1}]},
        ),
        ("/v1/records/query", {"ref": "x", "limit": "100"}),
    ],
)
def test_request_models_are_strict(path, body):
    client = TestClient(create_app(InfoRepository(), token="secret"))
    response = client.post(path, json=body, headers={"Authorization": "Bearer secret"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == model.ValidationError.code


def test_json_and_chunked_binary_body_limits():
    client = TestClient(
        create_app(InfoRepository(), token="secret", max_json_bytes=32, max_blob_bytes=8)
    )
    client.headers["Authorization"] = "Bearer secret"
    for content in (b"x" * 33, iter([b"x" * 16, b"x" * 17])):
        response = client.post("/v1/artifacts", content=content)
        assert response.status_code == 413
    response = client.put("/v1/blobs", content=iter([b"1234", b"56789"]))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == model.CapacityError.code
    assert client.put("/v1/blobs?size=9", content=b"").status_code == 413


def test_nonfinite_json_and_unknown_queries_are_rejected():
    client = TestClient(create_app(InfoRepository(), token="secret"))
    client.headers["Authorization"] = "Bearer secret"
    response = client.post(
        "/v1/artifacts/r/finalization",
        content=b'{"value":NaN}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    response = client.post(
        "/v1/artifacts?unexpected=1", json={"project": "p", "namespace": "runs", "name": "n"}
    )
    assert response.status_code == 400
    assert client.get("/v1/artifacts?limit=1&limit=2").status_code == 400
    assert client.get("/v1/artifacts?type=run").status_code == 400


@pytest.mark.parametrize("mode", ["default", "parent", "environment", "config"])
def test_server_discovers_local_sdk_repository(mode, tmp_path, monkeypatch):
    from cairn import Client
    from cairn.cli import main

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    if mode == "environment":
        monkeypatch.setenv("CAIRN_REPO", str(project / "data"))
    elif mode == "config":
        (project / "server-token").write_text("test-token\n")
        (project / ".cairn.toml").write_text('repo = "data"\ntoken_file = "server-token"\n')
    with Client(project="test") as local:
        artifact = local.start("datasets", "local")
        artifact.finish(value={"value": 1})
        identity = local.backend.info()["repository_id"]
        if mode == "parent":
            child = project / "scripts"
            child.mkdir()
            monkeypatch.chdir(child)

        def run(server, **kwargs):
            with TestClient(server.config.app) as http:
                headers = {"Authorization": f"Bearer {resolve_token()}"}
                assert http.get("/v1/info", headers=headers).json()["repository_id"] == identity
                response = http.post(
                    "/v1/resolutions",
                    json={"refs": [artifact.id], "project": "test"},
                    headers=headers,
                )
                assert response.status_code == 200
                assert response.json()[0]["id"] == artifact.id
                response = http.post(
                    "/v1/artifacts",
                    json={"project": "test", "namespace": "datasets", "name": "remote"},
                    headers=headers,
                )
                assert response.status_code == 200
                remote_id = response.json()["artifact"]["id"]
                assert local.backend.resolve(remote_id)["name"] == "remote"
                continued = local.start("datasets", "continued")
                continued.finish(value=2)
                assert (
                    http.post(
                        "/v1/resolutions", json={"refs": [continued.id]}, headers=headers
                    ).json()[0]["id"]
                    == continued.id
                )

        monkeypatch.setattr("uvicorn.Server.run", run)
        assert main(["server", "--port", "0", "--no-ui"]) == 0
        with Client(project="test") as reopened:
            assert reopened.backend.info()["repository_id"] == identity
        if mode == "parent":
            assert not (project / "scripts/.cairn").exists()


def test_server_rejects_remote_discovery_but_explicit_repo_overrides(tmp_path, monkeypatch, capsys):
    from cairn.cli import main

    monkeypatch.setenv("CAIRN_REPO", "http://example.test")
    assert main(["server", "--no-auth"]) == 1
    assert "remote server" in capsys.readouterr().err
    assert not (tmp_path / ".cairn").exists()
    monkeypatch.setattr("uvicorn.Server.run", lambda *args, **kwargs: None)
    assert main(["--repo", str(tmp_path / "explicit"), "server", "--no-auth", "--port", "0"]) == 0
    assert (tmp_path / "explicit/metadata.sqlite3").exists()

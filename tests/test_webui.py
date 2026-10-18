from contextlib import closing

from fastapi.testclient import TestClient

from cairn.server import create_app
from cairn.storage import Repository


def test_browser_sessions_and_plain_artifact_identity(tmp_path):
    with closing(Repository(tmp_path)) as repo:
        started = repo.start("demo", "runs", "training")
        identifier = started["artifact"]["id"]
        writer = started["writer"]["id"]
        app = create_app(repo, token="test-token", ui=True)
        with TestClient(app) as browser:
            assert browser.get("/ui-api/projects").status_code == 401
            for path in ("/favicon.svg", "/favicon.ico"):
                icon = browser.get(path)
                assert icon.status_code == 200
                assert icon.headers["content-type"].startswith("image/svg+xml")
                assert "<svg" in icon.text
                assert browser.head(path).status_code == 200
            link = "test-token"
            assert browser.post("/ui-api/login", json={"link_token": link}).status_code == 200
            assert browser.post("/ui-api/login", json={"link_token": link}).status_code == 200
            assert browser.get("/ui-api/session").json()["authenticated"]
            assert browser.get("/ui-api/projects").json()[0]["runs"] == 1
            assert browser.get(f"/p/demo/a/{identifier}?tab=lineage").status_code == 200
            live = browser.get(f"/ui-api/lineage/{identifier}/1").json()["nodes"]
            assert live[identifier]["status"] == "live"
            assert live[identifier]["version"] is None
            assert browser.post("/v1/artifacts", json={}).status_code == 401
            repo.close_writer(identifier, writer, 0)
            repo.finish(identifier)
            resolved = browser.post("/v1/resolutions", json={"refs": [identifier]}).json()[0]
            assert resolved["id"] == identifier and resolved["state"] == "finished"
            history = browser.get("/v1/history", params={"ref": identifier}).json()
            assert history[0]["id"] == identifier
            assert browser.post("/ui-api/logout").status_code == 200
            assert browser.get("/ui-api/session").status_code == 401
        with TestClient(create_app(repo, ui=True, no_auth=True)) as browser:
            assert browser.get("/ui-api/session").json()["no_auth"] is True
            assert browser.get("/ui-api/projects").status_code == 200


def test_report_comments_persist_across_versions_and_stay_project_scoped(tmp_path):
    def publish(repo, project="demo"):
        result = repo.start(project, "reports", "findings")
        identity, writer = result["artifact"]["id"], result["writer"]["id"]
        repo.close_writer(identity, writer, 0)
        repo.finish(
            identity,
            value={
                "$cairn": 1,
                "format": "report/bundle",
                "version": 1,
                "data": {},
                "blobs": [],
                "refs": [],
            },
        )
        return identity

    with closing(Repository(tmp_path)) as repo:
        first = publish(repo)
        second = publish(repo)
        other = publish(repo, "other")
        body = {
            "anchor": {"quote": "Selected finding", "path": "report.qmd", "line_start": 12},
            "body": "Please explain the uncertainty.",
            "request_id": "comment-1",
        }
        with TestClient(create_app(repo, token="test-token", ui=True)) as browser:
            path = f"/ui-api/reports/{first}/comments"
            assert browser.post(path, json=body).status_code == 401
            browser.post("/ui-api/login", json={"token": "test-token"})
            assert (
                browser.post(path, json=body, headers={"origin": "https://other.test"}).status_code
                == 401
            )
            created = browser.post(path, json=body)
            assert created.status_code == 200, created.text
            assert browser.post(path, json=body).json()["id"] == created.json()["id"]
            carried = browser.get(f"/ui-api/reports/{second}/comments").json()
            assert len(carried) == 1
            assert carried[0]["artifact_id"] == first
            assert carried[0]["anchor"] == body["anchor"]
            assert browser.get(f"/ui-api/reports/{other}/comments").json() == []
            comment_id = created.json()["id"]
            assert (
                browser.delete(f"/ui-api/reports/{other}/comments/{comment_id}").status_code == 404
            )
            edit_path = f"/ui-api/reports/{second}/comments/{comment_id}"
            assert (
                browser.put(
                    edit_path, json=body, headers={"origin": "https://other.test"}
                ).status_code
                == 401
            )
            updated = {**body, "body": "Autosaved edit"}
            assert browser.put(edit_path, json=updated).status_code == 200
            assert browser.get(path).json()[0]["body"] == "Autosaved edit"
            assert browser.put(edit_path, json=body).status_code == 200
            assert browser.post("/v1/artifacts", json={}).status_code == 401
    with closing(Repository(tmp_path)) as repo:
        third = publish(repo)
        with TestClient(create_app(repo, ui=True, no_auth=True)) as browser:
            saved = browser.get(f"/ui-api/reports/{third}/comments").json()
            assert len(saved) == 1 and saved[0]["body"] == body["body"]
            assert (
                browser.delete(f"/ui-api/reports/{third}/comments/{saved[0]['id']}").status_code
                == 200
            )
            assert browser.get(f"/ui-api/reports/{first}/comments").json() == []


def test_browser_tags_use_annotations_and_reject_conflicts_and_foreign_origins(tmp_path):
    with closing(Repository(tmp_path)) as repo:
        artifact = repo.start("demo", "runs", "tagged")["artifact"]
        identifier = artifact["id"]
        route = f"/ui-api/artifacts/{identifier}/tags"
        with TestClient(create_app(repo, token="test-token", ui=True)) as browser:
            assert browser.patch(route, json={"tags": ["baseline"]}).status_code == 401
            browser.post("/ui-api/login", json={"token": "test-token"})
            body = {
                "tags": [" baseline ", "baseline"],
                "expected_etag": artifact["annotations_etag"],
            }
            assert (
                browser.patch(
                    route, json=body, headers={"origin": "https://foreign.invalid"}
                ).status_code
                == 401
            )
            assert browser.patch(route, json=body).status_code == 200
            current = browser.post("/v1/resolutions", json={"refs": [identifier]}).json()[0]
            assert current["annotations"]["tags"] == ["baseline"]
            assert browser.patch(route, json=body).status_code == 409
            assert browser.patch(route, json={"tags": [3]}).status_code == 400
            assert (
                browser.patch(
                    route, json={"tags": [], "expected_etag": current["annotations_etag"]}
                ).status_code
                == 200
            )

import time
from contextlib import closing

from fastapi.testclient import TestClient

import cairn
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
            assert browser.get("/ui-api/artifacts?project=demo").status_code == 401
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


def test_browsers_write_report_bodies_and_comments_but_not_slots(tmp_path):
    with closing(Repository(tmp_path)) as repo:
        other = repo.create_report("other", "findings")["id"]
        with TestClient(create_app(repo, token="test-token", ui=True)) as browser:
            created = {"project": "demo", "name": "findings", "body": "# Plan"}
            assert browser.post("/ui-api/reports", json=created).status_code == 401
            browser.post("/ui-api/login", json={"token": "test-token"})
            foreign = {"origin": "https://other.test"}
            assert browser.post("/ui-api/reports", json=created, headers=foreign).status_code == 401
            report = browser.post("/ui-api/reports", json=created).json()
            path = f"/ui-api/reports/{report['id']}"
            edit = {"source": "# Plan\n\n::: {.agent #baseline}\n:::", "expected_revision": 1}
            assert browser.put(f"{path}/body", json=edit).json()["revision"] == 2
            assert browser.put(f"{path}/body", json=edit).status_code == 409
            slot = f"/v1/reports/{report['id']}/parts/baseline"
            assert browser.put(slot, json=edit).status_code == 401
            repo.write_report(report["id"], "baseline", "Done.", author="agent")
            parts = browser.get(f"/v1/reports/{report['id']}").json()["parts"]
            assert parts["baseline"]["source"] == "Done."
            assert parts["body"]["revision"] == 2
            listed = browser.get("/v1/reports", params={"project": "demo"}).json()
            assert [item["name"] for item in listed] == ["findings"]
            body = {
                "anchor": {"quote": "Plan", "path": "body", "line_start": 1},
                "body": "Too broad?",
                "for_agent": True,
                "request_id": "comment-1",
            }
            comment = browser.post(f"{path}/comments", json=body).json()
            assert comment["report_id"] == report["id"] and comment["artifact_id"] is None
            assert browser.post(f"{path}/comments", json=body).json()["id"] == comment["id"]
            elsewhere = f"/ui-api/reports/{other}/comments/{comment['id']}"
            assert browser.post(f"{elsewhere}/sending").status_code == 404
            assert browser.post(f"{path}/comments/{comment['id']}/sending").json()["sent_at"]
            listed = browser.get(f"/v1/reports/{report['id']}/comments").json()
            assert [item["body"] for item in listed] == ["Too broad?"]
            assert browser.post("/v1/reports", json=created).status_code == 401
            assert browser.delete(path, headers=foreign).status_code == 401
            assert browser.delete(path).json()["deleted"]
            assert browser.get(f"/v1/reports/{report['id']}").status_code == 404
            assert repo.list_comments("reports/**", project="demo")["items"] == []


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


def test_browser_sessions_renew_while_used_and_end_after_a_week_idle(tmp_path, monkeypatch):
    now = [time.time()]
    monkeypatch.setattr("itsdangerous.timed.time.time", lambda: now[0])
    with (
        closing(Repository(tmp_path)) as repo,
        TestClient(create_app(repo, token="test-token", ui=True)) as browser,
    ):
        browser.post("/ui-api/login", json={"token": "test-token"})
        assert "set-cookie" not in browser.get("/ui-api/session").headers
        now[0] += 6 * 24 * 60 * 60
        renewed = browser.get("/ui-api/session")
        assert "max-age=604800" in renewed.headers["set-cookie"].lower()
        now[0] += 6 * 24 * 60 * 60
        assert browser.get("/ui-api/session").status_code == 200
        now[0] += 2 * 60 * 60
        browser.post("/ui-api/logout")
        assert browser.get("/ui-api/session").status_code == 401
        browser.post("/ui-api/login", json={"token": "test-token"})
        now[0] += 8 * 24 * 60 * 60
        assert browser.get("/ui-api/session").status_code == 401


def test_browser_removal_requires_session_and_origin(tmp_path):
    with closing(Repository(tmp_path)) as repo:
        identifier = repo.start("demo", "runs", "removed")["artifact"]["id"]
        route = f"/ui-api/artifacts/{identifier}"
        with TestClient(create_app(repo, token="test-token", ui=True)) as browser:
            assert browser.delete(route).status_code == 401
            browser.post("/ui-api/login", json={"token": "test-token"})
            foreign = browser.delete(route, headers={"origin": "https://foreign.invalid"})
            assert foreign.status_code == 401
            assert browser.delete(route).json() == {
                "id": identifier,
                "deleted": True,
                "retained_by": [],
            }
            listed = browser.get("/ui-api/artifacts", params={"project": "demo"}).json()
            assert listed["items"] == []


def test_artifact_summaries_are_bounded_filtered_and_exclude_payloads(tmp_path):
    import sqlalchemy as sa

    from cairn.storage import artifacts

    with closing(Repository(tmp_path)) as repo:
        original = repo.start("large", "runs", "seed")["artifact"]
        with repo._transaction(write=True) as connection:
            template = dict(
                connection.execute(sa.select(artifacts).where(artifacts.c.id == original["id"]))
                .mappings()
                .one()
            )
            connection.execute(
                artifacts.insert(),
                [
                    {
                        **template,
                        "id": f"{index:032x}",
                        "name": f"run-{index:04}",
                        "metadata": {"config": "PRIVATE_LARGE_CONFIG" * 1000},
                        "manifest": {"outcome": "succeeded", "value": "LARGE_INLINE_VALUE" * 1000},
                    }
                    for index in range(2000)
                ],
            )
        tagged = f"{1777:032x}"
        repo.annotate(tagged, {"tags": ["chosen"]})
        repo.start("large", "datasets", "excluded-dataset")
        repo.start("other", "runs", "excluded-project")
        with TestClient(create_app(repo, ui=True, no_auth=True)) as browser:
            params = {"project": "large", "category": "runs"}
            response = browser.get("/ui-api/artifacts", params=params)
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 2001 and len(data["items"]) == 50
            assert len(response.content) < 40000
            assert "PRIVATE_LARGE_CONFIG" not in response.text
            assert "LARGE_INLINE_VALUE" not in response.text
            assert all("manifest" not in item and "metadata" not in item for item in data["items"])
            second = browser.get("/ui-api/artifacts", params={**params, "offset": 50}).json()
            assert not {a["id"] for a in data["items"]} & {a["id"] for a in second["items"]}
            for extra in ({"search": "run-1777"}, {"tag": "chosen"}):
                filtered = browser.get("/ui-api/artifacts", params={**params, **extra}).json()
                assert filtered["total"] == 1 and filtered["items"][0]["id"] == tagged
            assert (
                browser.get("/ui-api/artifacts", params={**params, "limit": 1000}).status_code
                == 400
            )


def test_viewers_serve_an_artifacts_files_to_a_sandbox_by_signed_link(tmp_path):
    (tmp_path / "index.html").write_text("<script src='./mesh.js'></script>")
    (tmp_path / "mesh.js").write_text("// mesh")
    with cairn.init("viewer", project="demo", repo=tmp_path / "repo", provenance=False) as run:
        cairn.add(tmp_path / "index.html", name="viewer/index.html")
        cairn.add(tmp_path / "mesh.js", name="viewer/mesh.js")
    with (
        closing(Repository(tmp_path / "repo")) as repo,
        TestClient(create_app(repo, token="test-token", ui=True)) as browser,
    ):
        assert browser.get(f"/ui-api/viewer-tokens/{run.id}").status_code == 401
        browser.post("/ui-api/login", json={"token": "test-token"})
        url = browser.get(f"/ui-api/viewer-tokens/{run.id}").json()["url"]
        browser.cookies.clear()
        page = browser.get(url + "viewer/index.html")
        assert page.text == "<script src='./mesh.js'></script>"
        assert page.headers["content-type"].startswith("text/html")
        assert page.headers["content-security-policy"].startswith("sandbox allow-scripts;")
        assert browser.get(url + "viewer/mesh.js").text == "// mesh"
        assert browser.get(url + "viewer/missing.js").status_code == 404
        forged = url.replace("/viewers/", "/viewers/x")
        assert browser.get(forged + "viewer/index.html").status_code == 401

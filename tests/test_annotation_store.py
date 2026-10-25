import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
import sqlalchemy as sa
from test_storage_review import backends, postgres_backends  # noqa: F401

from cairn.cli import main
from cairn.model import Conflict, NotFound, Unsupported, ValidationError, digest
from cairn.ops import check_repository, purge_artifact
from cairn.storage import (
    Repository,
    annotation_members,
    annotations_table,
    artifacts,
    legacy_comments,
    meta,
)


@pytest.fixture
def repo(tmp_path):
    with closing(Repository(tmp_path / "source")) as value:
        yield value


def create(repo, name="training", *, finished=True, project="study"):
    result = repo.start(project, "runs", name)
    identifier, writer = result["artifact"]["id"], result["writer"]["id"]
    if finished:
        repo.close_writer(identifier, writer, 0)
        repo.finish(identifier)
    return identifier


def test_name_properties_cover_every_version_and_exact_keys_win(repo):
    first, second = create(repo), create(repo)
    repo.annotate(first, {"tags": ["paper"], "status": "draft"}, target="name")
    assert repo.resolve(second)["annotations"] == {"tags": ["paper"], "status": "draft"}
    own = repo.annotate(second, {"status": "final"})
    assert own["annotations"] == {"tags": ["paper"], "status": "final"}
    assert own["annotations_etag"] == digest(own["annotations"])
    assert repo.resolve(first)["annotations"]["status"] == "draft"
    started = repo.start("study", "runs", "training")["artifact"]
    assert started["annotations"] == {"tags": ["paper"], "status": "draft"}
    found = repo.list_artifacts(
        project="study", where={"path": "/annotations/tags", "op": "contains", "value": "paper"}
    )["items"]
    assert len(found) == 3
    assert repo.annotate(second, {"status": None})["annotations"]["status"] == "draft"
    assert repo.annotation_layers(second) == {
        "name": {"status": "draft", "tags": ["paper"]},
        "artifact": {},
        "annotations": {"status": "draft", "tags": ["paper"]},
        "annotations_etag": digest({"status": "draft", "tags": ["paper"]}),
    }
    replay = repo.annotate(first, {"owner": "me"}, target="name", request_id="name")
    assert repo.annotate(first, {"owner": "me"}, target="name", request_id="name") == replay
    with pytest.raises(Conflict):
        repo.annotate(first, {"owner": "me"}, request_id="name")
    with pytest.raises(ValidationError):
        repo.annotate(first, {"owner": "me"}, target="project")
    repo.annotate(first, {"tags": None, "owner": None, "status": None}, target="name")
    assert all(item["annotations"] == {} for item in repo.list_artifacts()["items"])
    assert check_repository(repo)["ok"]


def test_purge_keeps_name_properties_and_check_detects_drift(repo):
    first = create(repo)
    repo.annotate(first, {"tags": ["paper"]}, target="name")
    repo.delete(first)
    purge_artifact(repo, first)
    second = create(repo)
    assert repo.resolve(second)["annotations"] == {"tags": ["paper"]}
    assert check_repository(repo)["ok"]
    with repo._transaction(write=True) as conn:
        conn.execute(artifacts.update().values(annotations={}, annotations_etag=digest({})))
        conn.execute(annotation_members.delete())
    errors = check_repository(repo)["errors"]
    assert errors[0]["error"] == "Annotations differ from their stored properties"


def test_comments_span_versions_resolve_and_follow_deletion(repo):
    first, second = create(repo), create(repo)
    anchor = {"path": "report.qmd", "line_start": 2, "quote": "loss"}
    comment = repo.add_comment(first, anchor, "Explain", author="reviewer")
    assert {key: comment[key] for key in ("number", "revision", "author", "anchor")} == {
        "number": 1,
        "revision": 1,
        "author": "reviewer",
        "anchor": anchor,
    }
    assert comment["resolved_at"] is None and not comment["artifact_deleted"]
    assert repo.comments(second) == []
    assert repo.comments(second, scope="name") == [comment]
    with pytest.raises(Conflict):
        repo.update_comment(comment["id"], "Edited", expected_revision=2)
    assert repo.update_comment(comment["id"], "Edited", expected_revision=1)["revision"] == 2
    resolved = repo.resolve_comment(comment["id"], note="Done", by="agent", request_id="fix")
    assert repo.resolve_comment(comment["id"], note="Done", by="agent", request_id="fix") == (
        resolved
    )
    assert resolved["resolution"] == {"note": "Done", "by": "agent"} and resolved["resolved_at"]
    assert repo.comments(second, scope="name", include_resolved=False) == []
    with pytest.raises(ValidationError):
        repo.resolve_comment(comment["id"], resolved=False, note="Again")
    reopened = repo.resolve_comment(comment["id"], resolved=False)
    assert (reopened["resolved_at"], reopened["resolution"], reopened["revision"]) == (
        None,
        None,
        4,
    )
    repo.delete(first)
    assert repo.comments(second, scope="name")[0]["artifact_deleted"]
    with pytest.raises(NotFound):
        repo.add_comment(first, anchor, "Late")
    with pytest.raises(ValidationError):
        repo.comments(create(repo, finished=False))
    with pytest.raises(NotFound):
        repo.delete_comment("c_missing")
    removed = repo.delete_comment(comment["id"], request_id="remove")
    assert repo.delete_comment(comment["id"], request_id="remove") == removed
    assert repo.comments(second, scope="name") == []


@pytest.mark.parametrize("backend_fixture", ["backends", "postgres_backends"])
def test_name_annotations_and_comments_across_transports(request, backend_fixture):
    direct, remote = request.getfixturevalue(backend_fixture)
    identifier = create(direct)
    remote.annotate(identifier, {"tags": ["paper"]}, target="name")
    later = create(direct)
    assert remote.annotation_layers(later)["name"] == {"tags": ["paper"]}
    comment = remote.add_comment(identifier, {"path": "r.qmd"}, "Check", author="reviewer")
    reply = remote.reply_comment(comment["id"], "Why?", for_agent=True)
    assert remote.send_comment(reply["id"]) == direct.comments(later, scope="name")[1]
    remote.delete_comment(reply["id"])
    assert remote.comments(later, scope="name") == direct.comments(later, scope="name")
    assert remote.comments(later, scope="name") == [comment]
    assert remote.list_comments(project="study") == direct.list_comments(project="study")
    assert remote.resolve_comment(comment["id"])["resolved_at"]
    assert remote.list_comments("runs/**", project="study", include_resolved=False)["items"] == []
    assert check_repository(direct)["ok"]


def test_replies_join_threads_and_marked_comments_are_sent_until_edited(repo):
    first, second = create(repo), create(repo)
    question = repo.add_comment(first, {"path": "r.qmd"}, "Why?", for_agent=True)
    aside = repo.add_comment(second, {"path": "r.qmd"}, "Typo")
    answer = repo.reply_comment(question["id"], "Because", author="agent")
    again = repo.reply_comment(answer["id"], "Show it", for_agent=True)
    assert (answer["reply_to"], again["reply_to"]) == (question["id"], question["id"])
    assert answer["artifact_id"] == first and answer["anchor"] == {}
    sent = repo.send_comment(question["id"], request_id="send")
    assert sent["sent_at"] and repo.send_comment(question["id"], request_id="send") == sent
    assert repo.send_comment(question["id"]) == sent
    assert repo.send_comment(again["id"])["sent_at"]
    assert not repo.send_comment(aside["id"])["sent_at"]
    assert repo.update_comment(question["id"], "Why?")["sent_at"]
    assert not repo.update_comment(question["id"], "Why so?")["sent_at"]
    assert repo.send_comment(question["id"])["sent_at"] != sent["sent_at"]
    assert not repo.update_comment(question["id"], "Why so?", for_agent=False)["sent_at"]
    assert not repo.send_comment(question["id"])["sent_at"]
    with pytest.raises(ValidationError):
        repo.resolve_comment(answer["id"])
    repo.resolve_comment(question["id"])
    assert [item["id"] for item in repo.comments(second, scope="name", include_resolved=False)] == [
        aside["id"]
    ]
    with pytest.raises(Conflict):
        repo.reply_comment(question["id"], "Late")
    repo.resolve_comment(question["id"], resolved=False)
    assert len(repo.comments(second, scope="name", include_resolved=False)) == 4
    repo.delete_comment(question["id"])
    assert [item["id"] for item in repo.comments(second, scope="name")] == [aside["id"]]
    assert check_repository(repo)["ok"]


def test_comment_cli_replies_and_watches_sent_threads(tmp_path, capsys, monkeypatch):
    path = tmp_path / "repo"
    with closing(Repository(path)) as repo:
        identifier = create(repo)
        question = repo.add_comment(identifier, {"path": "r.qmd"}, "Why?", for_agent=True)
        repo.add_comment(identifier, {"path": "r.qmd"}, "Note to self")
    base = ["--repo", str(path), "--project", "study", "comments"]
    checks = []

    def sleep(seconds):
        checks.append(seconds)
        with closing(Repository(path)) as repo:
            if len(checks) == 1:
                repo.send_comment(question["id"])
            elif len(checks) == 3:
                reply = repo.reply_comment(question["id"], "More detail", for_agent=True)
                repo.send_comment(reply["id"])
            elif len(checks) == 5:
                repo.update_comment(question["id"], "Why exactly?")
                repo.send_comment(question["id"])
            elif len(checks) == 7:
                raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        main([*base, "watch", "--json", "--interval", "0.1"])
    batches = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [[c["body"] for c in thread] for batch in batches for thread in batch] == [
        ["Why?"],
        ["Why?", "More detail"],
        ["Why exactly?", "More detail"],
    ]
    assert main([*base, "reply", question["id"], "Done in v2", "--by", "agent"]) == 0
    assert json.loads(capsys.readouterr().out)["reply_to"] == question["id"]
    assert main([*base, "list"]) == 0
    assert "↳ " + question["id"] in capsys.readouterr().out


def test_comment_listings_cover_a_project_and_name_patterns_in_pages(repo):
    for name in ("training", "eval/a", "eval/b"):
        repo.add_comment(create(repo, name), {"path": "r.qmd"}, name)
    repo.add_comment(create(repo, project="elsewhere"), {"path": "r.qmd"}, "elsewhere")

    def bodies(pattern=None, **options):
        page = repo.list_comments(pattern, project="study", **options)
        return [item["body"] for item in page["items"]]

    assert bodies() == ["training", "eval/a", "eval/b"]
    assert bodies("runs/eval/*") == ["eval/a", "eval/b"]
    assert bodies("runs/*") == ["training"]
    assert bodies("runs/eval") == []
    first = repo.list_comments(project="study", limit=2)
    rest = repo.list_comments(project="study", limit=2, cursor=first["next_cursor"])
    assert [item["body"] for item in first["items"] + rest["items"]] == bodies()
    assert rest["next_cursor"] is None
    with pytest.raises(ValidationError):
        repo.list_comments(project="elsewhere", cursor=first["next_cursor"])
    with pytest.raises(ValidationError):
        repo.list_comments("runs/training@v1", project="study")


@pytest.mark.parametrize("backend_fixture", ["backends", "postgres_backends"])
def test_concurrent_name_and_exact_writes_keep_projections(request, backend_fixture):
    direct, _ = request.getfixturevalue(backend_fixture)
    first = create(direct)

    def write(index):
        if index % 3 == 0:
            direct.annotate(first, {f"name-{index}": index}, target="name")
        elif index % 3 == 1:
            direct.annotate(first, {f"exact-{index}": index})
        else:
            create(direct)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(write, range(24)))
    assert check_repository(direct)["ok"]
    latest = create(direct)
    assert len(direct.resolve(latest)["annotations"]) == 8


def downgrade(repo):
    """Rewrite a schema 7 repository into the schema 6 layout it would have had."""
    with repo._transaction(write=True) as conn:
        comments = conn.execute(
            sa.select(annotations_table).where(annotations_table.c.kind == "comment")
        ).mappings()
        legacy_comments.create(conn)
        for row in comments:
            conn.execute(
                legacy_comments.insert().values(
                    id=row["id"],
                    artifact_id=row["artifact_id"],
                    anchor=row["value"]["anchor"],
                    body=row["value"]["body"],
                    created_at=row["created_at"],
                )
            )
        annotations_table.drop(conn)
        conn.execute(meta.update().where(meta.c.key == "schema_version").values(value=6))


@pytest.mark.parametrize("backend_fixture", ["backends", "postgres_backends"])
def test_explicit_migration_from_schema_6(request, backend_fixture, tmp_path, capsys):
    direct, _ = request.getfixturevalue(backend_fixture)

    def reopen(**options):
        if direct.path:
            return Repository(direct.path, **options)
        url = direct.engine.url.render_as_string(hide_password=False)
        return Repository(database_url=url, object_store=direct.objects, **options)

    identifier = create(direct)
    before = direct.annotate(identifier, {"tags": ["paper"], "review": {"status": "ok"}})
    comment = direct.add_comment(identifier, {"path": "report.qmd"}, "Looks right")
    downgrade(direct)
    with pytest.raises(Unsupported, match="cairn repo migrate"):
        reopen()
    if direct.path:
        assert main(["--repo", str(direct.path), "repo", "migrate"]) == 0
        assert json.loads(capsys.readouterr().out) == {"schema_version": 7}
    else:
        reopen(migrate=True).close()
    with closing(reopen()) as repo:
        after = repo.resolve(identifier)
        assert (after["annotations"], after["annotations_etag"]) == (
            before["annotations"],
            before["annotations_etag"],
        )
        (migrated,) = repo.comments(identifier)
        assert {key: migrated[key] for key in ("id", "body", "anchor", "created_at")} == {
            key: comment[key] for key in ("id", "body", "anchor", "created_at")
        }
        assert repo.annotation_layers(identifier)["artifact"] == before["annotations"]
        assert check_repository(repo)["ok"]
    with pytest.raises(Conflict):
        reopen(migrate=True)
    with pytest.raises(NotFound):
        Repository(tmp_path / "empty", migrate=True)


def test_comment_cli_lists_resolves_and_reopens(tmp_path, capsys):
    path = tmp_path / "repo"
    with closing(Repository(path)) as repo:
        identifier = create(repo)
        anchor = {"path": "report.qmd", "line_start": 4, "line_end": 6, "quote": "loss"}
        comment = repo.add_comment(identifier, anchor, "Explain this\nplease")
    base = ["--repo", str(path), "--project", "study", "comments"]
    assert main([*base, "list", "runs/training"]) == 0
    line = (
        f'{comment["id"]}  runs/training@v1  report.qmd:4-6  "loss"  → Explain this\n    please\n'
    )
    assert capsys.readouterr().out == line
    assert main([*base, "list"]) == 0
    assert capsys.readouterr().out == line
    assert main([*base, "resolve", comment["id"], "--note", "Rewrote it", "--by", "agent"]) == 0
    assert json.loads(capsys.readouterr().out)["resolution"]["note"] == "Rewrote it"
    assert main([*base, "list", "runs/training"]) == 0
    assert capsys.readouterr().out == ""
    assert main([*base, "list", "runs/training", "--all", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["resolution"]["by"] == "agent"
    assert main([*base, "reopen", comment["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["resolved_at"] is None
    patch = '{"tags": ["paper"]}'
    assert (
        main(
            [
                "--repo",
                str(path),
                "--project",
                "study",
                "artifact",
                "annotate",
                "runs/training",
                patch,
                "--name",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["annotations"] == {"tags": ["paper"]}


@pytest.mark.parametrize("answer", ["n", "y"])
def test_server_offers_to_migrate_an_outdated_repository(tmp_path, monkeypatch, answer):
    from cairn import cli

    with closing(Repository(tmp_path / "repo")) as repo:
        downgrade(repo)

    def stop(*_):
        raise RuntimeError("stop before serving")

    monkeypatch.setattr(cli, "_server_socket", stop)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: answer)
    args = cli.parser().parse_args(["--repo", str(tmp_path / "repo"), "server", "--no-auth"])
    with pytest.raises(Unsupported if answer == "n" else RuntimeError):
        cli._serve(args)
    if answer == "y":
        Repository(tmp_path / "repo").close()

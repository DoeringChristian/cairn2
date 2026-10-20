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


def create(repo, name="training", *, finished=True):
    result = repo.start("study", "runs", name)
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
    assert remote.comments(later, scope="name") == direct.comments(later, scope="name")
    assert remote.comments(later, scope="name") == [comment]
    assert remote.resolve_comment(comment["id"])["resolved_at"]
    assert check_repository(direct)["ok"]


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
    line = f'{comment["id"]}  v1  report.qmd:4-6  "loss"  → Explain this\n    please\n'
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

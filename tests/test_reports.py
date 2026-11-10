import json
from contextlib import closing

import pytest
import sqlalchemy as sa
from test_http import serve

from cairn import CapacityError, Client, Conflict, NotFound, Unsupported, ValidationError
from cairn.cli import main
from cairn.ops import check_repository, export_repository, import_repository
from cairn.storage import Repository, annotations_table


def test_reports_keep_every_revision_and_separate_body_from_slots(tmp_path):
    with closing(Repository(tmp_path)) as repo:
        report = repo.create_report("demo", "study", body="# Plan", author="ada")
        with pytest.raises(Conflict):
            repo.create_report("demo", "study")
        assert repo.create_report("other", "study")["id"] != report["id"]
        assert repo.write_report(report["id"], "baseline", "Running.")["revision"] == 1
        assert repo.write_report(report["id"], "baseline", "Running.")["revision"] == 1
        assert repo.write_report(report["id"], "baseline", "Done.", expected_revision=1) == {
            "report_id": report["id"],
            "part": "baseline",
            "revision": 2,
        }
        with pytest.raises(Conflict) as stale:
            repo.write_report(report["id"], "body", "# Other", expected_revision=0)
        assert stale.value.details == {"revision": 1}
        for part, source in (("../x", ""), ("body", None)):
            with pytest.raises(ValidationError):
                repo.write_report(report["id"], part, source)
        with pytest.raises(CapacityError):
            repo.write_report(report["id"], "big", "x" * (1024 * 1024 + 1))
        parts = repo.report(report["id"])["parts"]
        assert {part: value["source"] for part, value in parts.items()} == {
            "baseline": "Done.",
            "body": "# Plan",
        }
        assert parts["body"]["author"] == "ada"
        assert [item["name"] for item in repo.reports(project="demo")] == ["study"]
        assert repo.reports(project="none") == []


def test_report_comments_share_threads_and_listings_with_artifacts(tmp_path):
    with closing(Repository(tmp_path)) as repo:
        started = repo.start("demo", "reports", "study")
        repo.close_writer(started["artifact"]["id"], started["writer"]["id"], 0)
        legacy = repo.finish(started["artifact"]["id"])
        repo.add_comment(legacy["id"], {"path": "report.qmd"}, "Old report")
        report = repo.create_report("demo", "study", body="# Plan")
        question = repo.add_report_comment(
            report["id"], {"path": "body", "quote": "Plan"}, "Why?", for_agent=True
        )
        assert (question["report_id"], question["artifact_id"]) == (report["id"], None)
        assert question["number"] is None and not question["artifact_deleted"]
        reply = repo.reply_comment(question["id"], "Because.", author="agent")
        assert reply["report_id"] == report["id"]
        assert repo.send_comment(question["id"])["sent_at"]
        assert [c["body"] for c in repo.report_comments(report["id"])] == ["Why?", "Because."]
        assert [c["body"] for c in repo.comments(legacy["id"], scope="name")] == ["Old report"]
        listed = repo.list_comments("reports/**", project="demo")["items"]
        assert [c["body"] for c in listed] == ["Old report", "Why?", "Because."]
        repo.resolve_comment(question["id"])
        assert repo.report_comments(report["id"], include_resolved=False) == []
        assert check_repository(repo)["ok"]
        exported = export_repository(repo, tmp_path / "export")
        repo.delete_report(report["id"])
        with pytest.raises(NotFound):
            repo.report(report["id"])
        assert [c["body"] for c in repo.list_comments(project="demo")["items"]] == ["Old report"]
    with closing(Repository(tmp_path / "copy")) as copy:
        assert import_repository(copy, exported)["imported"]["cairn_report_parts"] == 1
        assert [c["body"] for c in copy.report_comments(report["id"])] == ["Why?", "Because."]
        assert check_repository(copy)["ok"]


def test_schema_7_repositories_migrate_to_reports(tmp_path):
    with closing(Repository(tmp_path)) as repo:
        identifier = repo.start("demo", "runs", "train")["artifact"]["id"]
        with repo._transaction(write=True) as conn:
            columns = ", ".join(c.name for c in annotations_table.c if c.name != "report_id")
            conn.execute(
                sa.text(f"CREATE TABLE schema7 AS SELECT {columns} FROM cairn_annotations")
            )
            conn.execute(sa.text("DROP TABLE cairn_annotations"))
            conn.execute(sa.text("ALTER TABLE schema7 RENAME TO cairn_annotations"))
            conn.execute(sa.text("DROP TABLE cairn_report_parts"))
            conn.execute(sa.text("DROP TABLE cairn_reports"))
            conn.execute(sa.text("UPDATE cairn_meta SET value = '7' WHERE key = 'schema_version'"))
    with pytest.raises(Unsupported, match="cairn repo migrate"):
        Repository(tmp_path)
    with closing(Repository(tmp_path, migrate=True)) as repo:
        assert repo.info()["schema_version"] == 8
        assert repo.resolve(identifier)["id"] == identifier
        report = repo.create_report("demo", "study")
        repo.add_report_comment(report["id"], {"path": "body"}, "Works")
        assert check_repository(repo)["ok"]


def test_clients_edit_reports_by_name_over_http(tmp_path):
    repository = Repository(tmp_path / "repository")
    try:
        with serve(repository) as url, Client(url, token="test-token", project="demo") as client:
            client.create_report("study", "# Plan\n\n::: {.agent #baseline}\n:::\n")
            written = client.write_report("study", "baseline", "Chamfer 0.41.", by="agent")
            assert written["revision"] == 1
            with pytest.raises(Conflict):
                client.write_report("study", "baseline", "Stale.", expected_revision=0)
            comment = client.comment_report("study", "Seeds?", quote="Plan", by="agent")
            assert comment["anchor"] == {"path": "body", "quote": "Plan"}
            report = client.report("study")
            assert report["parts"]["baseline"]["author"] == "agent"
            assert [item["name"] for item in client.reports()] == ["study"]
            assert [c["body"] for c in client.comments("reports/**")] == ["Seeds?"]
            client.delete_report("study")
            with pytest.raises(NotFound):
                client.report("study")
    finally:
        repository.close()


def test_report_cli_writes_parts_and_waits_for_edits_and_sent_comments(
    tmp_path, capsys, monkeypatch
):
    path = tmp_path / "repo"
    base = ["--repo", str(path), "--project", "demo", "report"]
    source = tmp_path / "body.md"
    source.write_text("# Plan\n")
    assert main([*base, "new", "study", str(source)]) == 0
    report = json.loads(capsys.readouterr().out)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("Queued."))
    assert main([*base, "write", "study", "baseline", "--by", "agent"]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == 1
    assert main([*base, "comment", "study", "Too broad", "--quote", "Plan"]) == 0
    capsys.readouterr()
    checks = []

    def sleep(seconds):
        checks.append(seconds)
        with closing(Repository(path)) as repo:
            if len(checks) == 1:
                repo.write_report(report["id"], "body", "# Plan v2\n")
            elif len(checks) == 2:
                question = repo.report_comments(report["id"])[0]
                repo.update_comment(question["id"], "Too broad?", for_agent=True)
                repo.send_comment(question["id"])

    monkeypatch.setattr("time.sleep", sleep)
    assert main([*base, "watch", "study", "--settle", "3600", "--interval", "0.1"]) == 0
    woken = json.loads(capsys.readouterr().out)
    assert len(checks) == 2 and not woken["edited"]
    assert [[c["body"] for c in thread] for thread in woken["threads"]] == [["Too broad?"]]
    assert woken["report"]["parts"]["body"]["revision"] == 2
    with closing(Repository(path)) as repo:
        question = repo.report_comments(report["id"])[0]
        repo.reply_comment(question["id"], "Narrowed it.", author="agent")
    assert main([*base, "watch", "study", "--after", "1", "--settle", "0"]) == 0
    woken = json.loads(capsys.readouterr().out)
    assert woken["edited"] and woken["threads"] == []
    assert main([*base, "show", "study"]) == 0
    assert json.loads(capsys.readouterr().out)["parts"]["baseline"]["source"] == "Queued."

import contextvars
import json
import os
import uuid
from contextlib import ExitStack

import pytest
import sqlalchemy as sa
from test_http import serve

import cairn
from cairn import Client, Conflict, ValidationError
from cairn.objects import Files
from cairn.storage import Repository


def fits(client):
    ids = {}
    for model, slope in (("linear", 2), ("cubic", 1)):
        for rate in (0.1, 0.3):
            for version in (1, 2):
                run = client.start("runs", f"fit/{model}-lr{rate}")
                run.update({"config": {"model": model, "rate": rate}})
                run.log(step=0, loss=rate * slope)
                ids[model, rate, version] = run.finish(value={"loss": rate * slope})["id"]
    client.start("datasets", "points").finish(value=1)
    return ids


def postgres(stack, tmp_path):
    import psycopg
    from psycopg import sql

    base = os.environ.get("CAIRN_TEST_POSTGRES_URL")
    if not base:
        pytest.skip("Set CAIRN_TEST_POSTGRES_URL to an expendable PostgreSQL admin database")
    database = "cairn2_test_" + uuid.uuid4().hex
    admin = base.replace("postgresql+psycopg:", "postgresql:")
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))

    def drop():
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )

    stack.callback(drop)
    url = sa.engine.make_url(base).set(database=database).render_as_string(hide_password=False)
    return Repository(database_url=url, object_store=Files(tmp_path / "objects"))


@pytest.fixture(params=["local", "http", "postgres"])
def client(request, tmp_path):
    with ExitStack() as stack:
        store = (
            postgres(stack, tmp_path)
            if request.param == "postgres"
            else Repository(tmp_path / "repo")
        )
        stack.callback(store.close)
        destination = (
            tmp_path / "repo" if request.param == "local" else stack.enter_context(serve(store))
        )
        yield stack.enter_context(
            Client(destination, project="study", token="test-token", outbox=tmp_path / "outbox")
        )


def test_patterns_select_names_versions_and_aliases(client):
    ids = fits(client)
    latest = client.find("runs/fit/*")
    assert sorted(str(run) for run in latest) == [
        "runs/fit/cubic-lr0.1@v2",
        "runs/fit/cubic-lr0.3@v2",
        "runs/fit/linear-lr0.1@v2",
        "runs/fit/linear-lr0.3@v2",
    ]
    assert len(client.find("runs/fit/*@*")) == 8
    assert {run.id for run in client.find("runs/fit/cubic*@v1")} == {
        ids["cubic", 0.1, 1],
        ids["cubic", 0.3, 1],
    }
    client.backend.set_alias(ids["linear", 0.3, 1], "best")
    assert [run.id for run in client.find("runs/fit/*@best")] == [ids["linear", 0.3, 1]]
    assert [run.name for run in client.find("*/points")] == ["points"]
    for name in ("fit/cubic-lr0.1/seed1", "fitness/x", "fit.x"):
        client.start("runs", name).finish()
    assert len(client.find("runs/fit/*")) == 4
    assert [run.name for run in client.find("runs/fit/*/seed1")] == ["fit/cubic-lr0.1/seed1"]
    assert len(client.find("runs/fit/**")) == 5
    assert [run.name for run in client.find("runs/fit*")] == ["fit.x"]
    assert [run.name for run in client.find("runs/**/seed1")] == ["fit/cubic-lr0.1/seed1"]
    assert client.find("runs/fitXx") == []
    with pytest.raises(ValidationError, match="Names cannot contain"):
        client.start("runs", "a*b")
    assert client.find("RUNS/fit/*") == [] and client.find("runs/missing") == []
    assert [run.id for run in client.find(ids["cubic", 0.1, 1])] == [ids["cubic", 0.1, 1]]
    assert len(client.find("runs/fit/*@*", limit=3)) == 3
    for seed in (10, 2, 1):
        client.start("runs", f"sweep/seed{seed}").finish()
    assert [run.name for run in client.find("runs/sweep/*")] == [
        "sweep/seed1",
        "sweep/seed2",
        "sweep/seed10",
    ]
    with pytest.raises(ValidationError, match="finished"):
        client.find("runs/fit/*@live")


def test_where_and_order_run_in_the_database(client):
    ids = fits(client)
    best = client.find("runs/fit/*", order="/value/loss", limit=1)
    assert [run.id for run in best] == [ids["cubic", 0.1, 2]]
    worst = client.find("runs/fit/*", order="-/value/loss", limit=1)
    assert worst[0].id == ids["linear", 0.3, 2]
    assert [run.name for run in client.find("runs/fit/*", limit=2)] == [
        "fit/cubic-lr0.1",
        "fit/cubic-lr0.3",
    ]
    cubic = client.find(
        "runs/fit/*", where={"path": "/metadata/config/model", "op": "eq", "value": "cubic"}
    )
    assert {run.name for run in cubic} == {"fit/cubic-lr0.1", "fit/cubic-lr0.3"}
    low = client.find("runs/fit/*", where={"path": "/value/loss", "op": "lt", "value": 0.5})
    assert len(low) == 3
    page = client.backend.find("runs/fit/*", project="study", order="/value/loss", limit=1)
    assert page["items"][0]["manifest"]["value"] == {"loss": 0.1}
    with pytest.raises(ValidationError, match="/value, /metadata or /annotations"):
        client.find("runs/fit/*", order="/loss")


def test_runs_record_what_they_find_and_use(client):
    ids = fits(client)
    run = client.start("runs", "summary")

    def dependencies():
        run.flush()
        page = run.client.backend.query(run.id, project="study", kind="dependency")
        return [item["data"] for item in page["items"]]

    found = run.find("runs/fit/cubic*")
    assert [item["artifact_id"] for item in dependencies()] == [run.id for run in found]
    run.use("datasets/points", role="training-data")
    best = run.find("runs/fit/*", order="/value/loss", limit=1)
    tagged = {"path": "/annotations/tags", "op": "contains", "value": "paper"}
    assert run.find("runs/fit/*", where=tagged) == []
    assert best[0].id == ids["cubic", 0.1, 2]
    dataset = client.get("datasets/points").id
    assert dependencies() == [
        {"artifact_id": ids["cubic", 0.1, 2], "role": "input"},
        {"artifact_id": ids["cubic", 0.3, 2], "role": "input"},
        {"artifact_id": dataset, "role": "training-data"},
    ]
    run.finish()
    summary = client.get(run.id)
    assert [item.id for item in summary.inputs(role="training-data")] == [dataset]
    assert len(summary.dependencies) == 3
    assert found[0].value == {"loss": 0.1}
    with pytest.raises(Conflict, match="can no longer record what it reads"):
        run.find("runs/fit/*@v1")


def test_top_level_reads_record_to_the_current_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with Client() as client:
        fits(client)

    def scenario():
        for read in (cairn.find, cairn.use):
            with pytest.raises(ValidationError, match="cairn.Client"):
                read("runs/fit/cubic-lr0.1")
        with cairn.init("summary", provenance=False, capture_output=False) as run:
            reader = cairn.use("runs/fit/cubic-lr0.1")
            cairn.finish(value={"best": reader.id})
        with Client() as client:
            summary = client.get(run.id)
            assert summary.value == {"best": reader.id}
            assert [item.id for item in summary.dependencies] == [reader.id]

    contextvars.Context().run(scenario)


def test_reports_record_found_and_read_artifacts(tmp_path):
    from cairn import reports

    with Client(tmp_path / "repo") as client:
        ids = fits(client)
        source = tmp_path / "study.qmd"
        source.write_text("""---
title: Study
---

```{python}
import cairn
best = cairn.find("runs/fit/*", order="/value/loss", limit=1)[0]
listed = cairn.find("runs/fit/cubic*")
print(best.name, len(listed), cairn.Client().project)
```
""")
        result = reports.build(source, tmp_path / "build", client=client)
        inputs = json.loads((result.path / "inputs.json").read_text())
        assert [(item["artifact_id"], item["requested"]) for item in inputs] == [
            (ids["cubic", 0.1, 2], "runs/fit/*"),
            (ids["cubic", 0.3, 2], "runs/fit/cubic*"),
        ]
        notebook = json.loads((result.path / "executed.ipynb").read_text())
        printed = [
            "".join(o["text"])
            for cell in notebook["cells"]
            for o in cell.get("outputs", [])
            if "text" in o
        ]
        assert printed == ["fit/cubic-lr0.1 2 default\n"]


def test_finish_inside_a_run_context_and_decorator(tmp_path):
    def scenario():
        with cairn.init("context", repo=tmp_path / "repo", provenance=False) as run:
            cairn.finish(value={"loss": 0.1})

        @cairn.run("decorated", repo=tmp_path / "repo", provenance=False)
        def train():
            cairn.finish(value={"loss": 0.2})

        train()
        with Client(tmp_path / "repo") as client:
            assert client.get(run.id).value == {"loss": 0.1}
            assert client.get("runs/decorated").value == {"loss": 0.2}

    contextvars.Context().run(scenario)

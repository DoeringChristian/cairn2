from contextlib import ExitStack

import pytest
from test_http import serve

from cairn import Client, Conflict, ValidationError
from cairn.storage import Repository


@pytest.mark.parametrize("remote", [False, True])
def test_annotated_artifact_discovery_is_shared_and_paginates(tmp_path, monkeypatch, remote):
    with ExitStack() as stack:
        store = Repository(tmp_path / "repo")
        stack.callback(store.close)
        destination = stack.enter_context(serve(store)) if remote else tmp_path / "repo"
        client = stack.enter_context(
            Client(destination, project="study", token="test-token", outbox=tmp_path / "outbox")
        )
        ids = []
        for score in (0.1, 0.2, 0.9):
            artifact = client.start("runs", f"trial-{score}")
            artifact.update({"config": {"lr": 0.01}, "score": score})
            artifact.annotate({"tags": ["baseline"]})
            result = artifact.finish(value={"score": score})
            ids.append(result["id"])
        before = client.resolve(ids[0]).descriptor["manifest"]
        reader = client.resolve(ids[0])
        reader.annotate({"tags": ["baseline", "reviewed"]})
        assert reader.descriptor["annotations"]["tags"] == ["baseline", "reviewed"]
        assert reader.descriptor["manifest"] == before
        client.annotate(ids[1], {"tags": ["baseline", "reviewed"]})
        live = client.start("models", "working")
        live.annotate({"tags": ["baseline", "reviewed"]})
        original = client.backend.list_artifacts

        def small_pages(**kwargs):
            return original(**{**kwargs, "limit": 1})

        monkeypatch.setattr(client.backend, "list_artifacts", small_pages)
        result = list(
            client.find(
                namespace="runs",
                state="finished",
                where={
                    "and": [
                        {"path": "/metadata/config/lr", "op": "eq", "value": 0.01},
                        {"path": "/annotations/tags", "op": "contains", "value": "baseline"},
                        {"path": "/annotations/tags", "op": "contains", "value": "reviewed"},
                    ]
                },
            )
        )
        assert {item["id"] for item in result} == set(ids[:2])
        assert (
            len(
                list(
                    client.find(
                        where={"path": "/annotations/tags", "op": "contains", "value": "baseline"},
                        limit=2,
                    )
                )
            )
            == 2
        )
        assert [item["id"] for item in client.find(state="live")] == [live.id]
        reader.annotate({"tags": ["baseline"]})
        assert {
            item["id"]
            for item in client.find(
                where={"path": "/annotations/tags", "op": "contains", "value": "reviewed"},
                namespace="runs",
            )
        } == {ids[1]}
        assert {
            item["id"]
            for item in client.find(where={"path": "/metadata/score", "op": "lt", "value": 0.3})
        } == set(ids[:2])
        live.finish()
        with pytest.raises(ValidationError):
            list(client.find(limit=-1))
        with pytest.raises(ValidationError):
            client.annotate(ids[0], ["invalid"])
        assert list(client.find(limit=0)) == []
        # Discovery needs no writer or delivery journal, even for remote servers.
        with Client(
            destination, project="study", token="test-token", outbox=tmp_path / "reader-outbox"
        ) as read_client:
            assert len(list(read_client.find(namespace="runs"))) == 3
            assert read_client.delivery is None
            assert not (tmp_path / "reader-outbox").exists()


@pytest.mark.parametrize("remote", [False, True])
def test_annotation_merge_and_optimistic_update(tmp_path, remote):
    with ExitStack() as stack:
        store = Repository(tmp_path / "repo")
        stack.callback(store.close)
        destination = stack.enter_context(serve(store)) if remote else tmp_path / "repo"
        client = stack.enter_context(Client(destination, token="test-token"))
        writer = client.start("runs", "trial")
        writer.finish()
        reader = client.resolve(writer.id)
        initial = reader.descriptor["annotations_etag"]
        changed = reader.annotate(
            {"review": {"status": "draft", "owner": "Ada"}}, expected_etag=initial
        )
        with pytest.raises(Conflict):
            reader.annotate({"title": "stale"}, expected_etag=initial)
        merged = reader.annotate(
            {"review": {"status": "approved", "owner": None}},
            expected_etag=changed["annotations_etag"],
        )
        assert merged["annotations"] == {"review": {"status": "approved"}}
        assert merged["annotations_etag"] != changed["annotations_etag"]
        assert reader.descriptor == merged

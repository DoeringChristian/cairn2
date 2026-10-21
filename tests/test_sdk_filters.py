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
        reader = client.get(ids[0])
        reader.annotate({"tags": ["baseline", "reviewed"]})
        assert reader.annotations["tags"] == ["baseline", "reviewed"]
        assert client.get(ids[0]).value == {"score": 0.1}
        client.annotate(ids[1], {"tags": ["baseline", "reviewed"]})
        live = client.start("models", "working")
        live.annotate({"tags": ["baseline", "reviewed"]})
        original = client.backend.find

        def small_pages(*args, **kwargs):
            return original(*args, **{**kwargs, "limit": 1})

        monkeypatch.setattr(client.backend, "find", small_pages)
        result = client.find(
            "runs/*",
            where={
                "and": [
                    {"path": "/metadata/config/lr", "op": "eq", "value": 0.01},
                    {"path": "/annotations/tags", "op": "contains", "value": "baseline"},
                    {"path": "/annotations/tags", "op": "contains", "value": "reviewed"},
                ]
            },
        )
        assert {item.id for item in result} == set(ids[:2])
        tagged = {"path": "/annotations/tags", "op": "contains", "value": "baseline"}
        assert len(client.find("**", where=tagged, limit=2)) == 2
        assert [item["id"] for item in client.backend.list_artifacts(state="live")["items"]] == [
            live.id
        ]
        reader.annotate({"tags": ["baseline"]})
        reviewed = {"path": "/annotations/tags", "op": "contains", "value": "reviewed"}
        assert {item.id for item in client.find("runs/*", where=reviewed)} == {ids[1]}
        cheap = {"path": "/metadata/score", "op": "lt", "value": 0.3}
        assert {item.id for item in client.find("**", where=cheap)} == set(ids[:2])
        live.finish()
        with pytest.raises(ValidationError):
            client.find("**", limit=-1)
        with pytest.raises(ValidationError):
            client.annotate(ids[0], ["invalid"])
        assert client.find("**", limit=0) == []
        # Discovery needs no writer or delivery journal, even for remote servers.
        with Client(
            destination, project="study", token="test-token", outbox=tmp_path / "reader-outbox"
        ) as read_client:
            assert len(read_client.find("runs/*")) == 3
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
        reader = client.get(writer.id)
        initial = client.backend.resolve(writer.id)["annotations_etag"]
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
        assert reader.annotations == merged["annotations"]

"""Scale the real paging/chunking paths down to exercise capacity and crash boundaries."""

import hashlib
from contextlib import closing

import pytest
from test_http import serve

from cairn import Client, Directory, File, datasets
from cairn.model import Conflict, IntegrityError
from cairn.storage import Repository


@pytest.fixture(autouse=True)
def small_parts(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CAIRN_DATASET_CACHE_BYTES", "256")
    monkeypatch.setattr(datasets, "CHUNK_BYTES", 128)
    monkeypatch.setattr(datasets, "INLINE_BYTES", 256)
    monkeypatch.setattr(datasets, "PAGE_ITEMS", 3)


def source_tree(tmp_path):
    source = tmp_path / "images"
    source.mkdir()
    values = {f"frame-{i:04}.bin": bytes([i]) * (50 if i % 3 else 400) for i in range(14)}
    values.update({"nested/a.txt": b"hello", "nested/z.txt": b"world", "empty": b""})
    for name, data in values.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return source, values


def test_paged_directory_selective_reads_full_download_and_retention(tmp_path, monkeypatch):
    source, values = source_tree(tmp_path)
    with Client(tmp_path / "repo", project="science") as client:
        artifact = client.start("datasets", "images")
        result = artifact.finish(value=Directory(source))
        reader = client.get(result["id"])
        assert reader.value.version == 2
        assert reader.value.blobs == []
        assert set(reader.value.iter_files()) == set(values)
        opened = []
        original = client.backend.open_blob

        def opening(digest):
            opened.append(digest)
            return original(digest)

        monkeypatch.setattr(client.backend, "open_blob", opening)
        assert reader.file("nested/a.txt").load() == b"hello"
        assert opened == [hashlib.sha256(b"hello").hexdigest()]
        assert reader.file("nested/a.txt").load() == b"hello"
        assert len(opened) == 1
        with reader.file("frame-0000.bin").open() as stream:
            assert stream.seek(127) == 127
            assert stream.read(5) == values["frame-0000.bin"][127:132]
            stream.seek(-3, 2)
            assert stream.read() == values["frame-0000.bin"][-3:]
        destination = reader.download(to=tmp_path / "download")
        assert {
            str(p.relative_to(destination)): p.read_bytes()
            for p in destination.rglob("*")
            if p.is_file()
        } == values
        assert sum(p.stat().st_size for p in (tmp_path / "cache/dataset-chunks").iterdir()) <= 256
        # Internal parts can be soft-deleted, but the published dataset retains them transitively.
        client.backend.collect_garbage(grace_seconds=0, dry_run=False)
        assert reader.file("nested/z.txt").load() == b"world"
        assert result["manifest"]["dependencies"][0]["role"] == "dataset-part"


def test_remote_dataset_exceeds_outbox_without_staging_whole_directory(tmp_path, monkeypatch):
    source, values = source_tree(tmp_path)
    with closing(Repository(tmp_path / "repo")) as repository, serve(repository) as url:
        with Client(url, token="test-token", project="test", outbox=tmp_path / "outbox") as client:
            artifact = client.start("datasets", "remote")
            # Enough for JSON, but less than a larger generated source below.
            client.delivery.max_bytes = 8192
            (source / "large.bin").write_bytes(bytes(range(256)) * 100)
            values["large.bin"] = (source / "large.bin").read_bytes()
            uploaded = []
            original = client.backend.put_blob

            def bounded(data, **kwargs):
                uploaded.append(len(data))
                assert len(data) <= 128
                assert client.delivery._used() < 8192
                return original(data, **kwargs)

            monkeypatch.setattr(client.backend, "put_blob", bounded)
            artifact.add(source, name="images")
            artifact.finish(timeout=10)
            assert uploaded
            assert not list((tmp_path / "outbox/blobs").iterdir())
            identity = artifact.id
        # A fresh client/machine needs only the repository, not the upload journal.
        monkeypatch.setenv("CAIRN_STATE_DIR", str(tmp_path / "other-state"))
        monkeypatch.setenv("CAIRN_CACHE_DIR", str(tmp_path / "other-cache"))
        with Client(url, token="test-token", project="test") as other:
            reader = other.get(identity)
            assert reader.file("images/large.bin").load() == values["large.bin"]
            reader.download("images", to=tmp_path / "copy")
            assert (tmp_path / "copy/images/nested/a.txt").read_bytes() == b"hello"


def test_upload_restart_skips_acknowledged_bytes_and_recovers_lost_append_ack(
    tmp_path, monkeypatch
):
    source, values = source_tree(tmp_path)
    with Client(tmp_path / "repo") as client:
        original = client.backend.append_batch
        calls = 0

        def lose_ack(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 5:
                raise ConnectionError("lost acknowledgement")
            return result

        monkeypatch.setattr(client.backend, "append_batch", lose_ack)
        with pytest.raises(ConnectionError):
            client.start("datasets", "first-attempt").finish(value=Directory(source))
        monkeypatch.setattr(client.backend, "append_batch", original)
        uploads = []
        put = client.backend.put_blob

        def record(data, **kwargs):
            uploads.append(kwargs["digest"])
            return put(data, **kwargs)

        monkeypatch.setattr(client.backend, "put_blob", record)
        second = client.start("datasets", "retry")
        second.finish(value=Directory(source))
        # First three chunks of frame-0000 were acknowledged before failure.
        assert hashlib.sha256(bytes([0]) * 128).hexdigest() not in uploads
        assert client.get(second.id).file("frame-0000.bin").load() == values["frame-0000.bin"]


def test_partial_download_resumes_verified_chunks_and_rejects_corruption(tmp_path, monkeypatch):
    source = tmp_path / "big.bin"
    source.write_bytes(bytes(range(256)) * 4)
    with Client(tmp_path / "repo") as client:
        artifact = client.start("datasets", "file")
        artifact.finish(value=File(source))
        value = client.get(artifact.id).value
        original = client.backend.open_blob
        calls = 0

        def fail(digest):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ConnectionError("offline")
            return original(digest)

        monkeypatch.setattr(client.backend, "open_blob", fail)
        with pytest.raises(ConnectionError):
            value.download(tmp_path / "output")
        assert not (tmp_path / "output/big.bin").exists()
        assert next((tmp_path / "output").glob("*.partial")).stat().st_size == 128
        monkeypatch.setattr(client.backend, "open_blob", original)
        assert value.download(tmp_path / "output").read_bytes() == source.read_bytes()
        # Tampered cached chunks are detected and replaced.
        cache = tmp_path / "cache/dataset-chunks"
        cached = next(cache.iterdir())
        cached.chmod(0o644)
        cached.write_bytes(b"bad")
        assert value.load() == source.read_bytes()


def test_source_mutation_is_not_published_and_paths_cannot_escape(tmp_path, monkeypatch):
    source = tmp_path / "big.bin"
    source.write_bytes(b"a" * 400)
    with Client(tmp_path / "repo") as client:
        original = client.backend.put_blob

        def mutate(data, **kwargs):
            result = original(data, **kwargs)
            source.write_bytes(b"b" * 400)
            return result

        monkeypatch.setattr(client.backend, "put_blob", mutate)
        artifact = client.start("datasets", "changed")
        with pytest.raises(Conflict):
            artifact.finish(value=File(source))
        assert client.backend.resolve(artifact.id)["state"] == "live"


def test_repeated_finish_aliases_and_mixed_capture_downloads(tmp_path):
    source = tmp_path / "big.bin"
    source.write_bytes(b"abc" * 200)
    client = Client(tmp_path / "repo")
    artifact = client.start("datasets", "mixed")
    artifact.add(File(b"notes", name="original.txt"), name="renamed.txt")
    artifact.add(source, name="large.bin")
    first = artifact.finish(value=File(source, name="other.bin"))
    output = client.get(artifact.id).download(to=tmp_path / "all")
    assert (output / "renamed.txt").read_bytes() == b"notes"
    assert (output / "large.bin").read_bytes() == source.read_bytes()
    assert (output / "other.bin").read_bytes() == source.read_bytes()
    client.close()
    assert artifact.finish(value=File(source, name="other.bin")) == first


def test_dataset_part_gc_and_unacknowledged_upload_retention(tmp_path, monkeypatch):
    import sqlalchemy as sa

    from cairn.storage import blobs

    source, _ = source_tree(tmp_path)
    with Client(tmp_path / "repo") as client:
        repository = client.backend
        append = repository.append_batch

        def gc_between_chunks(*args, **kwargs):
            result = append(*args, **kwargs)
            with repository._transaction(write=True) as conn:
                conn.execute(blobs.update().values(lease_until=0, created_at="2000-01-01"))
            repository.collect_garbage(grace_seconds=0, dry_run=False)
            return result

        monkeypatch.setattr(repository, "append_batch", gc_between_chunks)
        artifact = client.start("datasets", "gc-test")
        artifact.finish(value=Directory(source))
        assert client.get(artifact.id).file("nested/a.txt").load() == b"hello"
        repository.delete(artifact.id)
        with repository._transaction(write=True) as conn:
            conn.execute(blobs.update().values(lease_until=0))
        repository.collect_garbage(grace_seconds=0, dry_run=False)
        with repository._transaction() as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(blobs)).scalar() == 0


def test_download_bad_bytes_never_become_a_completed_file(tmp_path, monkeypatch):
    import io

    source = tmp_path / "big.bin"
    source.write_bytes(b"abc" * 200)
    with Client(tmp_path / "repo") as client:
        artifact = client.start("datasets", "corrupt")
        artifact.finish(value=File(source))
        monkeypatch.setattr(client.backend, "open_blob", lambda digest: io.BytesIO(b"tampered"))
        with pytest.raises(IntegrityError, match="verification"):
            client.get(artifact.id).download(to=tmp_path / "output")
        assert not (tmp_path / "output/big.bin").exists()


def test_large_directory_rejects_symlinks_and_destination_escape(tmp_path):
    from cairn.model import ValidationError

    source, _ = source_tree(tmp_path)
    (source / "link").symlink_to(source / "empty")
    with pytest.raises(ValidationError, match="symlinks"):
        Directory(source)
    (source / "link").unlink()
    with Client(tmp_path / "repo") as client:
        artifact = client.start("datasets", "paths")
        artifact.finish(value=Directory(source))
        value = client.get(artifact.id).value
        with pytest.raises(ValidationError):
            value.file("../escape")
        output = tmp_path / "output"
        output.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (output / "nested").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValidationError, match="escapes"):
            value.download(output, name="nested/a.txt")
        assert not (outside / "a.txt").exists()


def test_paged_values_require_explicit_retention_and_old_servers_fail_early(tmp_path):
    from cairn.model import Unsupported, ValidationError, dependencies

    value = datasets.envelope(
        "directory", {"name": "data", "node": "abc", "size": 1, "count": 1}, version=2
    )
    with pytest.raises(ValidationError, match="retained"):
        dependencies(value)
    source, _ = source_tree(tmp_path)

    class Old:
        def _require(self, feature):
            raise Unsupported("Server does not advertise " + feature)

    with pytest.raises(Unsupported, match="paged_datasets"):
        datasets.upload(source, Old(), "project", directory=True, name="data")


def test_large_shared_path_captures_preserve_no_copy_behavior(tmp_path):
    source, values = source_tree(tmp_path)
    with Client(tmp_path / "repo") as client:
        artifact = client.start("datasets", "linked")
        artifact.add(source, name="images", link=True)
        artifact.add(source / "frame-0000.bin", name="single.bin", link=True)
        artifact.finish()
        reader = client.get(artifact.id)
        assert reader.file("images").version == 1
        assert not [p for p in (tmp_path / "repo/objects").rglob("*") if p.is_file()]
        path = reader.download("images", to=tmp_path / "copy")
        assert (path / "frame-0000.bin").resolve() == source / "frame-0000.bin"
        assert reader.file("single.bin").load() == values["frame-0000.bin"]


def test_dataset_export_import_and_reupload_after_collection(tmp_path):
    from cairn.ops import check_repository, export_repository, import_repository

    source, _ = source_tree(tmp_path)
    with Client(tmp_path / "repo") as client, Client(tmp_path / "restored") as restored:
        artifact = client.start("datasets", "original")
        artifact.finish(value=Directory(source))
        export_repository(client.backend, tmp_path / "export")
        import_repository(restored.backend, tmp_path / "export")
        assert check_repository(restored.backend, verify_blobs=True)["ok"]
        assert restored.get(artifact.id).file("nested/a.txt").load() == b"hello"
        client.backend.delete(artifact.id)
        client.backend.collect_garbage(grace_seconds=0, dry_run=False)
        replacement = client.start("datasets", "replacement")
        replacement.finish(value=Directory(source))
        assert client.get(replacement.id).file("nested/a.txt").load() == b"hello"


def test_upload_resumes_after_lost_finish_ack_in_a_fresh_client(tmp_path, monkeypatch):
    source, _ = source_tree(tmp_path)
    with Client(tmp_path / "repo") as first:
        original = first.backend.finish

        def lose_ack(*args, **kwargs):
            original(*args, **kwargs)
            raise ConnectionError("lost finish acknowledgement")

        monkeypatch.setattr(first.backend, "finish", lose_ack)
        with pytest.raises(ConnectionError):
            first.start("datasets", "first").finish(value=Directory(source))
    with Client(tmp_path / "repo") as second:
        artifact = second.start("datasets", "second")
        artifact.finish(value=Directory(source))
        assert second.get(artifact.id).file("nested/a.txt").load() == b"hello"


def test_download_preflights_nested_value_collisions_and_preserves_media_type(tmp_path):
    source = tmp_path / "big.bin"
    source.write_bytes(b"abc" * 200)
    with Client(tmp_path / "repo") as client:
        artifact = client.start("datasets", "collision")
        artifact.add(File(b"different", name="big.bin"))
        artifact.finish(value={"volume": File(source, media_type="application/x-volume")})
        reader = client.get(artifact.id)
        assert reader.value["volume"].data["media_type"] == "application/x-volume"
        with pytest.raises(Conflict, match="collide"):
            reader.download(to=tmp_path / "output")
        assert not (tmp_path / "output").exists()


def test_dataset_in_literal_dictionary_retains_real_values_only(tmp_path):
    source = tmp_path / "big.bin"
    source.write_bytes(b"abc" * 200)
    # The envelope-looking dictionary is ordinary user data, not an index to retire.
    literal = {"$cairn": 1, "format": "file", "version": 2, "data": {"node": "fake"}}
    with Client(tmp_path / "repo") as client:
        artifact = client.start("datasets", "literal")
        artifact.finish(value={**literal, "actual": File(source)})
        reader = client.get(artifact.id)
        output = reader.download(to=tmp_path / "output")
        assert (output / "big.bin").read_bytes() == source.read_bytes()
        assert reader.value["data"]["node"] == "fake"
        node = reader.value["actual"].data["node"]
        assert client.backend.resolve(node)["deleted_at"] is not None

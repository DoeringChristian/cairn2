"""Explicit repository maintenance; no background jobs or hidden migrations."""

import hashlib
import json
import os
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError as SQLIntegrityError

from .contract import (
    PUBLICATION_COLLECTION_LIMIT,
    PUBLICATION_METADATA_LIMIT,
    PUBLICATION_VALUE_LIMIT,
)
from .model import (
    CairnError,
    Conflict,
    IntegrityError,
    Unsupported,
    ValidationError,
    canonical,
    dependencies,
    digest,
    new_id,
    safe_path,
    validate_digest,
)
from .objects import sync_directory
from .storage import (
    NAME_KEYS,
    SCHEMA_VERSION,
    aliases,
    annotation_members,
    annotations_table,
    artifact_refs,
    artifacts,
    blob_refs,
    blobs,
    effective_annotations,
    meta,
    names,
    records,
    validate_annotations,
    writers,
)

TRANSFER_TABLES = (
    names,
    artifacts,
    writers,
    records,
    aliases,
    blob_refs,
    artifact_refs,
    annotation_members,
    annotations_table,
)


def _maintenance_active(conn):
    entries = conn.execute(sa.select(meta.c.value).where(meta.c.key.like("export:%"))).scalars()
    return any(entry["expires"] > time.time() for entry in entries)


def _sha(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _json_lines(path, expected=None):
    hasher, count = hashlib.sha256(), 0
    with Path(path).open("rb") as source:
        while line := source.readline(20 * 1024 * 1024 + 1):
            if len(line) > 20 * 1024 * 1024:
                raise IntegrityError("Transfer row exceeds 20 MiB")
            hasher.update(line)
            count += 1
            try:
                yield json.loads(line)
            except ValueError as exc:
                raise IntegrityError("Invalid transfer JSON") from exc
    if expected is not None and (
        hasher.hexdigest() != expected["sha256"] or count != expected["rows"]
    ):
        raise IntegrityError("Transfer table changed or failed verification")


def export_repository(repository, destination):
    """Export a consistent metadata snapshot and all retained bytes while writers continue."""
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise Conflict("Export destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".cairn-export-", dir=destination.parent))
    pin = "export:" + new_id("export")
    lease_duration = 24 * 3600
    try:
        with repository._transaction(write=True) as conn:
            # This short global retention pin prevents purge/GC, not ordinary writes.
            conn.execute(
                meta.insert().values(key=pin, value={"expires": time.time() + lease_duration})
            )
        manifest = {
            "format": "cairn/repository",
            "version": 1,
            "schema_version": SCHEMA_VERSION,
            "repository_id": repository.info()["repository_id"],
            "tables": {},
            "objects": {},
        }
        with repository._transaction() as conn:
            for table in TRANSFER_TABLES:
                filename = table.name + ".jsonl"
                count = 0
                with (stage / filename).open("wb") as target:
                    statement = sa.select(table).order_by(*table.primary_key.columns)
                    result = conn.execution_options(stream_results=True).execute(statement)
                    for row in result.mappings():
                        target.write(canonical(dict(row)) + b"\n")
                        count += 1
                    target.flush()
                    os.fsync(target.fileno())
                manifest["tables"][table.name] = {
                    "file": filename,
                    "rows": count,
                    "sha256": _sha(stage / filename),
                }
            objects_file = stage / "objects.jsonl"
            count = 0
            with objects_file.open("wb") as target:
                statement = (
                    sa.select(blobs.c.digest, blobs.c.size)
                    .where(
                        sa.exists(
                            sa.select(blob_refs.c.owner).where(blob_refs.c.digest == blobs.c.digest)
                        )
                    )
                    .order_by(blobs.c.digest)
                )
                for row in conn.execute(statement).mappings():
                    target.write(canonical(dict(row)) + b"\n")
                    count += 1
                target.flush()
                os.fsync(target.fileno())
            manifest["objects"] = {
                "file": "objects.jsonl",
                "rows": count,
                "sha256": _sha(objects_file),
            }
        object_dir = stage / "objects"
        object_dir.mkdir()
        for item in _json_lines(objects_file):
            sha = validate_digest(item["digest"])
            with repository.open_blob(sha) as source, (object_dir / sha).open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            if (object_dir / sha).stat().st_size != item["size"] or _sha(object_dir / sha) != sha:
                raise IntegrityError("Exported content failed verification")
            with repository._transaction(write=True) as conn:
                lease = conn.execute(
                    sa.select(meta.c.value).where(meta.c.key == pin).with_for_update()
                ).scalar()
                if not lease or lease["expires"] <= time.time():
                    raise Conflict("Export retention lease expired")
                conn.execute(
                    meta.update()
                    .where(meta.c.key == pin)
                    .values(value={"expires": time.time() + lease_duration})
                )
        (stage / "repository.json").write_bytes(canonical(manifest))
        with (stage / "repository.json").open("rb") as source:
            os.fsync(source.fileno())
        sync_directory(object_dir)
        sync_directory(stage)
        os.replace(stage, destination)
        sync_directory(destination.parent)
        return destination
    finally:
        with repository._transaction(write=True) as conn:
            conn.execute(meta.delete().where(meta.c.key == pin))
        if stage.exists():
            shutil.rmtree(stage)


def _read_transfer(source):
    source = Path(source).expanduser().resolve()
    try:
        manifest = json.loads((source / "repository.json").read_bytes())
    except (OSError, ValueError) as exc:
        raise IntegrityError("Not a complete repository export") from exc
    if (
        manifest.get("format") != "cairn/repository"
        or manifest.get("version") != 1
        or manifest.get("schema_version") != SCHEMA_VERSION
    ):
        raise Unsupported("Unsupported repository export version")
    if set(manifest.get("tables", {})) != {table.name for table in TRANSFER_TABLES}:
        raise IntegrityError("Missing or unexpected export tables")
    for entry in [*manifest["tables"].values(), manifest["objects"]]:
        path = source / safe_path(entry["file"])
        if (
            not path.resolve().is_relative_to(source)
            or not path.is_file()
            or _sha(path) != entry["sha256"]
        ):
            raise IntegrityError("Transfer table digest mismatch")
        if sum(1 for _ in _json_lines(path)) != entry["rows"]:
            raise IntegrityError("Transfer table row count mismatch")
    return source, manifest


def import_repository(repository, source):
    """Stage verified objects, then atomically expose metadata. Conflicts never merge silently."""
    source, manifest = _read_transfer(source)
    for item in _json_lines(source / manifest["objects"]["file"], manifest["objects"]):
        sha = validate_digest(item["digest"])
        path = source / "objects" / sha
        if not path.resolve().is_relative_to(source) or not path.is_file():
            raise IntegrityError("Missing transfer content")
        repository.put_blob(path, digest=sha, size=item["size"])
    counts = {}
    try:
        with repository._transaction(write=True) as conn:
            for table in TRANSFER_TABLES:
                count = 0
                entry = manifest["tables"][table.name]
                for row in _json_lines(source / entry["file"], entry):
                    if not isinstance(row, dict) or set(row) != set(table.c.keys()):
                        raise IntegrityError(f"Unexpected columns in {table.name}")
                    canonical(row)
                    if table is writers:
                        row = {**row, "closed": True}
                    if table is records:
                        expected = digest(
                            {"sequence": row["sequence"], "kind": row["kind"], "data": row["data"]}
                        )
                        if expected != row["hash"]:
                            raise IntegrityError("Transferred record hash mismatch")
                    condition = sa.and_(
                        *(table.c[col.name] == row[col.name] for col in table.primary_key.columns)
                    )
                    old = (
                        conn.execute(sa.select(table).where(condition).with_for_update())
                        .mappings()
                        .first()
                    )
                    if old is not None:
                        if canonical(dict(old)) != canonical(row):
                            raise Conflict(
                                f"Destination already has different {table.name} data",
                                details={
                                    "id": {
                                        col.name: row[col.name] for col in table.primary_key.columns
                                    }
                                },
                            )
                    else:
                        conn.execute(table.insert().values(**row))
                        count += 1
                counts[table.name] = count
            failure = next(_integrity_errors(conn), None)
            if failure is not None:
                raise IntegrityError("Transferred repository is inconsistent", details=failure)
    except SQLIntegrityError as exc:
        raise Conflict(
            "Transfer violates existing names, identities or reference integrity"
        ) from exc
    return {
        "imported": counts,
        "objects": manifest["objects"]["rows"],
        "source_repository_id": manifest["repository_id"],
        "repository_id": repository.info()["repository_id"],
    }


def _record_state(conn, artifact_id, cutoff):
    """Reconstruct evidence from the acknowledged prefix, independently of its indexes."""
    count, metadata, captures, content, refs, producers = 0, {}, {}, {}, {}, {}
    statement = (
        sa.select(records)
        .where(records.c.artifact_id == artifact_id, records.c.ordinal <= cutoff)
        .order_by(records.c.ordinal)
    )
    for row in conn.execute(statement).mappings():
        count += 1
        sequence = producers.get(row["writer_id"], 0) + 1
        if row["ordinal"] != count or row["sequence"] != sequence:
            raise IntegrityError("Record ordinals or producer sequences are not contiguous")
        producers[row["writer_id"]] = sequence
        kind, data = row["kind"], row["data"]
        if kind not in {"record", "metadata", "capture", "dependency"} or not isinstance(
            data, dict
        ):
            raise IntegrityError("Invalid stored record")
        if digest({"sequence": sequence, "kind": kind, "data": data}) != row["hash"]:
            raise IntegrityError("Stored record hash mismatch")
        blob_list, ref_list = dependencies(data)
        content.update((item["digest"], item) for item in blob_list)
        refs.update(((item["artifact_id"], item["role"]), item) for item in ref_list)
        if kind == "metadata":
            metadata.update(data)
        elif kind == "capture":
            if set(data) != {"name", "value"}:
                raise IntegrityError("Invalid capture record")
            safe_path(data["name"])
            captures[data["name"]] = data["value"]
        elif kind == "dependency":
            if set(data) - {"artifact_id", "role"} or not isinstance(data.get("artifact_id"), str):
                raise IntegrityError("Invalid dependency record")
            role = data.get("role", "input")
            if not isinstance(role, str):
                raise IntegrityError("Invalid dependency role")
            refs[(data["artifact_id"], role)] = {"artifact_id": data["artifact_id"], "role": role}
    if count != cutoff:
        raise IntegrityError("Record prefix does not reach its acknowledged cutoff")
    return metadata, captures, list(content.values()), list(refs.values()), producers


def _check_retention(conn, owner, blob_list, ref_list, *, exact=False):
    declared = {item["digest"]: item["size"] for item in blob_list}
    refs = {(item["artifact_id"], item["role"]) for item in ref_list}
    indexed_blobs = set(
        conn.execute(sa.select(blob_refs.c.digest).where(blob_refs.c.owner == owner)).scalars()
    )
    indexed_refs = set(
        conn.execute(
            sa.select(artifact_refs.c.artifact_id, artifact_refs.c.role).where(
                artifact_refs.c.owner == owner
            )
        ).all()
    )
    if not set(declared) <= indexed_blobs or not refs <= indexed_refs:
        raise IntegrityError("Retention index omits acknowledged evidence")
    if exact and (indexed_blobs != set(declared) or indexed_refs != refs):
        raise IntegrityError("Manifest and retention index disagree")
    for sha, size in declared.items():
        blob = conn.execute(sa.select(blobs).where(blobs.c.digest == sha)).mappings().first()
        if blob is None or blob["state"] != "ready" or blob["size"] != size:
            raise IntegrityError("Evidence references unavailable content")
    for artifact_id, _ in refs:
        if not conn.execute(
            sa.select(artifacts.c.id).where(
                artifacts.c.id == artifact_id, artifacts.c.state == "finished"
            )
        ).first():
            raise IntegrityError("Evidence references an unavailable finished artifact")


def _check_manifest(conn, artifact):
    manifest = artifact["manifest"]
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"value", "metadata", "captures", "blobs", "dependencies", "outcome"}
        or not isinstance(manifest["metadata"], dict)
        or not isinstance(manifest["captures"], dict)
    ):
        raise IntegrityError("Invalid artifact manifest")
    _, captures, record_blobs, record_refs, _ = _record_state(
        conn, artifact["id"], artifact["cutoff"]
    )
    if canonical(captures) != canonical(manifest["captures"]):
        raise IntegrityError("Manifest captures disagree with the frozen record prefix")
    expected_blobs, expected_refs = dependencies(
        [manifest["value"], manifest["metadata"], manifest["captures"]]
    )
    declared_blobs, declared_refs = dependencies(
        {
            "$cairn": 1,
            "version": 1,
            "format": "manifest",
            "blobs": manifest["blobs"],
            "refs": manifest["dependencies"],
        }
    )
    declared = {entry["digest"]: entry["size"] for entry in declared_blobs}
    refs = {(entry["artifact_id"], entry["role"]) for entry in declared_refs}
    if any(
        declared.get(entry["digest"]) != entry["size"] for entry in expected_blobs + record_blobs
    ):
        raise IntegrityError("Manifest omits value or record content dependencies")
    if any(
        (entry["artifact_id"], entry["role"]) not in refs for entry in expected_refs + record_refs
    ):
        raise IntegrityError("Manifest omits value or record artifact dependencies")
    _check_retention(conn, artifact["id"], declared_blobs, declared_refs, exact=True)


def _check_artifact(conn, artifact):
    expected = validate_annotations(artifact["annotations"])
    if artifact["annotations_etag"] != digest(artifact["annotations"]):
        raise IntegrityError("Annotation hash mismatch")
    if canonical(effective_annotations(conn, artifact)) != canonical(artifact["annotations"]):
        raise IntegrityError("Annotations differ from their stored properties")
    actual = set(
        conn.execute(
            sa.select(annotation_members.c.path_key, annotation_members.c.value_key).where(
                annotation_members.c.artifact_id == artifact["id"]
            )
        ).all()
    )
    if actual != expected:
        raise IntegrityError("Annotation membership index mismatch")
    if not conn.execute(
        sa.select(names).where(
            *(names.c[key] == artifact[key] for key in ("project", "namespace", "name"))
        )
    ).first():
        raise IntegrityError("Artifact has no name index")
    if artifact["state"] not in {"live", "closing", "finished"} or artifact["cutoff"] < 0:
        raise IntegrityError("Invalid artifact state or cutoff")
    intent = artifact["intent"]
    if artifact["state"] == "live":
        if intent is not None:
            raise IntegrityError("Live artifact has a finalization intent")
    elif (
        not isinstance(intent, dict)
        or set(intent) != {"value", "metadata", "outcome"}
        or intent["outcome"] not in {"completed", "failed", "cancelled", "incomplete"}
        or (intent["metadata"] is not None and not isinstance(intent["metadata"], dict))
    ):
        raise IntegrityError("Invalid artifact finalization intent")
    metadata, _, content, refs, producers = _record_state(conn, artifact["id"], artifact["cutoff"])
    total = conn.execute(
        sa.select(sa.func.count())
        .select_from(records)
        .where(records.c.artifact_id == artifact["id"])
    ).scalar_one()
    projected = metadata
    if artifact["state"] == "finished":
        projected = {**metadata, **((artifact["intent"] or {}).get("metadata") or {})}
    if total != artifact["cutoff"] or canonical(projected) != canonical(artifact["metadata"]):
        raise IntegrityError("Artifact projection disagrees with its acknowledged records")
    intent_blobs, intent_refs = dependencies(artifact["intent"])
    _check_retention(conn, artifact["id"], content + intent_blobs, refs + intent_refs)
    publication_bytes = sum(
        len(canonical({data["name"]: data["value"]}))
        for data in conn.execute(
            sa.select(records.c.data).where(
                records.c.artifact_id == artifact["id"], records.c.kind == "capture"
            )
        ).scalars()
    )
    retained_blobs = conn.execute(
        sa.select(blobs.c.digest, blobs.c.size)
        .join(blob_refs, blobs.c.digest == blob_refs.c.digest)
        .where(blob_refs.c.owner == artifact["id"])
    ).mappings()
    retained_refs = conn.execute(
        sa.select(artifact_refs.c.artifact_id, artifact_refs.c.role).where(
            artifact_refs.c.owner == artifact["id"]
        )
    ).mappings()
    publication_bytes += sum(
        len(canonical(dict(item))) + 1 for rows in (retained_blobs, retained_refs) for item in rows
    )
    if (
        artifact["publication_bytes"] != publication_bytes
        or publication_bytes > PUBLICATION_COLLECTION_LIMIT
    ):
        raise IntegrityError("Artifact publication capacity counter disagrees with evidence")
    final_metadata = {**metadata, **((artifact["intent"] or {}).get("metadata") or {})}
    if (
        len(canonical(final_metadata)) > PUBLICATION_METADATA_LIMIT
        or len(canonical((artifact["intent"] or {}).get("value"))) > PUBLICATION_VALUE_LIMIT
    ):
        raise IntegrityError("Artifact publication inputs exceed capacity")
    writer_rows = list(
        conn.execute(sa.select(writers).where(writers.c.artifact_id == artifact["id"])).mappings()
    )
    if not writer_rows or set(producers) - {row["id"] for row in writer_rows}:
        raise IntegrityError("Record producer does not belong to its artifact")
    for writer in writer_rows:
        if writer["acknowledged"] != producers.get(writer["id"], 0):
            raise IntegrityError("Writer acknowledgement disagrees with stored records")
        if artifact["state"] in {"finished"} and not writer["closed"]:
            raise IntegrityError("An immutable artifact has an open writer")
    if artifact["state"] in {"closing", "finished"} and not isinstance(artifact["intent"], dict):
        raise IntegrityError("Closing artifact has no finalization intent")
    if artifact["state"] == "finished":
        if artifact["number"] is None or artifact["number"] < 1 or not artifact["finished_at"]:
            raise IntegrityError("Finished artifact is missing its version label or timestamp")
        _check_manifest(conn, artifact)
        manifest = artifact["manifest"]
        intent = artifact["intent"]
        if (
            canonical(manifest["metadata"]) != canonical(final_metadata)
            or canonical(manifest["value"]) != canonical(intent["value"])
            or manifest["outcome"] != intent["outcome"]
        ):
            raise IntegrityError("Finished artifact disagrees with its finalization intent")
    elif (
        artifact["manifest"] is not None
        or artifact["number"] is not None
        or artifact["finished_at"]
    ):
        raise IntegrityError("Live artifact has immutable publication data")


def _check_name(conn, name):
    scope = sa.and_(*(artifacts.c[key] == name[key] for key in ("project", "namespace", "name")))
    maximum = conn.execute(sa.select(sa.func.max(artifacts.c.number)).where(scope)).scalar() or 0
    if name["next_version"] <= maximum:
        raise IntegrityError("Name counter would reuse a version label")
    selections = list(
        conn.execute(
            sa.select(aliases.c.artifact_id).where(
                *(aliases.c[key] == name[key] for key in ("project", "namespace", "name"))
            )
        ).scalars()
    )
    if name["head"] is not None:
        selections.append(name["head"])
    for selected in selections:
        target = (
            conn.execute(sa.select(artifacts).where(artifacts.c.id == selected)).mappings().first()
        )
        if (
            target is None
            or target["state"] != "finished"
            or any(target[key] != name[key] for key in ("project", "namespace", "name"))
        ):
            raise IntegrityError("Name or alias points outside its finished artifacts")


def _integrity_errors(conn):
    for (identifier,) in conn.execute(sa.select(annotation_members.c.artifact_id).distinct()):
        if not conn.execute(sa.select(artifacts.c.id).where(artifacts.c.id == identifier)).first():
            yield {
                "table": annotation_members.name,
                "id": identifier,
                "error": "Annotation index refers to an unknown artifact",
            }

    stray = conn.execute(
        sa.select(annotations_table.c.id)
        .outerjoin(artifacts, artifacts.c.id == annotations_table.c.artifact_id)
        .where(
            sa.or_(
                ~annotations_table.c.kind.in_(["property", "comment"]),
                ~sa.exists(
                    sa.select(names.c.name).where(
                        *(names.c[key] == annotations_table.c[key] for key in NAME_KEYS)
                    )
                ),
                sa.and_(
                    annotations_table.c.artifact_id.is_not(None),
                    sa.or_(*(artifacts.c[key] != annotations_table.c[key] for key in NAME_KEYS)),
                ),
                sa.and_(
                    annotations_table.c.kind == "comment",
                    sa.or_(
                        annotations_table.c.artifact_id.is_(None), artifacts.c.state != "finished"
                    ),
                ),
            )
        )
        .limit(1)
    ).scalar()
    if stray:
        yield {"table": annotations_table.name, "id": stray, "error": "Annotation target mismatch"}
    for table, check in ((names, _check_name), (artifacts, _check_artifact)):
        for row in conn.execute(sa.select(table)).mappings():
            try:
                check(conn, row)
            except (CairnError, KeyError, TypeError, ValueError) as exc:
                yield {"table": table.name, "id": row.get("id", row.get("name")), "error": str(exc)}
    for table in (blob_refs, artifact_refs):
        orphan = conn.execute(
            sa.select(table.c.owner)
            .where(~sa.exists(sa.select(artifacts.c.id).where(artifacts.c.id == table.c.owner)))
            .limit(1)
        ).scalar()
        if orphan:
            yield {"table": table.name, "id": orphan, "error": "Unknown retention owner"}


def check_repository(repository, *, verify_blobs=False):
    errors, counts = [], {}
    with repository._transaction() as conn:
        for table in TRANSFER_TABLES:
            counts[table.name] = conn.execute(
                sa.select(sa.func.count()).select_from(table)
            ).scalar_one()
        errors.extend(_integrity_errors(conn))
        content = list(
            conn.execute(
                sa.select(blobs.c.digest, blobs.c.size, blobs.c.key).where(
                    blobs.c.state == "ready",
                    sa.exists(
                        sa.select(blob_refs.c.owner).where(blob_refs.c.digest == blobs.c.digest)
                    ),
                )
            ).mappings()
        )
    for blob in content:
        try:
            if verify_blobs:
                hasher, size = hashlib.sha256(), 0
                with repository.open_blob(blob["digest"]) as stream:
                    while chunk := stream.read(1024 * 1024):
                        hasher.update(chunk)
                        size += len(chunk)
                if hasher.hexdigest() != blob["digest"] or size != blob["size"]:
                    raise IntegrityError("Stored bytes do not match digest/size")
            elif blob["key"].startswith("link:"):
                repository._linked(blob)
        except (OSError, CairnError) as exc:
            errors.append({"blob": blob["digest"], "error": str(exc)})
    return {"ok": not errors, "errors": errors, "counts": counts}


def purge_artifact(repository, artifact_id):
    with repository._transaction(write=True) as conn:
        if _maintenance_active(conn):
            raise Conflict("A repository export currently pins retained data")
        artifact = repository._artifact(conn, artifact_id, lock=True, deleted=True)
        if not artifact["deleted_at"]:
            raise Conflict("Delete the artifact before permanently purging it")
        incoming = list(
            conn.execute(
                sa.select(artifact_refs.c.owner).where(
                    artifact_refs.c.artifact_id == artifact_id, artifact_refs.c.owner != artifact_id
                )
            ).scalars()
        )
        if incoming:
            raise Conflict(
                "Retained evidence still references this artifact", details={"owners": incoming}
            )
        _purge(conn, [artifact_id])
        return {"id": artifact_id, "purged": True}


def _purge(conn, identifiers):
    """Remove rows of artifacts that no artifact outside identifiers references."""
    for table in (blob_refs, artifact_refs):
        conn.execute(table.delete().where(table.c.owner.in_(identifiers)))
    for table in (annotations_table, aliases, annotation_members, records, writers):
        conn.execute(table.delete().where(table.c.artifact_id.in_(identifiers)))
    conn.execute(names.update().where(names.c.head.in_(identifiers)).values(head=None))
    # Names retain their counter; request receipts cannot resurrect deleted artifact IDs.
    conn.execute(artifacts.delete().where(artifacts.c.id.in_(identifiers)))


def _purgeable(conn, cutoff):
    """Removed artifacts past the grace period that only other such artifacts reference."""
    selected = set(
        conn.execute(
            sa.select(artifacts.c.id)
            .where(artifacts.c.deleted_at.is_not(None), artifacts.c.deleted_at < cutoff)
            .with_for_update()
        ).scalars()
    )
    while selected:
        held = set(
            conn.execute(
                sa.select(artifact_refs.c.artifact_id).where(
                    artifact_refs.c.artifact_id.in_(selected),
                    artifact_refs.c.owner.not_in(selected),
                )
            ).scalars()
        )
        if not held:
            break
        selected -= held
    return sorted(selected)


def collect_garbage(repository, *, grace_seconds=3600, dry_run=True):
    if grace_seconds < 0:
        raise ValidationError("Grace period cannot be negative")
    removed, candidates = [], []
    timestamp = time.time()
    cutoff = datetime.fromtimestamp(timestamp - grace_seconds, UTC).isoformat()
    with repository._transaction(write=True) as conn:
        if _maintenance_active(conn):
            return {
                "dry_run": dry_run,
                "pinned": True,
                "artifacts": [],
                "candidates": [],
                "removed": [],
            }
        purged = _purgeable(conn, cutoff)
        if not dry_run and purged:
            _purge(conn, purged)
    with repository._transaction(write=True) as conn:
        if _maintenance_active(conn):
            return {
                "dry_run": dry_run,
                "pinned": True,
                "artifacts": purged,
                "candidates": [],
                "removed": [],
            }
        candidates = [
            dict(row)
            for row in conn.execute(
                sa.select(blobs)
                .where(
                    blobs.c.lease_until <= timestamp,
                    blobs.c.created_at < cutoff,
                    ~sa.exists(
                        sa.select(blob_refs.c.owner).where(blob_refs.c.digest == blobs.c.digest)
                    ),
                )
                .with_for_update()
            ).mappings()
        ]
        # PostgreSQL may wait on an appender's blob lock after taking the SELECT's
        # snapshot. Recheck its now-committed references while holding that lock.
        candidates = [
            row
            for row in candidates
            if not conn.execute(
                sa.select(blob_refs.c.owner).where(blob_refs.c.digest == row["digest"]).limit(1)
            ).first()
        ]
        if not dry_run:
            for row in candidates:
                conn.execute(
                    blobs.update()
                    .where(blobs.c.digest == row["digest"])
                    .values(state="deleting", lease_until=timestamp + 3600)
                )
    if not dry_run:
        for row in candidates:
            repository.delete_object(row["key"])
            with repository._transaction(write=True) as conn:
                conn.execute(
                    blobs.delete().where(
                        blobs.c.digest == row["digest"],
                        blobs.c.key == row["key"],
                        blobs.c.state == "deleting",
                    )
                )
            removed.append(row["digest"])
    # A late uploader can leave bytes after its lease was reclaimed, but cannot register them.
    with repository._transaction() as conn:
        registered = set(conn.execute(sa.select(blobs.c.key)).scalars())
    orphaned = []
    for item in repository.objects.inventory():
        if item["key"] not in registered and item["modified"] + grace_seconds < timestamp:
            with repository._transaction(write=True) as conn:
                if conn.execute(
                    sa.select(blobs.c.digest).where(
                        blobs.c.key == item.get("upload_key", item["key"])
                    )
                ).first():
                    continue
                orphaned.append(item["key"])
                if not dry_run:
                    repository.objects.delete(item["key"])
    return {
        "dry_run": dry_run,
        "pinned": False,
        "artifacts": purged,
        "candidates": [row["digest"] for row in candidates],
        "removed": removed,
        "orphans": orphaned,
    }

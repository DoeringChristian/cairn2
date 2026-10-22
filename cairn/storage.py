"""One transactional repository for direct callers and the HTTP adapter."""

import hashlib
import io
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError as SQLIntegrityError

from . import query as queries
from .contract import (
    PUBLICATION_COLLECTION_LIMIT,
    PUBLICATION_METADATA_LIMIT,
    PUBLICATION_VALUE_LIMIT,
    ArtifactDescriptor,
    QueryPage,
    StartResult,
    WriterDescriptor,
)
from .model import (
    PROTOCOL_VERSION,
    CapacityError,
    Conflict,
    IntegrityError,
    NotFound,
    NotPublished,
    Pending,
    Unsupported,
    ValidationError,
    canonical,
    dependencies,
    digest,
    new_id,
    now,
    safe_path,
    validate_digest,
    validate_name,
)
from .objects import Files, ensure_directory

SCHEMA_VERSION = 7
schema = sa.MetaData()
NAME_KEYS = ("project", "namespace", "name")
# PostgreSQL JSON preserves the logged numeric representation (-0.0, 1e20).
# JSONB would rewrite these and invalidate replay hashes; queries cast explicitly.
JSON = sa.JSON()

meta = sa.Table(
    "cairn_meta",
    schema,
    sa.Column("key", sa.String, primary_key=True),
    sa.Column("value", JSON, nullable=False),
)
names = sa.Table(
    "cairn_names",
    schema,
    sa.Column("project", sa.String, primary_key=True),
    sa.Column("namespace", sa.String, primary_key=True),
    sa.Column("name", sa.String, primary_key=True),
    sa.Column("head", sa.String),
    sa.Column("next_version", sa.Integer, nullable=False, default=1),
)
artifacts = sa.Table(
    "cairn_artifacts",
    schema,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("project", sa.String, nullable=False),
    sa.Column("namespace", sa.String, nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("state", sa.String, nullable=False),
    sa.Column("metadata", JSON, nullable=False),
    sa.Column("annotations", JSON, nullable=False),
    sa.Column("annotations_etag", sa.String(64), nullable=False),
    sa.Column("cutoff", sa.BigInteger, nullable=False, default=0),
    sa.Column("publication_bytes", sa.BigInteger, nullable=False, default=0),
    sa.Column("intent", JSON),
    sa.Column("number", sa.Integer),
    sa.Column("manifest", JSON),
    sa.Column("created_at", sa.String, nullable=False),
    sa.Column("finished_at", sa.String),
    sa.Column("deleted_at", sa.String),
    sa.UniqueConstraint("project", "namespace", "name", "number"),
)
annotation_members = sa.Table(
    "cairn_annotation_members",
    schema,
    sa.Column("artifact_id", sa.ForeignKey(artifacts.c.id), primary_key=True),
    sa.Column("path_key", sa.String(64), primary_key=True),
    sa.Column("value_key", sa.String(64), primary_key=True),
)
sa.Index(
    "cairn_annotation_lookup",
    annotation_members.c.path_key,
    annotation_members.c.value_key,
    annotation_members.c.artifact_id,
)
sa.Index(
    "cairn_annotations_gin", sa.cast(artifacts.c.annotations, JSONB), postgresql_using="gin"
).ddl_if(dialect="postgresql")
writers = sa.Table(
    "cairn_writers",
    schema,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("artifact_id", sa.ForeignKey(artifacts.c.id), nullable=False),
    sa.Column("acknowledged", sa.BigInteger, nullable=False, default=0),
    sa.Column("closed", sa.Boolean, nullable=False, default=False),
    sa.Column("created_at", sa.String, nullable=False),
)
records = sa.Table(
    "cairn_records",
    schema,
    sa.Column("artifact_id", sa.ForeignKey(artifacts.c.id), primary_key=True),
    sa.Column("ordinal", sa.BigInteger, primary_key=True),
    sa.Column("writer_id", sa.ForeignKey(writers.c.id), nullable=False),
    sa.Column("sequence", sa.BigInteger, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("data", JSON, nullable=False),
    sa.Column("hash", sa.String, nullable=False),
    sa.Column("created_at", sa.String, nullable=False),
    sa.UniqueConstraint("writer_id", "sequence"),
)
aliases = sa.Table(
    "cairn_aliases",
    schema,
    sa.Column("project", sa.String, primary_key=True),
    sa.Column("namespace", sa.String, primary_key=True),
    sa.Column("name", sa.String, primary_key=True),
    sa.Column("alias", sa.String, primary_key=True),
    sa.Column("artifact_id", sa.ForeignKey(artifacts.c.id), nullable=False),
)
blobs = sa.Table(
    "cairn_blobs",
    schema,
    sa.Column("digest", sa.String, primary_key=True),
    sa.Column("size", sa.BigInteger, nullable=False),
    sa.Column("key", sa.String, nullable=False),
    sa.Column("state", sa.String, nullable=False),
    sa.Column("lease_until", sa.Float, nullable=False),
    sa.Column("created_at", sa.String, nullable=False),
)
blob_refs = sa.Table(
    "cairn_blob_refs",
    schema,
    sa.Column("owner", sa.String, primary_key=True),
    sa.Column("digest", sa.ForeignKey(blobs.c.digest), primary_key=True),
)
artifact_refs = sa.Table(
    "cairn_artifact_refs",
    schema,
    sa.Column("owner", sa.String, primary_key=True),
    sa.Column("artifact_id", sa.ForeignKey(artifacts.c.id), primary_key=True),
    sa.Column("role", sa.String, primary_key=True),
)
requests = sa.Table(
    "cairn_requests",
    schema,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("hash", sa.String, nullable=False),
    sa.Column("result", JSON),
)
# Mutable properties and comments; artifact_id NULL targets every version of the name.
annotations_table = sa.Table(
    "cairn_annotations",
    schema,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("project", sa.String, nullable=False),
    sa.Column("namespace", sa.String, nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("artifact_id", sa.ForeignKey(artifacts.c.id)),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("key", sa.String),
    sa.Column("value", JSON, nullable=False),
    sa.Column("author", sa.String),
    sa.Column("created_at", sa.String, nullable=False),
    sa.Column("updated_at", sa.String, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("resolved_at", sa.String),
    sa.Column("resolution", JSON),
)
for _label, _columns, _target in (
    (
        "cairn_exact_properties",
        ("artifact_id", "key"),
        annotations_table.c.artifact_id.is_not(None),
    ),
    ("cairn_name_properties", (*NAME_KEYS, "key"), annotations_table.c.artifact_id.is_(None)),
):
    _where = sa.and_(annotations_table.c.kind == "property", _target)
    sa.Index(
        _label,
        *(annotations_table.c[column] for column in _columns),
        unique=True,
        sqlite_where=_where,
        postgresql_where=_where,
    )
sa.Index("cairn_annotation_names", *(annotations_table.c[key] for key in NAME_KEYS))
sa.Index("cairn_annotation_targets", annotations_table.c.artifact_id, annotations_table.c.kind)
# Schema 6 comments, read only by the explicit migration.
legacy_comments = sa.Table(
    "cairn_comments",
    sa.MetaData(),
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("artifact_id", sa.String),
    sa.Column("anchor", JSON),
    sa.Column("body", sa.Text),
    sa.Column("created_at", sa.String),
)
sa.Index("cairn_discovery", artifacts.c.project, artifacts.c.namespace, artifacts.c.id)
sa.Index("cairn_record_kind", records.c.artifact_id, records.c.kind, records.c.ordinal)
sa.Index("cairn_incoming_artifacts", artifact_refs.c.artifact_id)
sa.Index("cairn_incoming_blobs", blob_refs.c.digest)


def _natural(left, right):
    """Compare names with runs of digits as numbers, so seed2 sorts before seed10."""

    def key(text):
        parts = re.split(r"(\d+)", text)
        return [int(part) if odd % 2 else part.casefold() for odd, part in enumerate(parts)], text

    return (key(left) > key(right)) - (key(left) < key(right))


def _row(result):
    value = result.mappings().first()
    return dict(value) if value is not None else None


def validate_annotations(value):
    if not isinstance(value, dict):
        raise ValidationError("Annotations and annotation patches must be JSON objects")
    if len(canonical(value)) > 1024 * 1024:
        raise CapacityError("Annotations must not exceed 1 MiB")
    members = queries.member_keys(value)
    if len(members) > 10000:
        raise CapacityError("Annotations must not exceed 10000 indexed array members")
    return members


def _merge_patch(previous, patch):
    result = dict(previous) if isinstance(previous, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict):
            result[key] = _merge_patch(result.get(key), value)
        else:
            result[key] = value
    return result


def _text(value, label, limit, *, optional=False):
    if value is None and optional:
        return
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValidationError(f"{label} must contain 1–{limit} characters")


def annotation_layers(conn, artifact):
    """Name-level and exact properties; exact keys override the name's in the projection."""
    result = {"name": {}, "artifact": {}}
    for owner, key, value in conn.execute(
        sa.select(
            annotations_table.c.artifact_id, annotations_table.c.key, annotations_table.c.value
        )
        .where(
            annotations_table.c.kind == "property",
            *(annotations_table.c[key] == artifact[key] for key in NAME_KEYS),
            sa.or_(
                annotations_table.c.artifact_id.is_(None),
                annotations_table.c.artifact_id == artifact["id"],
            ),
        )
        .order_by(annotations_table.c.key)
    ):
        result["artifact" if owner else "name"][key] = value
    return result


def effective_annotations(conn, artifact):
    layers = annotation_layers(conn, artifact)
    return {**layers["name"], **layers["artifact"]}


class Repository:
    def __init__(
        self, path=None, *, database_url=None, object_store=None, migrate=False, link_roots=()
    ):
        if path is not None and database_url is not None:
            raise ValidationError("Choose a local path or database URL")
        self.path = (
            Path(path or ".cairn/repo").expanduser().resolve() if database_url is None else None
        )
        if self.path:
            ensure_directory(self.path)
            database_url = "sqlite:///" + str(self.path / "metadata.sqlite3")
            object_store = object_store or Files(self.path / "objects")
        if object_store is None:
            raise ValidationError("An explicit database requires an object store")
        self.objects = object_store
        self.link_roots = [Path(root).resolve() for root in link_roots]
        self.engine = sa.create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False, "timeout": 30}
            if database_url.startswith("sqlite")
            else {},
        )
        self.dialect = self.engine.dialect.name
        if self.dialect not in {"sqlite", "postgresql"}:
            raise Unsupported("Supported databases are SQLite and PostgreSQL")
        if self.dialect == "sqlite":

            @sa.event.listens_for(self.engine, "connect")
            def initialize_sqlite(connection, _):
                connection.isolation_level = None
                connection.execute("PRAGMA busy_timeout=30000")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.create_collation("cairn_natural", _natural)

        with self._transaction(write=True) as conn:
            if self.dialect == "postgresql":
                conn.execute(sa.text("SELECT pg_advisory_xact_lock(731804171)"))
            existing = sa.inspect(conn).get_table_names()
            upgrade = False
            if meta.name in existing:
                version = conn.execute(
                    sa.select(meta.c.value).where(meta.c.key == "schema_version")
                ).scalar()
                upgrade = migrate and version == 6
                if version != SCHEMA_VERSION and not upgrade:
                    hint = (
                        "; export a backup, then run `cairn repo migrate`" if version == 6 else ""
                    )
                    raise Unsupported(
                        f"Repository schema {version!r}; this client requires {SCHEMA_VERSION}{hint}",
                        details={"schema_version": version},
                    )
                if migrate and not upgrade:
                    raise Conflict(f"Repository schema is already {version}")
            elif migrate:
                raise NotFound("No repository to migrate")
            elif any(name.startswith("cairn_") for name in existing):
                raise IntegrityError("Repository tables exist without schema metadata")
            schema.create_all(conn)
            self.natural = True
            if self.dialect == "postgresql":
                try:
                    with conn.begin_nested():
                        conn.execute(
                            sa.text(
                                "CREATE COLLATION IF NOT EXISTS cairn_natural "
                                "(provider = icu, locale = 'und-u-kn-true')"
                            )
                        )
                except sa.exc.DBAPIError:
                    # Without ICU, names sort by code point instead.
                    self.natural = False
            if upgrade:
                self._upgrade(conn)
            if not conn.execute(sa.select(meta.c.key).limit(1)).first():
                conn.execute(
                    meta.insert(),
                    [
                        {"key": "schema_version", "value": SCHEMA_VERSION},
                        {"key": "repository_id", "value": new_id("repo")},
                    ],
                )

    def _upgrade(self, conn):
        """Schema 6 to 7: inline annotations become exact properties and comments move along."""
        stamp, rows = now(), []
        columns = (
            artifacts.c.id,
            *(artifacts.c[key] for key in NAME_KEYS),
            artifacts.c.annotations,
        )
        stored = list(conn.execute(sa.select(*columns)).mappings())
        for artifact in stored:
            for key, value in artifact["annotations"].items():
                rows.append(
                    {
                        **{name: artifact[name] for name in NAME_KEYS},
                        "id": new_id("a"),
                        "artifact_id": artifact["id"],
                        "kind": "property",
                        "key": key,
                        "value": value,
                        "created_at": stamp,
                        "updated_at": stamp,
                        "revision": 1,
                    }
                )
        comments = conn.execute(
            sa.select(legacy_comments, *(artifacts.c[key] for key in NAME_KEYS)).join(
                artifacts, artifacts.c.id == legacy_comments.c.artifact_id
            )
        ).mappings()
        for comment in comments:
            rows.append(
                {
                    **{name: comment[name] for name in NAME_KEYS},
                    "id": comment["id"],
                    "artifact_id": comment["artifact_id"],
                    "kind": "comment",
                    "key": None,
                    "value": {"anchor": comment["anchor"], "body": comment["body"]},
                    "created_at": comment["created_at"],
                    "updated_at": comment["created_at"],
                    "revision": 1,
                }
            )
        if rows:
            conn.execute(annotations_table.insert(), rows)
        legacy_comments.drop(conn)
        for artifact in stored:
            if canonical(effective_annotations(conn, artifact)) != canonical(
                artifact["annotations"]
            ):
                raise IntegrityError("Migrated annotations differ", details={"id": artifact["id"]})
        conn.execute(
            meta.update().where(meta.c.key == "schema_version").values(value=SCHEMA_VERSION)
        )

    def close(self):
        self.engine.dispose()

    @contextmanager
    def _transaction(self, *, write=False):
        with self.engine.connect() as conn:
            if self.dialect == "sqlite":
                conn.exec_driver_sql("BEGIN IMMEDIATE" if write else "BEGIN")
            elif not write:
                conn = conn.execution_options(isolation_level="REPEATABLE READ")
                conn.begin()
            else:
                conn.begin()
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def info(self):
        with self._transaction() as conn:
            result = dict(conn.execute(sa.select(meta.c.key, meta.c.value)).all())
        return {
            **result,
            "protocol_version": PROTOCOL_VERSION,
            "envelope_version": 1,
            "limits": {
                "batch_entries": 10000,
                "batch_bytes": 4 * 1024 * 1024,
                "record_bytes": 1024 * 1024,
                "publication_collection_bytes": PUBLICATION_COLLECTION_LIMIT,
                "publication_value_bytes": PUBLICATION_VALUE_LIMIT,
                "publication_metadata_bytes": PUBLICATION_METADATA_LIMIT,
                "manifest_bytes": 8 * 1024 * 1024,
                "query_page": 10000,
            },
        }

    def _get(self, conn, table, identifier, *, lock=False):
        if not isinstance(identifier, str) or not identifier:
            raise ValidationError("Expected a nonempty identifier")
        statement = sa.select(table).where(table.c.id == identifier)
        if lock:
            statement = statement.with_for_update()
        result = _row(conn.execute(statement))
        if result is None:
            raise NotFound(f"Unknown {table.name.removeprefix('cairn_')}: {identifier}")
        return result

    def _once(self, conn, request_id, operation, payload, function):
        if request_id is not None and (
            not isinstance(request_id, str) or not request_id or len(request_id) > 128
        ):
            raise ValidationError("Request ID must be a string of 1–128 characters")
        key = request_id or new_id("request")
        fingerprint = digest({"operation": operation, "payload": payload})
        try:
            with conn.begin_nested():
                conn.execute(requests.insert().values(id=key, hash=fingerprint, result=None))
        except SQLIntegrityError:
            existing = _row(
                conn.execute(sa.select(requests).where(requests.c.id == key).with_for_update())
            )
            if existing is None or existing["hash"] != fingerprint:
                raise Conflict("Request ID was reused with different parameters")
            if existing["result"] is not None:
                return existing["result"]
        result = function()
        conn.execute(requests.update().where(requests.c.id == key).values(result=result))
        return result

    def _artifact(self, conn, identifier, *, lock=False, deleted=False):
        result = self._get(conn, artifacts, identifier, lock=lock)
        if result["deleted_at"] and not deleted:
            raise NotFound("Artifact has been deleted")
        return self._descriptors(conn, [result])[0]

    @staticmethod
    def _descriptors(conn, rows):
        return [{**dict(row), "entity": "artifact"} for row in rows]

    def _lock_name(self, conn, key):
        # Serializes property writes and starts per name; SQLite writers are already serial.
        if self.dialect == "postgresql":
            conn.execute(
                sa.text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": int(digest([key[name] for name in NAME_KEYS])[:15], 16)},
            )

    def _project(self, conn, artifact):
        """Store the effective annotations, their ETag and membership index on the artifact."""
        value = effective_annotations(conn, artifact)
        members = validate_annotations(value)
        etag = digest(value)
        conn.execute(
            artifacts.update()
            .where(artifacts.c.id == artifact["id"])
            .values(annotations=value, annotations_etag=etag)
        )
        conn.execute(
            annotation_members.delete().where(annotation_members.c.artifact_id == artifact["id"])
        )
        if members:
            conn.execute(
                annotation_members.insert(),
                [
                    {"artifact_id": artifact["id"], "path_key": path, "value_key": member}
                    for path, member in sorted(members)
                ],
            )
        return {**artifact, "annotations": value, "annotations_etag": etag}

    def annotate(
        self, artifact_id, patch, *, expected_etag=None, target="artifact", request_id=None
    ):
        validate_annotations(patch)
        if expected_etag is not None:
            validate_digest(expected_etag)
        if target not in ("artifact", "name"):
            raise ValidationError("Annotation target must be artifact or name")

        def update():
            self._lock_name(conn, self._artifact(conn, artifact_id))
            artifact = self._artifact(conn, artifact_id, lock=True)
            if expected_etag is not None and artifact["annotations_etag"] != expected_etag:
                raise Conflict("Annotations changed since they were read")
            current = annotation_layers(conn, artifact)[target]
            value, stamp = _merge_patch(current, patch), now()
            owner = artifact_id if target == "artifact" else None
            selected = [annotations_table.c.kind == "property"] + (
                [annotations_table.c.artifact_id == owner]
                if owner
                else [
                    annotations_table.c.artifact_id.is_(None),
                    *(annotations_table.c[key] == artifact[key] for key in NAME_KEYS),
                ]
            )
            for key in current.keys() - value.keys():
                conn.execute(
                    annotations_table.delete().where(*selected, annotations_table.c.key == key)
                )
            for key, item in value.items():
                if key not in current:
                    conn.execute(
                        annotations_table.insert().values(
                            **{name: artifact[name] for name in NAME_KEYS},
                            id=new_id("a"),
                            artifact_id=owner,
                            kind="property",
                            key=key,
                            value=item,
                            created_at=stamp,
                            updated_at=stamp,
                            revision=1,
                        )
                    )
                elif canonical(item) != canonical(current[key]):
                    conn.execute(
                        annotations_table.update()
                        .where(*selected, annotations_table.c.key == key)
                        .values(
                            value=item,
                            updated_at=stamp,
                            revision=annotations_table.c.revision + 1,
                        )
                    )
            if owner:
                return self._project(conn, artifact)
            family = conn.execute(
                sa.select(artifacts).where(self._name_condition(artifacts, artifact))
            ).mappings()
            projected = {
                row["id"]: self._project(conn, row) for row in self._descriptors(conn, family)
            }
            return projected[artifact_id]

        payload = [artifact_id, patch, expected_etag] + ([target] if target != "artifact" else [])
        with self._transaction(write=True) as conn:
            # Reject replay against deleted artifacts without acquiring their row
            # lock ahead of the receipt lock (finish uses receipt -> artifact).
            self._artifact(conn, artifact_id)
            return self._once(conn, request_id, "annotate", payload, update)

    def annotation_layers(self, artifact_id):
        with self._transaction() as conn:
            artifact = self._artifact(conn, artifact_id, deleted=True)
            return {
                **annotation_layers(conn, artifact),
                "annotations": artifact["annotations"],
                "annotations_etag": artifact["annotations_etag"],
            }

    @staticmethod
    def _name_condition(table, artifact):
        return sa.and_(*(table.c[key] == artifact[key] for key in ("project", "namespace", "name")))

    @staticmethod
    def _writer_dict(writer):
        return {**writer, "next_sequence": writer["acknowledged"] + 1}

    def start(self, project, namespace, name, *, request_id=None) -> StartResult:
        for item in (project, namespace, name):
            validate_name(item)
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", namespace):
            raise ValidationError("Namespaces must be lowercase identifiers")

        def create():
            key = {"project": project, "namespace": namespace, "name": name}
            self._lock_name(conn, key)
            if not conn.execute(sa.select(names).where(self._name_condition(names, key))).first():
                try:
                    with conn.begin_nested():
                        conn.execute(names.insert().values(**key, next_version=1))
                except SQLIntegrityError:
                    pass
            artifact = {
                "id": new_id(),
                **key,
                "state": "live",
                "metadata": {},
                "annotations": {},
                "annotations_etag": digest({}),
                "cutoff": 0,
                "publication_bytes": 0,
                "intent": None,
                "number": None,
                "manifest": None,
                "created_at": now(),
                "finished_at": None,
                "deleted_at": None,
            }
            conn.execute(artifacts.insert().values(**artifact))
            if annotation_layers(conn, artifact)["name"]:
                artifact = self._project(conn, artifact)
            return {
                "artifact": {**artifact, "entity": "artifact"},
                "writer": self._new_writer(conn, artifact["id"]),
            }

        with self._transaction(write=True) as conn:
            return self._once(conn, request_id, "start", [project, namespace, name], create)

    def _new_writer(self, conn, artifact_id):
        writer = {
            "id": new_id("w"),
            "artifact_id": artifact_id,
            "acknowledged": 0,
            "closed": False,
            "created_at": now(),
        }
        conn.execute(writers.insert().values(**writer))
        return self._writer_dict(writer)

    def register_writer(self, artifact_id, *, request_id=None) -> WriterDescriptor:
        def register():
            if self._artifact(conn, artifact_id, lock=True)["state"] != "live":
                raise Conflict("Artifact no longer accepts new writers")
            return self._new_writer(conn, artifact_id)

        with self._transaction(write=True) as conn:
            return self._once(conn, request_id, "register_writer", artifact_id, register)

    def writer(self, artifact_id, writer_id) -> WriterDescriptor:
        with self._transaction() as conn:
            self._artifact(conn, artifact_id)
            writer = self._get(conn, writers, writer_id)
            if writer["artifact_id"] != artifact_id:
                raise NotFound("Writer does not belong to this artifact")
            return self._writer_dict(writer)

    def _validate_dependencies(self, conn, values, owner=None):
        blob_list, ref_list = dependencies(values)
        for blob in sorted(blob_list, key=lambda x: x["digest"]):
            stored = _row(
                conn.execute(
                    sa.select(blobs).where(blobs.c.digest == blob["digest"]).with_for_update()
                )
            )
            if stored is None or stored["state"] != "ready" or stored["size"] != blob["size"]:
                raise IntegrityError(
                    "Referenced content has not been uploaded", details={"digest": blob["digest"]}
                )
        for reference in sorted(ref_list, key=lambda x: x["artifact_id"]):
            target = self._artifact(conn, reference["artifact_id"], lock=True, deleted=True)
            if target["state"] != "finished":
                raise ValidationError("Dependencies must reference finished artifacts")
        if owner:
            self._charge_publication(conn, owner, self._retain(conn, owner, blob_list, ref_list))
        return blob_list, ref_list

    @staticmethod
    def _charge_publication(conn, artifact_id, added_bytes):
        if not added_bytes:
            return
        used = conn.execute(
            sa.select(artifacts.c.publication_bytes).where(artifacts.c.id == artifact_id)
        ).scalar_one()
        if used + added_bytes > PUBLICATION_COLLECTION_LIMIT:
            raise CapacityError(
                "Artifact publication collections exceed their capacity; start a new artifact",
                details={
                    "used_bytes": used,
                    "added_bytes": added_bytes,
                    "limit_bytes": PUBLICATION_COLLECTION_LIMIT,
                },
            )
        if added_bytes:
            conn.execute(
                artifacts.update()
                .where(artifacts.c.id == artifact_id)
                .values(publication_bytes=used + added_bytes)
            )

    @staticmethod
    def _publication_inputs(artifact, value, metadata):
        frozen_metadata = {**artifact["metadata"], **(metadata or {})}
        if len(canonical(frozen_metadata)) > PUBLICATION_METADATA_LIMIT:
            raise CapacityError("Publication metadata exceeds 1 MiB")
        if len(canonical(value)) > PUBLICATION_VALUE_LIMIT:
            raise CapacityError("Publication value exceeds 1 MiB; store large values as blobs")
        return frozen_metadata

    @staticmethod
    def _retain(conn, owner, blob_list, ref_list):
        added_bytes = 0
        blob_sizes = {item["digest"]: item["size"] for item in blob_list}
        for table, entries in (
            (blob_refs, [{"owner": owner, "digest": item["digest"]} for item in blob_list]),
            (artifact_refs, [{"owner": owner, **item} for item in ref_list]),
        ):
            for entry in entries:
                condition = sa.and_(*(table.c[key] == value for key, value in entry.items()))
                if not conn.execute(sa.select(table).where(condition)).first():
                    conn.execute(table.insert().values(**entry))
                    descriptor = (
                        {"digest": entry["digest"], "size": blob_sizes[entry["digest"]]}
                        if table is blob_refs
                        else {"artifact_id": entry["artifact_id"], "role": entry["role"]}
                    )
                    added_bytes += len(canonical(descriptor)) + 1
        return added_bytes

    def append_batch(self, artifact_id, writer_id, entries):
        if not isinstance(entries, list):
            raise ValidationError("A batch must be a list")
        if len(entries) > 10000:
            raise CapacityError("A batch must be a list with at most 10000 entries")
        validated = []
        previous = None
        total_bytes = 0
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"sequence", "kind", "data"}:
                raise ValidationError("Entries require sequence, kind and data")
            sequence, kind, data = entry["sequence"], entry["kind"], entry["data"]
            if (
                type(sequence) is not int
                or sequence < 1
                or (previous is not None and sequence != previous + 1)
            ):
                raise ValidationError("Batch sequences must be positive and contiguous")
            previous = sequence
            if (
                not isinstance(kind, str)
                or kind not in {"record", "capture", "dependency", "metadata"}
                or not isinstance(data, dict)
            ):
                raise ValidationError("Invalid entry kind or data")
            encoded = canonical(entry)
            if len(encoded) > 1024 * 1024:
                raise CapacityError("Record exceeds 1 MiB; store large values as blobs")
            total_bytes += len(encoded)
            if total_bytes > 4 * 1024 * 1024:
                raise CapacityError("Batch exceeds 4 MiB; split it into smaller batches")
            if kind == "capture":
                if set(data) != {"name", "value"}:
                    raise ValidationError("Capture requires name and value")
                safe_path(data["name"])
            if kind == "dependency":
                if set(data) - {"artifact_id", "role"} or not isinstance(
                    data.get("artifact_id"), str
                ):
                    raise ValidationError("Dependency requires an exact artifact ID")
                if not isinstance(data.get("role", "input"), str):
                    raise ValidationError("Dependency role must be a string")
            validated.append((entry, hashlib.sha256(encoded).hexdigest()))
        with self._transaction(write=True) as conn:
            artifact = self._artifact(conn, artifact_id, lock=True)
            writer = self._get(conn, writers, writer_id, lock=True)
            if writer["artifact_id"] != artifact_id:
                raise NotFound("Writer does not belong to artifact")
            acknowledged, cutoff = writer["acknowledged"], artifact["cutoff"]
            additions = []
            metadata = dict(artifact["metadata"])
            duplicate_hashes = {}
            if entries and entries[0]["sequence"] <= acknowledged:
                duplicate_hashes = dict(
                    conn.execute(
                        sa.select(records.c.sequence, records.c.hash).where(
                            records.c.writer_id == writer_id,
                            records.c.sequence.between(
                                entries[0]["sequence"], min(acknowledged, entries[-1]["sequence"])
                            ),
                        )
                    ).all()
                )
            for entry, entry_hash in validated:
                sequence, kind, data = entry["sequence"], entry["kind"], entry["data"]
                if sequence <= acknowledged:
                    if duplicate_hashes.get(sequence) != entry_hash:
                        raise Conflict("Sequence was reused with different content")
                    continue
                if artifact["state"] not in {"live", "closing"} or writer["closed"]:
                    raise Conflict("Writer is closed")
                if sequence != acknowledged + 1:
                    raise Conflict("Producer sequence gap", details={"expected": acknowledged + 1})
                if kind == "metadata":
                    metadata.update(data)
                cutoff += 1
                acknowledged = sequence
                additions.append(
                    {
                        "artifact_id": artifact_id,
                        "ordinal": cutoff,
                        "writer_id": writer_id,
                        "sequence": sequence,
                        "kind": kind,
                        "data": data,
                        "hash": entry_hash,
                        "created_at": now(),
                    }
                )
            if additions:
                if len(canonical(metadata)) > PUBLICATION_METADATA_LIMIT:
                    raise CapacityError("Artifact metadata exceeds 1 MiB")
                if artifact["intent"]:
                    self._publication_inputs(
                        {**artifact, "metadata": metadata},
                        artifact["intent"]["value"],
                        artifact["intent"]["metadata"],
                    )
                # Charge only accepted additions, never retries. Replacement captures
                # count cumulatively: this bound needs no growing per-write scan.
                self._charge_publication(
                    conn,
                    artifact_id,
                    sum(
                        len(canonical({row["data"]["name"]: row["data"]["value"]}))
                        for row in additions
                        if row["kind"] == "capture"
                    ),
                )
                # Acquire each dependency lock once, in a batch-wide order, so
                # concurrent batches cannot deadlock by listing opposite orders.
                explicit_refs = [row["data"] for row in additions if row["kind"] == "dependency"]
                self._validate_dependencies(
                    conn,
                    [
                        [row["data"] for row in additions],
                        {
                            "$cairn": 1,
                            "version": 1,
                            "format": "dependencies",
                            "refs": explicit_refs,
                        },
                    ],
                    owner=artifact_id,
                )
                conn.execute(records.insert(), additions)
                conn.execute(
                    writers.update()
                    .where(writers.c.id == writer_id)
                    .values(acknowledged=acknowledged)
                )
                conn.execute(
                    artifacts.update()
                    .where(artifacts.c.id == artifact_id)
                    .values(cutoff=cutoff, metadata=metadata)
                )
            return {"acknowledged": acknowledged, "cutoff": cutoff}

    def close_writer(self, artifact_id, writer_id, final_sequence) -> WriterDescriptor:
        if type(final_sequence) is not int or final_sequence < 0:
            raise ValidationError("final_sequence must be nonnegative")
        with self._transaction(write=True) as conn:
            self._artifact(conn, artifact_id, lock=True)
            writer = self._get(conn, writers, writer_id, lock=True)
            if writer["artifact_id"] != artifact_id:
                raise NotFound("Writer does not belong to artifact")
            if writer["acknowledged"] != final_sequence:
                raise Pending(
                    "Writer has not acknowledged its final sequence",
                    details={
                        "acknowledged": writer["acknowledged"],
                        "final_sequence": final_sequence,
                    },
                )
            conn.execute(writers.update().where(writers.c.id == writer_id).values(closed=True))
            return self._writer_dict({**writer, "closed": True})

    def _seal(self, conn, artifact, value, metadata, outcome):
        frozen_metadata = self._publication_inputs(artifact, value, metadata)
        captures = {}
        for record in conn.execute(
            sa.select(records.c.data)
            .where(records.c.artifact_id == artifact["id"], records.c.kind == "capture")
            .order_by(records.c.ordinal)
        ):
            captures[record.data["name"]] = record.data["value"]
        refs = [
            dict(row)
            for row in conn.execute(
                sa.select(artifact_refs.c.artifact_id, artifact_refs.c.role).where(
                    artifact_refs.c.owner == artifact["id"]
                )
            ).mappings()
        ]
        contents = [
            dict(row)
            for row in conn.execute(
                sa.select(blobs.c.digest, blobs.c.size)
                .join(blob_refs, blobs.c.digest == blob_refs.c.digest)
                .where(blob_refs.c.owner == artifact["id"])
            ).mappings()
        ]
        manifest = {
            "metadata": frozen_metadata,
            "value": value,
            "captures": captures,
            "dependencies": refs,
            "blobs": contents,
            "outcome": outcome,
        }
        if len(canonical(manifest)) > 8 * 1024 * 1024:
            raise CapacityError("Artifact manifest exceeds 8 MiB")
        condition = self._name_condition(names, artifact)
        name = _row(conn.execute(sa.select(names).where(condition).with_for_update()))
        conn.execute(
            names.update()
            .where(condition)
            .values(head=artifact["id"], next_version=name["next_version"] + 1)
        )
        conn.execute(
            artifacts.update()
            .where(artifacts.c.id == artifact["id"])
            .values(
                state="finished",
                number=name["next_version"],
                manifest=manifest,
                metadata=frozen_metadata,
                finished_at=now(),
            )
        )
        return self._artifact(conn, artifact["id"])

    def finish(
        self, artifact_id, *, value=None, metadata=None, outcome="completed", request_id=None
    ) -> ArtifactDescriptor:
        if not isinstance(outcome, str) or outcome not in {
            "completed",
            "failed",
            "cancelled",
            "incomplete",
        }:
            raise ValidationError("Invalid completion outcome")
        if request_id is not None and (
            not isinstance(request_id, str) or not request_id or len(request_id) > 128
        ):
            raise ValidationError("Request ID must be a string of 1–128 characters")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValidationError("Metadata must be an object")
        intent = {"value": value, "metadata": metadata, "outcome": outcome}
        canonical(intent)
        request_id = request_id or new_id("request")
        payload = [artifact_id, intent]
        # Reserve the receipt before changing lifecycle state. Pending keeps a
        # null result so the same request can drain producers and complete later.
        with self._transaction(write=True) as conn:

            def prepare():
                artifact = self._artifact(conn, artifact_id, lock=True)
                if artifact["intent"] is not None and canonical(artifact["intent"]) != canonical(
                    intent
                ):
                    raise Conflict("A different completion intent already owns this artifact")
                if artifact["state"] != "finished" and artifact["intent"] is None:
                    self._publication_inputs(artifact, value, metadata)
                    self._validate_dependencies(conn, [value, metadata], owner=artifact_id)
                    conn.execute(
                        artifacts.update()
                        .where(artifacts.c.id == artifact_id)
                        .values(state="closing", intent=intent)
                    )

            self._once(conn, request_id, "finish", payload, prepare)
        with self._transaction(write=True) as conn:

            def complete():
                artifact = self._artifact(conn, artifact_id, lock=True)
                pending = list(
                    conn.execute(
                        sa.select(writers.c.id).where(
                            writers.c.artifact_id == artifact_id, writers.c.closed.is_(False)
                        )
                    ).scalars()
                )
                if pending:
                    raise Pending(
                        "Writers must close before completion", details={"writers": pending}
                    )
                return (
                    artifact
                    if artifact["state"] == "finished"
                    else self._seal(conn, artifact, value, metadata, outcome)
                )

            return self._once(conn, request_id, "finish", payload, complete)

    def _resolve(self, conn, ref, project):
        if not isinstance(ref, str) or not ref:
            raise ValidationError("Expected a nonempty reference")
        if re.fullmatch(r"[0-9a-f]{8,32}", ref):
            condition = (
                artifacts.c.id == ref
                if len(ref) == 32
                else sa.and_(artifacts.c.id >= ref, artifacts.c.id < ref + "g")
            )
            matches = list(conn.execute(sa.select(artifacts).where(condition).limit(2)).mappings())
            if len(matches) > 1:
                raise Conflict("Artifact ID prefix is ambiguous; use a longer ID")
            if matches:
                artifact = dict(matches[0])
                if artifact["deleted_at"] and artifact["state"] != "finished":
                    raise NotFound("Artifact has been deleted")
                return self._descriptors(conn, [artifact])[0]
        spelling, separator, selector = ref.rpartition("@")
        if not separator:
            spelling, selector = ref, "latest"
        if "/" not in spelling:
            raise NotFound(f"Unknown reference: {ref}")
        namespace, name = spelling.split("/", 1)
        key = {"project": project, "namespace": namespace, "name": name}
        if not conn.execute(sa.select(names).where(self._name_condition(names, key))).first():
            raise NotFound(f"Unknown artifact name: {spelling}")
        condition = self._name_condition(artifacts, key)
        if selector == "live":
            matches = list(
                conn.execute(
                    sa.select(artifacts).where(
                        condition,
                        artifacts.c.deleted_at.is_(None),
                        artifacts.c.state.in_(["live", "closing"]),
                    )
                ).mappings()
            )
            if not matches:
                raise NotFound("No live artifact")
            if len(matches) > 1:
                raise Conflict("Several artifacts are live; use an exact artifact ID")
            return self._descriptors(conn, matches)[0]
        if selector == "latest":
            identifier = conn.execute(
                sa.select(names.c.head).where(self._name_condition(names, key))
            ).scalar()
            if not identifier:
                raise NotPublished("Name has no finished artifact")
        elif re.fullmatch(r"v[1-9][0-9]*", selector):
            identifier = conn.execute(
                sa.select(artifacts.c.id).where(condition, artifacts.c.number == int(selector[1:]))
            ).scalar()
        else:
            identifier = conn.execute(
                sa.select(aliases.c.artifact_id).where(
                    self._name_condition(aliases, key), aliases.c.alias == selector
                )
            ).scalar()
        if not identifier:
            raise NotFound(f"Unknown version or alias: {selector}")
        return self._artifact(conn, identifier)

    def resolve(self, ref, *, project="default"):
        with self._transaction() as conn:
            return self._resolve(conn, ref, project)

    def resolve_many(self, refs, *, project="default"):
        if not isinstance(refs, list):
            raise ValidationError("References must be a list")
        if len(refs) > 1000:
            raise CapacityError("Resolve at most 1000 references at once")
        with self._transaction() as conn:
            return [self._resolve(conn, ref, project) for ref in refs]

    def list_artifacts(
        self,
        *,
        project=None,
        namespace=None,
        where=None,
        state=None,
        limit=100,
        cursor=None,
    ):
        self._limit(limit)
        statement = sa.select(artifacts).where(artifacts.c.deleted_at.is_(None))
        if project is not None:
            statement = statement.where(artifacts.c.project == project)
        if namespace is not None:
            statement = statement.where(artifacts.c.namespace == namespace)
        if state is not None and (
            not isinstance(state, str) or state not in {"live", "closing", "finished"}
        ):
            raise ValidationError("State must be live, closing, or finished")
        statement = statement.where(
            queries.discovery_predicate(
                where,
                artifacts.c.metadata,
                artifacts.c.annotations,
                artifacts.c.id,
                self.engine.dialect.name,
                annotation_members,
            )
        )
        if state is not None:
            statement = statement.where(artifacts.c.state == state)
        fingerprint = digest([project, namespace, where, state])
        if cursor:
            position = queries.decode_cursor(cursor)
            if position.get("query") != fingerprint or not isinstance(position.get("after"), str):
                raise ValidationError("Cursor belongs to another discovery query")
            statement = statement.where(artifacts.c.id > position["after"])
        with self._transaction() as conn:
            rows = list(
                conn.execute(statement.order_by(artifacts.c.id).limit(limit + 1)).mappings()
            )
            items = self._descriptors(conn, rows[:limit])
        next_cursor = (
            queries.encode_cursor({"query": fingerprint, "after": items[-1]["id"]})
            if len(rows) > limit
            else None
        )
        return {"items": items, "next_cursor": next_cursor}

    def _pattern(self, conn, pattern, project):
        """Conditions naming the finished artifacts a reference pattern matches."""
        if not isinstance(pattern, str) or not pattern:
            raise ValidationError("Expected a nonempty reference pattern")
        conditions = [
            artifacts.c.project == project,
            artifacts.c.state == "finished",
            artifacts.c.deleted_at.is_(None),
        ]
        spelling, separator, selector = pattern.rpartition("@")
        if not separator:
            spelling, selector = pattern, "latest"
        if "/" not in spelling and "*" not in spelling:
            try:
                return [*conditions, artifacts.c.id == self._resolve(conn, pattern, project)["id"]]
            except (NotFound, NotPublished):
                return [sa.false()]
        # * stays within one path segment and ** crosses them, as in shell globs.
        expression = "".join(
            ".*" if part == "**" else "[^/]*" if part == "*" else re.escape(part)
            for part in re.split(r"(\*\*|\*)", spelling)
        )
        spelled = artifacts.c.namespace + "/" + artifacts.c.name
        conditions.append(spelled.regexp_match(f"^{expression}$"))
        namespace = spelling.split("/", 1)[0]
        if "/" in spelling and "*" not in namespace:
            conditions.append(artifacts.c.namespace == namespace)
        same_name = [names.c[key] == artifacts.c[key] for key in NAME_KEYS]
        if selector == "latest":
            head = sa.select(names.c.head).where(*same_name).correlate(artifacts).scalar_subquery()
            conditions.append(artifacts.c.id == head)
        elif selector == "live":
            raise ValidationError("A search returns finished artifacts; @live never matches")
        elif re.fullmatch(r"v[1-9][0-9]*", selector):
            conditions.append(artifacts.c.number == int(selector[1:]))
        elif selector != "*":
            aliased = (
                sa.select(aliases.c.artifact_id)
                .where(*(aliases.c[key] == artifacts.c[key] for key in NAME_KEYS))
                .where(aliases.c.alias == selector)
                .correlate(artifacts)
            )
            conditions.append(artifacts.c.id.in_(aliased))
        return conditions

    def find(self, pattern, *, project="default", where=None, order=None, limit=100, cursor=None):
        """The finished artifacts a pattern names, filtered and sorted in SQL.

        The pattern addresses artifacts by name, like a reference.
        """
        self._limit(limit)
        if order is not None and not isinstance(order, str):
            raise ValidationError("order must be a field path, optionally prefixed with -")
        descending = order is not None and order.startswith("-")
        path = order[1:] if descending else order
        columns = {
            "value": artifacts.c.manifest,
            "metadata": artifacts.c.metadata,
            "annotations": artifacts.c.annotations,
        }
        dialect = self.engine.dialect.name
        filtered = queries.discovery_predicate(
            where,
            artifacts.c.metadata,
            artifacts.c.annotations,
            artifacts.c.id,
            dialect,
            annotation_members,
            manifest=artifacts.c.manifest,
        )
        sorting = (
            [*queries.sort_keys(path, columns, dialect, descending=descending), artifacts.c.id]
            if path is not None
            else [
                artifacts.c.namespace,
                artifacts.c.name.collate("cairn_natural") if self.natural else artifacts.c.name,
                artifacts.c.number,
            ]
        )
        fingerprint = digest([pattern, project, where, order])
        offset = 0
        if cursor:
            position = queries.decode_cursor(cursor)
            if position.get("query") != fingerprint or type(position.get("offset")) is not int:
                raise ValidationError("Cursor belongs to another search")
            offset = position["offset"]
        with self._transaction() as conn:
            matching = self._pattern(conn, pattern, project)
            rows = list(
                conn.execute(
                    sa.select(artifacts)
                    .where(*matching, filtered)
                    .order_by(*sorting)
                    .offset(offset)
                    .limit(limit + 1)
                ).mappings()
            )
            items = self._descriptors(conn, rows[:limit])
        next_cursor = (
            queries.encode_cursor({"query": fingerprint, "offset": offset + limit})
            if len(rows) > limit
            else None
        )
        return {"items": items, "next_cursor": next_cursor}

    def versions(self, ref, *, project="default"):
        with self._transaction() as conn:
            if "/" in ref:
                spelling = ref.split("@", 1)[0]
                namespace, name = spelling.split("/", 1)
                key = {"project": project, "namespace": namespace, "name": name}
            else:
                key = self._resolve(conn, ref, project)
            return self._descriptors(
                conn,
                conn.execute(
                    sa.select(artifacts)
                    .where(
                        self._name_condition(artifacts, key),
                        artifacts.c.state == "finished",
                        artifacts.c.deleted_at.is_(None),
                    )
                    .order_by(artifacts.c.number)
                ).mappings(),
            )

    def set_alias(self, artifact_id, alias, *, expected_id=None, request_id=None):
        if expected_id is not None and not isinstance(expected_id, str):
            raise ValidationError("Expected artifact must be an ID or null")
        validate_name(alias)
        if alias == "live" or re.fullmatch(r"v[0-9]+", alias):
            raise ValidationError("Reserved alias")

        def set_value():
            artifact = self._artifact(conn, artifact_id, lock=True)
            if artifact["state"] != "finished":
                raise ValidationError("Aliases require a finished artifact")
            key = {k: artifact[k] for k in ("project", "namespace", "name")}
            name = _row(
                conn.execute(
                    sa.select(names).where(self._name_condition(names, key)).with_for_update()
                )
            )
            condition = sa.and_(self._name_condition(aliases, key), aliases.c.alias == alias)
            previous = (
                name["head"]
                if alias == "latest"
                else conn.execute(sa.select(aliases.c.artifact_id).where(condition)).scalar()
            )
            if previous != expected_id:
                raise Conflict("Alias changed", details={"artifact_id": previous})
            if alias == "latest":
                conn.execute(
                    names.update().where(self._name_condition(names, key)).values(head=artifact_id)
                )
            elif previous:
                conn.execute(aliases.update().where(condition).values(artifact_id=artifact_id))
            else:
                conn.execute(aliases.insert().values(**key, alias=alias, artifact_id=artifact_id))
            return {"alias": alias, "artifact_id": artifact_id}

        with self._transaction(write=True) as conn:
            return self._once(
                conn, request_id, "set_alias", [artifact_id, alias, expected_id], set_value
            )

    @staticmethod
    def _limit(limit):
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValidationError("Page size must be between 1 and 10000")

    def query(
        self, ref, *, project="default", where=None, fields=None, limit=100, cursor=None, kind=None
    ) -> QueryPage:
        self._limit(limit)
        if kind is not None and (
            not isinstance(kind, str) or kind not in {"record", "capture", "dependency", "metadata"}
        ):
            raise ValidationError("Unknown record kind")
        if fields is not None:
            if not isinstance(fields, list) or len(fields) > 100:
                raise ValidationError("Projection requires at most 100 field paths")
            for field in fields:
                queries.parts(field)
        fingerprint = queries.fingerprint(where, fields, kind)
        with self._transaction() as conn:
            descriptor = self._resolve(conn, ref, project)
            artifact_id = descriptor["id"]
            cutoff, after = descriptor["cutoff"], 0
            if cursor:
                position = queries.decode_cursor(cursor)
                if (
                    position.get("query") != fingerprint
                    or position.get("ref") != ref
                    or position.get("project") != project
                    or position.get("artifact") != artifact_id
                ):
                    raise Conflict("Reference or query changed since the cursor was created")
                cutoff, after = position.get("cutoff"), position.get("after")
                if (
                    type(cutoff) is not int
                    or type(after) is not int
                    or not 0 <= after <= cutoff <= descriptor["cutoff"]
                ):
                    raise ValidationError("Invalid cursor cutoff")
            statement = sa.select(records).where(
                records.c.artifact_id == artifact_id,
                records.c.ordinal > after,
                records.c.ordinal <= cutoff,
                queries.predicate(where, records.c.data, self.dialect),
            )
            if kind:
                statement = statement.where(records.c.kind == kind)
            rows = list(
                conn.execute(statement.order_by(records.c.ordinal).limit(limit + 1)).mappings()
            )
            items = []
            for row in rows[:limit]:
                item = dict(row)
                item.pop("hash")
                if fields is not None:
                    item["data"] = {
                        field: value
                        for field in fields
                        for present, value in [queries.extract(item["data"], field)]
                        if present
                    }
                items.append(item)
            next_cursor = (
                queries.encode_cursor(
                    {
                        "query": fingerprint,
                        "ref": ref,
                        "project": project,
                        "artifact": artifact_id,
                        "cutoff": cutoff,
                        "after": items[-1]["ordinal"],
                    }
                )
                if len(rows) > limit
                else None
            )
            return {
                "items": items,
                "next_cursor": next_cursor,
                "cutoff": cutoff,
                "artifact_id": artifact_id,
            }

    def _reserve_blob(self, sha, length, key):
        """Return True when the content is stored, else reserve it for this upload."""
        with self._transaction(write=True) as conn:
            existing = _row(
                conn.execute(sa.select(blobs).where(blobs.c.digest == sha).with_for_update())
            )
            if existing and existing["state"] == "ready":
                if existing["size"] != length:
                    raise IntegrityError("Conflicting digest length")
                conn.execute(
                    blobs.update()
                    .where(blobs.c.digest == sha)
                    .values(lease_until=time.time() + 3600)
                )
                return True
            if existing and existing["lease_until"] > time.time():
                raise Pending("Another upload or cleanup owns this content")
            values = {
                "digest": sha,
                "size": length,
                "key": key,
                "state": "uploading",
                "lease_until": time.time() + 3600,
                "created_at": now(),
            }
            if existing:
                conn.execute(blobs.update().where(blobs.c.digest == sha).values(**values))
            else:
                try:
                    with conn.begin_nested():
                        conn.execute(blobs.insert().values(**values))
                except SQLIntegrityError as exc:
                    raise Pending("Another upload reserved this content") from exc
        return False

    def put_blob(self, source, *, digest=None, size=None):
        if digest is not None:
            validate_digest(digest)
        if size is not None and (type(size) is not int or size < 0):
            raise ValidationError("Invalid blob length")
        opened = None
        if isinstance(source, (str, os.PathLike)):
            opened = Path(source).open("rb")  # noqa: SIM115 — closed by the owning finally block
            source = opened
        elif isinstance(source, (bytes, bytearray, memoryview)):
            source = io.BytesIO(source)
        if not hasattr(source, "read"):
            raise ValidationError("Blob source must be bytes, a path, or a binary stream")
        try:
            with tempfile.TemporaryFile("w+b") as stage:
                hasher, length = hashlib.sha256(), 0
                while chunk := source.read(1024 * 1024):
                    if not isinstance(chunk, bytes):
                        raise ValidationError("Blob stream must produce bytes")
                    hasher.update(chunk)
                    length += len(chunk)
                    stage.write(chunk)
                sha = hasher.hexdigest()
                if (digest is not None and digest != sha) or (size is not None and size != length):
                    raise IntegrityError("Blob digest or length does not match")
                key = f"{sha[:2]}/{sha}/{new_id('o')}"
                deadline = time.monotonic() + 10
                while True:
                    try:
                        ready = self._reserve_blob(sha, length, key)
                        break
                    except Pending:
                        # Concurrent writers often store identical content, such as provenance.
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(0.05)
                if ready:
                    return {"digest": sha, "size": length}
                stage.seek(0)
                self.objects.put(key, stage, digest=sha, size=length)
                with self._transaction(write=True) as conn:
                    stored = _row(
                        conn.execute(
                            sa.select(blobs).where(blobs.c.digest == sha).with_for_update()
                        )
                    )
                    if (
                        not stored
                        or stored["key"] != key
                        or stored["state"] != "uploading"
                        or stored["lease_until"] <= time.time()
                    ):
                        raise Conflict("Upload reservation expired; retry the upload")
                    conn.execute(
                        blobs.update()
                        .where(blobs.c.digest == sha)
                        .values(state="ready", lease_until=time.time() + 3600)
                    )
                return {"digest": sha, "size": length}
        finally:
            if opened:
                opened.close()

    def has_blobs(self, digests):
        if not isinstance(digests, list):
            raise ValidationError("Digests must be a list")
        if len(digests) > 10000:
            raise CapacityError("Check at most 10000 digests")
        for sha in digests:
            validate_digest(sha)
        with self._transaction() as conn:
            ready = set(
                conn.execute(
                    sa.select(blobs.c.digest).where(
                        blobs.c.digest.in_(digests), blobs.c.state == "ready"
                    )
                ).scalars()
            )
        return {sha: sha in ready for sha in digests}

    def link_blob(self, path):
        """Register a file this repository can read as content, without copying it.

        Only files under link_roots are linked; their bytes stay where they are.
        """
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValidationError("Link an absolute file path")
        resolved = Path(path).resolve()
        if not any(resolved.is_relative_to(root) for root in self.link_roots):
            raise Unsupported(f"This repository does not link files outside its link roots: {path}")
        if not resolved.is_file():
            raise ValidationError(f"Only regular files can be linked: {path}")
        before = resolved.stat()
        hasher = hashlib.sha256()
        with resolved.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
        after = resolved.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise Conflict(f"File changed while it was linked: {path}")
        sha, length = hasher.hexdigest(), after.st_size
        with self._transaction(write=True) as conn:
            existing = _row(
                conn.execute(sa.select(blobs).where(blobs.c.digest == sha).with_for_update())
            )
            if existing and existing["state"] == "ready":
                return {"digest": sha, "size": length}
            if existing and existing["lease_until"] > time.time():
                raise Pending("Another upload or cleanup owns this content")
            values = {
                "digest": sha,
                "size": length,
                "key": f"link:{after.st_mtime_ns}:{resolved}",
                "state": "ready",
                "lease_until": time.time() + 3600,
                "created_at": now(),
            }
            if existing:
                conn.execute(blobs.update().where(blobs.c.digest == sha).values(**values))
            else:
                conn.execute(blobs.insert().values(**values))
        return {"digest": sha, "size": length}

    def linked_path(self, digest):
        """The original file of linked content, or None for content stored in the repository."""
        with self._transaction() as conn:
            stored = _row(conn.execute(sa.select(blobs).where(blobs.c.digest == digest)))
        if stored is None or stored["state"] != "ready" or not stored["key"].startswith("link:"):
            return None
        return self._linked(stored)

    @staticmethod
    def _linked(stored):
        _, modified, path = stored["key"].split(":", 2)
        try:
            info = Path(path).stat()
        except FileNotFoundError as exc:
            raise NotFound(f"Linked file is missing: {path}") from exc
        if (info.st_size, info.st_mtime_ns) != (stored["size"], int(modified)):
            raise IntegrityError(f"Linked file changed since it was linked: {path}")
        return Path(path)

    def delete_object(self, key):
        """Delete stored bytes; linked files belong to their owner and stay."""
        if not key.startswith("link:"):
            self.objects.delete(key)

    @contextmanager
    def open_blob(self, digest):
        validate_digest(digest)
        # Opening is a bounded lease; retained artifacts provide long-lived ownership.
        with self._transaction(write=True) as conn:
            stored = _row(
                conn.execute(sa.select(blobs).where(blobs.c.digest == digest).with_for_update())
            )
            if stored is None or stored["state"] != "ready":
                raise NotFound("Content is unavailable")
            conn.execute(
                blobs.update()
                .where(blobs.c.digest == digest)
                .values(lease_until=time.time() + 3600)
            )
        if stored["key"].startswith("link:"):
            with self._linked(stored).open("rb") as stream:
                yield stream
        else:
            with self.objects.open(stored["key"]) as stream:
                yield stream

    def delete(self, artifact_id, *, request_id=None):
        def remove():
            artifact = self._artifact(conn, artifact_id, lock=True, deleted=True)
            fallback = conn.execute(
                sa.select(artifacts.c.id)
                .where(
                    self._name_condition(artifacts, artifact),
                    artifacts.c.id != artifact_id,
                    artifacts.c.state == "finished",
                    artifacts.c.deleted_at.is_(None),
                )
                .order_by(artifacts.c.number.desc())
                .limit(1)
            ).scalar()
            conn.execute(
                names.update()
                .where(self._name_condition(names, artifact), names.c.head == artifact_id)
                .values(head=fallback)
            )
            conn.execute(aliases.delete().where(aliases.c.artifact_id == artifact_id))
            if not artifact["deleted_at"]:
                conn.execute(
                    artifacts.update().where(artifacts.c.id == artifact_id).values(deleted_at=now())
                )
            retained_by = conn.execute(
                sa.select(artifact_refs.c.owner)
                .join(artifacts, artifacts.c.id == artifact_refs.c.owner)
                .where(
                    artifact_refs.c.artifact_id == artifact_id,
                    artifact_refs.c.owner != artifact_id,
                    artifacts.c.deleted_at.is_(None),
                )
                .distinct()
                .order_by(artifact_refs.c.owner)
            ).scalars()
            return {"id": artifact_id, "deleted": True, "retained_by": list(retained_by)}

        with self._transaction(write=True) as conn:
            return self._once(conn, request_id, "delete", artifact_id, remove)

    @staticmethod
    def _comments(conn, *conditions, lock=False):
        statement = (
            sa.select(
                annotations_table,
                artifacts.c.number,
                artifacts.c.deleted_at.label("artifact_deleted_at"),
            )
            .join(artifacts, artifacts.c.id == annotations_table.c.artifact_id)
            .where(annotations_table.c.kind == "comment", *conditions)
            .order_by(annotations_table.c.created_at, annotations_table.c.id)
        )
        if lock:
            statement = statement.with_for_update(of=annotations_table)
        comments = []
        for row in conn.execute(statement).mappings():
            row = dict(row)
            value = row.pop("value")
            del row["kind"], row["key"]
            row["artifact_deleted"] = row.pop("artifact_deleted_at") is not None
            comments.append({**row, **value})
        return comments

    def _comment(self, conn, comment_id):
        if not isinstance(comment_id, str) or not comment_id:
            raise ValidationError("Expected a nonempty identifier")
        found = self._comments(conn, annotations_table.c.id == comment_id, lock=True)
        if not found:
            raise NotFound(f"Unknown comment: {comment_id}")
        return found[0]

    def add_comment(self, artifact_id, anchor, body, *, author=None, request_id=None):
        if not isinstance(anchor, dict) or len(canonical(anchor)) > 65536:
            raise ValidationError("Comment anchor must be a bounded object")
        _text(body, "Comment body", 65536)
        _text(author, "Comment author", 256, optional=True)

        def add():
            target = self._artifact(conn, artifact_id)
            if target["state"] != "finished":
                raise ValidationError("Comments require a finished artifact")
            identifier, stamp = new_id("c"), now()
            conn.execute(
                annotations_table.insert().values(
                    **{key: target[key] for key in NAME_KEYS},
                    id=identifier,
                    artifact_id=artifact_id,
                    kind="comment",
                    value={"anchor": anchor, "body": body},
                    author=author,
                    created_at=stamp,
                    updated_at=stamp,
                    revision=1,
                )
            )
            return self._comment(conn, identifier)

        payload = [artifact_id, anchor, body] + ([author] if author is not None else [])
        with self._transaction(write=True) as conn:
            return self._once(conn, request_id, "add_comment", payload, add)

    def comments(self, artifact_id, *, scope="artifact", include_resolved=True):
        if scope not in ("artifact", "name"):
            raise ValidationError("Comment scope must be artifact or name")
        with self._transaction() as conn:
            target = self._artifact(conn, artifact_id, deleted=True)
            if target["state"] != "finished":
                raise ValidationError("Comments require a finished artifact")
            selected = (
                [annotations_table.c.artifact_id == artifact_id]
                if scope == "artifact"
                else [annotations_table.c[key] == target[key] for key in NAME_KEYS]
            )
            if not include_resolved:
                selected.append(annotations_table.c.resolved_at.is_(None))
            return self._comments(conn, *selected)

    def _change_comment(self, comment_id, request_id, operation, payload, change):
        def apply():
            comment = self._comment(conn, comment_id)
            values = change(comment)
            conn.execute(
                annotations_table.update()
                .where(annotations_table.c.id == comment_id)
                .values(**values, updated_at=now(), revision=comment["revision"] + 1)
            )
            return self._comment(conn, comment_id)

        with self._transaction(write=True) as conn:
            return self._once(conn, request_id, operation, [comment_id, *payload], apply)

    def update_comment(self, comment_id, body, *, expected_revision=None, request_id=None):
        _text(body, "Comment body", 65536)
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision < 1
        ):
            raise ValidationError("Expected revision must be a positive integer")

        def change(comment):
            if expected_revision is not None and comment["revision"] != expected_revision:
                raise Conflict("Comment changed since it was read")
            return {"value": {"anchor": comment["anchor"], "body": body}}

        payload = [body, expected_revision]
        return self._change_comment(comment_id, request_id, "update_comment", payload, change)

    def resolve_comment(self, comment_id, *, resolved=True, note=None, by=None, request_id=None):
        if type(resolved) is not bool:
            raise ValidationError("resolved must be a boolean")
        _text(note, "Resolution note", 65536, optional=True)
        _text(by, "Resolver", 256, optional=True)
        if not resolved and (note is not None or by is not None):
            raise ValidationError("Reopening takes no note")

        def change(comment):
            if not resolved:
                return {"resolved_at": None, "resolution": None}
            return {"resolved_at": now(), "resolution": {"note": note, "by": by}}

        payload = [resolved, note, by]
        return self._change_comment(comment_id, request_id, "resolve_comment", payload, change)

    def delete_comment(self, comment_id, *, request_id=None):
        def remove():
            self._comment(conn, comment_id)
            conn.execute(annotations_table.delete().where(annotations_table.c.id == comment_id))
            return {"id": comment_id, "deleted": True}

        with self._transaction(write=True) as conn:
            return self._once(conn, request_id, "delete_comment", comment_id, remove)

    def collect_garbage(self, *, grace_seconds=3600, dry_run=True):
        from .ops import collect_garbage

        return collect_garbage(self, grace_seconds=grace_seconds, dry_run=dry_run)

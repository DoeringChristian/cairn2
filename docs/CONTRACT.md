# Repository contract

`cairn/contract.py` declares `RepositoryProtocol` and JSON result shapes. The direct
repository and HTTP adapter implement the same methods. Protocol version **4**,
repository schema **6**, durable outbox schema **3**. Earlier prototype schemas
are rejected; this change does not silently convert existing data.

## One artifact lifecycle

An artifact has one permanent hexadecimal ID. It starts live, accepts records,
metadata and files, then finishes and becomes immutable under that same ID.
There are no revision objects or separately identified version objects.
`closing` is an internal draining state: existing producers may finish delivering
accepted work, but new producers cannot join.

Every start creates a new artifact, even under an existing name. An identical
`request_id` retries that creation instead of creating another artifact. A name
is scoped by `(project, namespace, name)`. Namespaces are arbitrary conventions:
`runs`, `datasets`, `reports`, and `viewers` have no special backend behavior.

On finish, the artifact receives the next `vN` label for its name and becomes
that name's `latest`. Concurrent finishes receive distinct numbers; the last
finish to commit becomes latest. Neither relabeling nor starting another artifact
changes an earlier artifact's data.

References accepted by `resolve` and `query`:

- Full artifact ID, or an unambiguous hexadecimal prefix of at least 8 characters.
- `runs/training@v3` or `runs/training@latest` (the selector may be omitted).
- `runs/training@best`, for an explicitly assigned alias.
- `runs/training@live`, only when exactly one unfinished artifact has that name.

IDs are random identifiers, not hashes of artifact content. A direct ID resolves
the same artifact before and after finish. Version labels and aliases resolve
to that ID. Dependencies store full IDs of finished artifacts.

## Operations

All results are JSON dictionaries/lists except streamed blob reads. Times are
UTC ISO strings; errors have a stable code, message and details.

| Method | Result and behavior |
| --- | --- |
| `info()` | Repository identity, schema/protocol versions and supported limits. |
| `start(project, namespace, name, *, request_id=None)` | `{artifact, writer}`; minimal identity creation, no configuration parameter. |
| `register_writer(artifact_id, *, request_id=None)` | Another producer handle for a live artifact. |
| `writer(artifact_id, writer_id)` | Producer acknowledgement, next sequence and closed state. |
| `append_batch(artifact_id, writer_id, entries)` | `{acknowledged, cutoff}`; atomic, contiguous producer sequences; exact retry accepted, changed content rejected. |
| `close_writer(artifact_id, writer_id, final_sequence)` | Closes producer only after its final sequence is acknowledged. |
| `finish(artifact_id, *, value=None, metadata=None, outcome='completed', request_id=None)` | The finished artifact descriptor, with the original ID. Pending while producers remain open. One exact persisted finish intent; retries cannot change it. |
| `resolve(ref, *, project='default')` | Artifact descriptor, live or finished. |
| `resolve_many(refs, *, project='default')` | Descriptors in input order, from one read transaction. |
| `list_artifacts(*, project=None, namespace=None, where=None, state=None, limit=100, cursor=None)` | `{items, next_cursor}`; each item is an artifact, not a name-group object. |
| `annotate(artifact_id, patch, *, expected_etag=None, request_id=None)` | Atomic JSON Merge Patch with optional concurrency check; returns the artifact descriptor. |
| `versions(ref, *, project='default')` | Finished artifacts sharing the referenced name, ordered by numeric label. |
| `set_alias(artifact_id, alias, *, expected_id=None, request_id=None)` | Assigns an alias within the artifact's name; compare-and-swap against its previous target. `latest` is allowed; `live` and `vN` are reserved. |
| `query(ref, *, project='default', where=None, fields=None, limit=100, cursor=None, kind=None)` | `{items, next_cursor, cutoff, artifact_id}`; cursor pins artifact and acknowledged cutoff. |
| `put_blob(source, *, digest=None, size=None)` | Verified immutable content descriptor `{digest, size}`. |
| `has_blobs(digests)` | Mapping of digest to availability. |
| `open_blob(digest)` | Binary stream context manager, caller closes it. |
| `delete(artifact_id, *, request_id=None)` | Soft deletion; retained finished artifact IDs remain readable for provenance. |
| `add_comment(artifact_id, anchor, body, *, request_id=None)` | Comment on a finished artifact; anchor is structured source location data. |
| `comments(artifact_id)` / `delete_comment(comment_id)` | Read/delete annotations without changing captured artifact content. |

There is no snapshot operation. A separate finished checkpoint must be a separate
artifact. Writer handles and sequence numbers exist only for reliable delivery.

## Records and values

Entries are `{sequence, kind, data}`. Kinds are `record`, `metadata`, `capture`,
and `dependency`. Record data is an arbitrary JSON object; `step` is optional and
has no special storage semantics. Metadata shallow-merges. Captures are
`{name, value}`; dependencies are `{artifact_id, role}`. The repository assigns
one monotonically increasing ordinal across all producers of an artifact.

Queries use JSON-pointer field paths and structured predicates:
`{path:'/loss', op:'lt', value:0.1}`, or `{and:[...]}` / `{or:[...]}`.
Missing and type-incompatible values do not match comparisons. Record pages
are in ordinal order. Live queries freeze the first page's cutoff.

Typed values use `{$cairn:1, format, version:1, data, blobs, refs}`. Here `version`
is a codec-format version, not an artifact identity. Blob descriptors contain
`digest`, `size`, and optional `name`/`media_type`; refs contain `artifact_id` and
optional `role`. Only finished artifact dependencies are accepted. No arbitrary
pickle deserialization is performed.

A finished artifact's manifest contains `metadata`, `value`, `captures`, `blobs`,
`dependencies`, and `outcome`. Its data and records cannot change after finish.
The artifact descriptor also carries `number`, timestamps, cutoff and state.

## Capacity and reliable publication

Individual entries are limited to 1 MiB; batches to 4 MiB and 10,000 entries.
Final merged metadata and final value are each limited to 1 MiB. Large payloads
belong in blobs. Captures plus unique retained blob/dependency descriptors have
a conservative cumulative 6 MiB minus 1 KiB budget; capture replacements count
cumulatively. Blob contents and ordinary scalar record volume do not consume
that collection budget. The artifact's `publication_bytes` reports usage.

Capacity is checked before batch acknowledgement and before accepting finish
intent. Rejected batches do not advance records, producer sequences or budgets.
Definitive rejected finishes can be corrected; uncertain deliveries preserve
exact retry intent. The 8 MiB final manifest bound is a defensive check.

## HTTP, SDK and reports

Routes are named REST resources: `POST /v1/artifacts`, artifact-scoped writers,
entries, finalization, aliases and comments; `POST /v1/resolutions`,
`POST /v1/records/query`, and `GET /v1/history?ref=...&project=...`.
Blob uploads/downloads stream bytes. There are no revision, snapshot or separate
version resources, and no server report execution/editing routes.

`Client(path)` calls SQLite/files directly. `Client(url)` calls the selected
server. Remote writing uses a durable outbox; read-only clients never start or
resume delivery. `flush()` waits for acknowledgement; reads never merge queued
writes into server results.

`Client.start(namespace, name)` returns a writable `Artifact`; `Client.run()` and
`cairn.init()` add config and client-captured provenance through ordinary metadata
and captures. `finish()` returns the finished descriptor; `.id` stays unchanged.
`Client.resolve()` returns a `Reader` only for a finished artifact. `use()` records
a dependency on that exact ID. No revision ID or snapshot API is exposed.

Reports execute QMD locally, pin declared finished artifact IDs, capture source,
outputs and package information, then upload an ordinary report artifact. The
supplied report reader scopes reads to those declared inputs; independent Python
clients remain independent. Reports are not a Python security sandbox.

Repository export/import preserves artifact IDs, labels, records and retained
bytes. Imported producer handles are closed; live artifacts can register a new
producer. Imports never silently merge conflicting evidence. Integrity checks
reconstruct records, manifests and capacity accounting. GC and purge respect
retention and active transfer leases.

## Discovery and mutable annotations

Artifact descriptors include `annotations` (a JSON object) and `annotations_etag`.
Captured metadata/records/files remain immutable after finish; annotations can be
edited on live or finished artifacts. Tags, titles and review status are ordinary
annotation fields, not backend-specific exceptions. Deleted artifacts reject edits.

`annotate` accepts a top-level object using JSON Merge Patch semantics: nested
objects merge, arrays replace and null removes a key. `expected_etag` compares with
the current annotation ETag under the artifact lock. A stale ETag raises Conflict;
a missing ETag allows an atomic patch against the latest state. `request_id` provides
exact retry receipts. No new artifact identity or version label is created.
Annotation objects are capped at 1 MiB and their scalar array membership index at
10,000 entries. Updates, ETags and index rows commit or roll back together.

Discovery predicates use explicit roots `/metadata/...` and `/annotations/...`.
`contains` tests scalar array membership with JSON type semantics (booleans differ
from numbers; numerically equal integers/floats match). It works on arbitrary
array fields and on record queries. It is not substring or object containment.
SQLite uses a derived annotation-member index keyed by path/value; PostgreSQL
uses a GIN index for eligible annotation containment predicates. Other metadata
and scalar predicates use the common query compiler and may require specialized
indexes for large workloads. Filters execute in SQL before pagination, never in
the Python client. Cursors bind project, namespace, state and predicate.

HTTP annotation edits use `PATCH /v1/artifacts/{id}/annotations` with
`{patch, expected_etag, request_id}`. Discovery passes JSON `where` and optional
`state` to `GET /v1/artifacts`. Python exposes `Client.annotate` and `Client.find`;
CLI exposes `artifact annotate` and `artifact list --where`. The dedicated tag
mutation/filter API has been removed. There is no UI implementation in this change.

Export/import preserves annotations and validates their ETags and membership
index against stored JSON. Purge removes the derived index. Annotation writes are
synchronous and do not enter a writer's record-upload journal.

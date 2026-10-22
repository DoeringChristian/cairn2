# Repository contract

`cairn/contract.py` declares `RepositoryProtocol` and JSON result shapes. The direct
repository and HTTP adapter implement the same methods. Protocol version **4**,
repository schema **7**, durable outbox schema **3**. Other schemas are rejected;
schema 6 repositories convert only through the explicit `cairn repo migrate`.

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
| `find(pattern, *, project='default', where=None, order=None, limit=100, cursor=None)` | `{items, next_cursor}`: descriptors of the finished artifacts a reference pattern names, filtered and ordered in SQL. Without `order`, results are sorted by namespace, then name with runs of digits compared as numbers, then version. |
| `annotate(artifact_id, patch, *, expected_etag=None, target='artifact', request_id=None)` | Atomic JSON Merge Patch with optional concurrency check; `target='name'` patches the properties of every version of the artifact's name. Returns the artifact descriptor. |
| `annotation_layers(artifact_id)` | `{name, artifact, annotations, annotations_etag}`: the name-level and exact properties and their projection. |
| `versions(ref, *, project='default')` | Finished artifacts sharing the referenced name, ordered by numeric label. |
| `set_alias(artifact_id, alias, *, expected_id=None, request_id=None)` | Assigns an alias within the artifact's name; compare-and-swap against its previous target. `latest` is allowed; `live` and `vN` are reserved. |
| `query(ref, *, project='default', where=None, fields=None, limit=100, cursor=None, kind=None)` | `{items, next_cursor, cutoff, artifact_id}`; cursor pins artifact and acknowledged cutoff. |
| `put_blob(source, *, digest=None, size=None)` | Verified immutable content descriptor `{digest, size}`. |
| `link_blob(path)` | `{digest, size}` of a file the repository can read, registered in place without a copy. Only absolute paths under the repository's link roots are accepted, otherwise Unsupported; the stored entry records the file's modification time, and opening it raises IntegrityError when size or time changed. |
| `has_blobs(digests)` | Mapping of digest to availability. |
| `open_blob(digest)` | Binary stream context manager, caller closes it. |
| `delete(artifact_id, *, request_id=None)` | Soft deletion: `{id, deleted, retained_by}`. Names and aliases stop resolving it; `latest` falls back to the highest remaining finished version. Finished IDs stay readable for provenance until garbage collection. |
| `add_comment(artifact_id, anchor, body, *, author=None, request_id=None)` | Comment on a finished, not deleted artifact; anchor is structured source location data and `author` unauthenticated text. |
| `comments(artifact_id, *, scope='artifact', include_resolved=True)` | Comments on the exact artifact, or with `scope='name'` on every version of its name, oldest first. |
| `update_comment(comment_id, body, *, expected_revision=None, request_id=None)` | Replaces the body; a stale revision raises Conflict. |
| `resolve_comment(comment_id, *, resolved=True, note=None, by=None, request_id=None)` | Sets or clears `resolved_at` and `resolution: {note, by}`. |
| `delete_comment(comment_id, *, request_id=None)` | `{id, deleted}`; an unknown ID raises NotFound. |
| `collect_garbage(*, grace_seconds=3600, dry_run=True)` | `{dry_run, pinned, artifacts, candidates, removed, orphans}`. Purges artifacts deleted before the grace period that only other purged artifacts reference, then unreferenced content. |

There is no snapshot operation. A separate finished checkpoint must be a separate
artifact. Writer handles and sequence numbers exist only for reliable delivery.

## Records and values

Entries are `{sequence, kind, data}`. Kinds are `record`, `metadata`, `capture`,
and `dependency`. The Python client’s `log()` and `track()` add `_wall_time` to record data
when absent: floating-point Unix seconds from the client clock at the log call.
Explicit values are preserved; durable delivery/retries retain the recorded value.
Low-level repository/HTTP writes do not inject it. This is distinct from the
server-assigned record `created_at`. Record data is an arbitrary JSON object; `step` is optional and
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
entries, finalization, aliases, annotations and comments; `PUT /v1/comments/{id}`
and `PUT /v1/comments/{id}/resolution`; `POST /v1/resolutions`, `POST /v1/searches`, `POST /v1/links`,
`POST /v1/records/query`, `GET /v1/history?ref=...&project=...`,
`DELETE /v1/artifacts/{id}`, and `POST /v1/maintenance/gc`.
Blob uploads/downloads stream bytes. There are no revision, snapshot or separate
version resources, and no server report execution/editing routes.

`Client(path)` calls SQLite/files directly. `Client(url)` calls the selected
server. Remote writing uses a durable outbox; read-only clients never start or
resume delivery. `flush()` waits for acknowledgement; reads never merge queued
writes into server results.

`Client.start(namespace, name)` returns a writable `Artifact`; `Client.run()` and
`cairn.init()` add config and client-captured provenance through ordinary metadata
and captures. Unless `capture_output=False`, they also store the process's stdout and
stderr as `console/NNNNNN.log` file captures until the run finishes or closes.
Capture names under `provenance/` and `console/` are reserved for Cairn. `finish()` returns the finished descriptor; `.id` stays unchanged.
On a run, artifact or report, `find(pattern, where=, order=, limit=)` returns
`Reader`s for finished artifacts and records each with role `input`;
`use(ref, role=)` returns one and records it with the role. Each artifact and
role is recorded once per run; recording after finish raises Conflict. The top-level
`cairn.find` and `cairn.use` require a run or report. `Client.get(ref)` and
`Client.find(...)` read without recording. `Reader.dependencies` lists an
artifact's inputs and `Reader.inputs(role=...)` those recorded with one role. Patterns are references with
shell-style wildcards: `*` matches within one level of the name and `**` across
levels; without a selector they match each name's `latest`. Names and aliases
cannot contain `*`. `Reader.query` pages raw entries with a cursor. No revision
ID or snapshot API is exposed.

`cairn.artifact(namespace, name, *, repo=None, project=None, token=None,
outbox=None)` starts an independently owned artifact handle. Unspecified
repo/project inherit the context-local artifact or run, falling back to
normal repo and project discovery (`CAIRN_PROJECT`, `.cairn.toml`). Its context manager temporarily
installs it as the default, restores the previous handle on exit, and finishes
on normal exit or records failure on exceptional exit unless already finished;
the same holds for `cairn.init()` contexts and `cairn.run`.
The Python `add()` method replaces `capture()` without an alias; the persisted
`capture` entry kind is unchanged. `cairn.init()` remains the run entry point.
`cairn.run(...)` takes `init()`'s arguments and decorates a function: each call runs
inside its own `init()` context and restores the previous default handle afterwards.
Without `config=`, an OmegaConf `DictConfig` first argument becomes the resolved config.
Report build/publish may inherit the context's client configuration (including
after finish); explicit clients take precedence. Without a context, publish
uses ordinary discovery, while build retains its explicit-client requirement
for declared inputs.

Reports execute QMD locally, record the exact finished artifact IDs they read
(declared `cairn.inputs`, and `cairn.find` and `cairn.use` with the
rules above, each reference resolved once per build), capture source, outputs and
package information, then upload an ordinary report artifact. Rebuilds resolve
references and searches again. `cairn.Client()` in a cell opens the build's
repository; independent Python clients are not recorded. Reports are not a Python security sandbox. A
front-matter `config` is composed with Hydra and build `overrides`; cells see it
as `cfg`, and the resolved config is the manifest's and artifact metadata's `config`.

Repository export/import preserves artifact IDs, labels, records and retained
bytes. Imported producer handles are closed; live artifacts can register a new
producer. Imports never silently merge conflicting evidence. Integrity checks
reconstruct records, manifests and capacity accounting. GC and purge respect
retention and active transfer leases.

## Discovery and mutable annotations

Mutable information lives in one annotation store, separate from captured content.
Each annotation is a property (one top-level key) or a comment, and targets either
an exact artifact or a name: every version of `namespace/name`, including later
ones. Aliases are never targets; `@latest` resolves to its exact version when written.

Artifact descriptors include `annotations` (a JSON object) and `annotations_etag`:
the artifact's effective properties, its name's overlaid key by key by its own.
They are stored on the artifact as a projection so discovery stays indexed.
Captured metadata/records/files remain immutable after finish; annotations can be
edited on live or finished artifacts. Tags, titles and review status are ordinary
annotation fields, not backend-specific exceptions. Deleted artifacts reject edits
and new comments.

`annotate` accepts a top-level object using JSON Merge Patch semantics: nested
objects merge, arrays replace and null removes a key. It patches the selected
target's own properties, so removing an exact key reveals the name's value again. `expected_etag` compares with
the current annotation ETag under the artifact lock. A stale ETag raises Conflict;
a missing ETag allows an atomic patch against the latest state. `request_id` provides
exact retry receipts. No new artifact identity or version label is created.
Annotation objects are capped at 1 MiB and their scalar array membership index at
10,000 entries; a name-level patch must keep every version within these limits.
Updates, projections, ETags and index rows commit or roll back together.

Comments always anchor to an exact finished artifact and carry its `number`,
`artifact_deleted`, `author`, `revision`, `resolved_at` and `resolution`. They are
not part of the projected `annotations` and do not count toward its limits.

Discovery predicates use explicit roots `/metadata/...` and `/annotations/...`;
`find` also accepts `/value/...`.
`contains` tests scalar array membership with JSON type semantics (booleans differ
from numbers; numerically equal integers/floats match). It works on arbitrary
array fields and on record queries. It is not substring or object containment.
SQLite uses a derived annotation-member index keyed by path/value; PostgreSQL
uses a GIN index for eligible annotation containment predicates. Other metadata
and scalar predicates use the common query compiler and may require specialized
indexes for large workloads. Filters execute in SQL before pagination, never in
the Python client. Cursors bind project, namespace, state and predicate.

HTTP annotation edits use `PATCH /v1/artifacts/{id}/annotations` with
`{patch, expected_etag, target, request_id}`; `GET` on the same path returns the
layers. `GET /v1/artifacts/{id}/comments` accepts `scope` and `include_resolved`.
`/v1/capabilities` lists the `search` feature, which `find` requires, the `links`
feature, which `link_blob` requires, and the
`annotations` feature; clients check it before using
name targets, layers, comment authors, scopes, edits or resolution. Discovery passes
JSON `where` and optional `state` to `GET /v1/artifacts`. Python exposes
`Client.annotate`, `Client.find`, `Client.comments`, `Client.resolve_comment` and
`Client.reopen_comment`; the CLI exposes `artifact annotate [--name]`,
`artifact list --where` and `comments list|resolve|reopen`.

Export/import preserves the annotation store and validates projections, ETags and
the membership index against it. Purge removes an artifact's exact annotations,
including comments, and its index rows; name-level annotations remain. Annotation writes are
synchronous and do not enter a writer's record-upload journal.

### Native report document versions

New builds write `cairn/document` version **3** in `document.json`. Version 3
is version 2 plus fenced divs: blocks of kind `open` (with `attributes`: `id`,
`class` list and key/value pairs, and an optional layout `grid` of cell widths
in percent) and `close` nest the blocks between them. Markdown blocks may be
marked `caption`, code blocks may carry `caption_prefix` and `embed`, and
labels may be lettered subfigures. Markdown,
source locations, code, labels, and output metadata remain in the document.
Each code-cell MIME value in `outputs[].data` is an asset reference:

```json
{"image/png": {"asset": "assets/<sha256>.png", "mime": "image/png", "encoding": "binary"}}
```

`encoding` is `binary` for decoded images/audio/video, `utf-8` for textual MIME
values (including SVG, HTML and Markdown), and `json` for structured values such
as Plotly. Stream output `text` is likewise a `text/plain`, `utf-8` reference.
All MIME alternatives are retained. Asset paths are relative to the report bundle,
content-addressed, and included in its file inventory and published blobs. Identical
payloads with the same extension reuse one file. Report bundle data advertises
`document_version`; absent means version 1.

Readers support all versions: version 1 retains inline notebook MIME payloads;
versions 2 and 3 load referenced assets as needed. Existing reports are not migrated.
Older readers that only understand version 1 cannot read new native documents.
The `report/bundle` envelope remains version 1; exports remain complete bundles,
and `document.json` is served byte-for-byte. The archived `executed.ipynb` remains
standard nbformat with its original outputs, including Jupyter's base64 encoding;
it is not part of the native UI's report-loading path.

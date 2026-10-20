# Design: one annotation store

Status: implemented for repository schema 7 (Cairn 0.7). The open questions were settled
with the defaults below: free-text authors, name-level tags only in the UI, no replies
yet, and migration drops `cairn_comments`.

Cairn keeps mutable information about artifacts in two unrelated places. This
proposal moves both into one store whose rows point at what they describe: an
exact artifact or a name (every version of `namespace/name`). It adds comment
resolution and a CLI for it, so a person can comment in the UI and a local agent can pull,
address and resolve those comments.

## Current state

Checked against the code at v0.6.0 (file references are to that tree).

**Annotations** are stored inline on the artifact row.

- `cairn_artifacts.annotations` (JSON) and `annotations_etag` (storage.py:77-78);
  the ETag is `digest(annotations)`, deterministic rather than a counter (:360).
- `cairn_annotation_members` indexes scalar array members by path/value for
  SQLite discovery (:89-101); PostgreSQL uses a GIN index on the column (:102-104).
- `annotate()` applies a JSON Merge Patch under the artifact row lock, checks an
  optional ETag, re-validates (1 MiB, 10,000 members), rewrites the member index
  and records a request receipt (:349-385). Live artifacts can be annotated;
  deleted ones raise NotFound.
- Every descriptor carries `annotations` and `annotations_etag` (contract.py:29-30).
- Discovery (`Client.find`, `artifact list --where`, UI tag filters) compiles
  `/annotations/...` predicates against the column and the member index
  (query.py:250-287).
- The UI edits only `tags` (webui.py:154-170, replacing the list with an ETag) and
  reads `title`/`description` from annotations ahead of metadata (webui.py:332-339).

**Comments** are stored in a separate table.

- `cairn_comments(id, artifact_id, anchor, body, created_at)` with no index on
  `artifact_id`, no author and no update time (storage.py:166-174).
- The contract has `add_comment`, `comments(artifact_id)` (exact artifact only) and
  `delete_comment` (contract.py:196-202). `delete_comment` has no request ID and
  no existence check.
- Only the UI server lists comments across a report's versions, edits a comment
  body, and scopes deletion to a family. It does this with direct SQL
  (webui.py:465-552). The family query does not exclude deleted versions.
- Anchors are free JSON produced by the UI: `quote`, `path`, `line_start`,
  `line_end`, `media_key`, `media.{time, clip, region}` (ReportComments.tsx:11).
- There is no user identity anywhere: one shared token, and browser sessions proxy
  to it (webui.py:55-59, 113-119).

**Inconsistencies this fixes**

- CONTRACT.md:162 says deleted artifacts reject edits, but `add_comment` accepts
  deleted finished artifacts (storage.py:1243).
- Comment editing and family listing are UI-only, so the CLI and SDK cannot
  see what the UI shows.
- The docs call comments "annotations" (CONTRACT.md:63, architecture.md:67).
- CONTRACT.md:186 says there is no UI implementation; webui.py has one.

**Constraints**

- No migration code exists. A repository with another schema version is refused
  (storage.py:247-254), and the docs promise no implicit conversion
  (CONTRACT.md:4-6, architecture.md:99-100).
- `create_all` runs on every open and only adds missing tables (storage.py:257).
- Export/import pins `schema_version` (ops.py:112, :201).
- HTTP clients require the exact protocol version (transport.py:114-120). Nothing
  reads `/v1/capabilities` features.

## Model

One table, `cairn_annotations`. Each row is one annotation with a target and a
kind.

| Column | Meaning |
|---|---|
| `id` | `a_…` for properties; comments keep `c_…` IDs |
| `project`, `namespace`, `name` | always set: the name the annotation belongs to |
| `artifact_id` | exact target, or NULL for a name-level annotation |
| `kind` | `property` or `comment` |
| `key` | property key (a top-level annotation key); NULL for comments |
| `value` | property value, or `{"body": …, "anchor": {…}}` for a comment |
| `author` | optional free text supplied by the client; not authenticated |
| `created_at`, `updated_at` | timestamps |
| `revision` | integer incremented on every change, for comment compare-and-swap |
| `resolved_at`, `resolution` | comments only: when it was resolved and an optional `{by, note}` |

Constraints and indexes:

- `artifact_id` keeps a foreign key to `cairn_artifacts` and stays NULL for name
  targets.
- Two partial unique indexes enforce one property per key, since NULLs are never
  equal in a plain unique index: `(artifact_id, key)` where `artifact_id` is not
  NULL, and `(project, namespace, name, key)` where `artifact_id` is NULL. Both
  are restricted to `kind = 'property'`.
- An index on `(project, namespace, name, kind)` for family listing.
- An index on `(artifact_id, kind)` for exact targets.

### Targets

- **Exact artifact (`artifact_id`).** `@vN`, aliases and `@latest` are resolved
  to an ID when the annotation is written. A version label never moves, so
  `reports/direction@v1` is simply that ID.
- **Name (`artifact_id` NULL).** Applies to every version of the name, including
  future ones. Written with an explicit name reference such as `reports/direction`
  without a selector.
- **No alias targets.** An annotation stored "on `@latest`" would silently move to
  every new version. Writing through `@latest` therefore resolves to the exact
  version. Name-level writes use the bare name.

Name rows persist after purge (ops.py:612-618), so a name target stays valid
even when no version remains.

### Properties and the descriptor `annotations`

The effective annotation document of an artifact is its name-level properties
overlaid by its exact properties, key by key, with the exact property winning.
This document is what `annotations` means everywhere it is used today.

To keep discovery, descriptors and ETags unchanged, the artifact row keeps
`annotations`, `annotations_etag` and the member index as a **derived projection**
of the store, like the member index already is:

- Writing an exact property recomputes that artifact's projection.
- Writing a name property recomputes the projection of every artifact of that name
  (bounded by its version count), in the same transaction.
- `annotations_etag` stays `digest(effective document)`, so existing ETags remain
  valid across the migration and compare-and-swap behaves as before.
- `repo check` verifies that each projection equals its recomputation (extending
  ops.py:395-407).

`annotate(artifact_id, patch, …)` keeps its contract. Each top-level key of the
patch becomes a property upsert (nested merge within the key's value) or, for
`null`, a deletion of that artifact's exact property. A new `target` argument
selects `name` instead of the exact artifact. With an exact target, deleting a key
that comes only from the name leaves the name value visible. This is the single
semantic difference, and it is documented. To hide a name value for one version,
set the key explicitly on that version.

### Comments

- A comment always targets an exact finished artifact (its anchor refers to that
  version's source lines) and always belongs to the name.
- `comments(artifact_id, *, scope="artifact"|"name", include_resolved=True)` lists
  comments. Each comment includes its version `number` and `artifact_deleted`. The
  `"name"` scope moves the UI's family listing into the contract. The SDK's
  `Client.comments(ref)` defaults to the name and to open comments.
- New comments on deleted artifacts are rejected, matching CONTRACT.md:162.
  Existing ones stay listed and are marked.
- `update_comment(id, body, *, expected_revision=None, request_id=None)` moves the
  UI's in-place edit into the contract, with optional compare-and-swap.
- `resolve_comment(id, *, resolved=True, note=None, by=None, request_id=None)` sets
  or clears the resolution. The SDK wraps it as `resolve_comment` and
  `reopen_comment`.
- `delete_comment(id, *, request_id=None)` raises NotFound for an unknown ID.
- Comments are never part of the projected `annotations` document, so they do not
  count toward its 1 MiB limit or the discovery index.

### Purge and garbage collection

- Purging an artifact deletes its exact annotations, including comments.
- Name-level annotations remain until removed explicitly, also when no version of
  the name remains.

## Interfaces

**Python**

```python
client.annotate("reports/direction@latest", {"status": "draft"})  # exact version
client.annotate("reports/direction", {"tags": ["paper"]}, target="name")
client.comments("reports/direction")  # open comments, all versions
client.comments("reports/direction@v3", scope="artifact", include_resolved=True)
client.resolve_comment("c_123", note="Rewrote report.qmd:42-44", by="agent")
client.reopen_comment("c_123")
```

**CLI**

```bash
cairn comments list reports/direction [--all] [--exact] [--json]
cairn comments resolve c_123 [--note TEXT] [--by NAME]
cairn comments reopen c_123
cairn artifact annotate reports/direction '{"tags":["paper"]}' --name
```

The plain output prints one line per comment:

```
c_123  v3  report.qmd:42-44  "quoted text"  → body
```

`--json` prints full rows for agents.

**HTTP** (all existing routes keep their behavior)

- `GET /v1/artifacts/{id}/comments?scope=name&include_resolved=false`
- `PUT /v1/comments/{id}` with `{body, expected_revision, request_id}`
- `PUT /v1/comments/{id}/resolution` with `{resolved, note, by, request_id}`
- `DELETE /v1/comments/{id}` accepts an optional `{request_id}`
- `PATCH /v1/artifacts/{id}/annotations` accepts `target: "name"`
- `GET /v1/artifacts/{id}/annotations` returns the layers

**UI**

- Comments are listed through the contract instead of webui SQL.
- Resolved comments are hidden by default and shown with a toggle.
- Each comment shows the version it was made on, its author if set, and its
  resolution note.
- Resolve and Reopen actions are added.
- The tag editor on the artifact page has an "All versions" toggle that edits the
  name's tags. Saving an empty tag list removes the key, so a version without tags
  of its own shows the name's tags again.

## Compatibility

- **Repository schema 6 → 7, by explicit migration only.** `cairn repo migrate`
  runs in one transaction under the existing PostgreSQL advisory lock. It:
  1. requires schema 6;
  2. creates `cairn_annotations`;
  3. converts each non-empty `annotations` object into exact property rows;
  4. copies comments with their IDs and timestamps;
  5. drops `cairn_comments`;
  6. verifies the projection and sets schema 7.

  The projection is byte-identical to the old column, so ETags and the member index
  are unchanged. Opening a schema 6 repository without migrating keeps failing with
  a message that names the command. The docs recommend `cairn repo export` before
  migrating.
- **Exports.** Export manifests carry schema 7. A schema 6 export is imported with
  0.6 and then migrated.
- **Protocol stays 4.** Every existing route and response shape is unchanged. The
  new routes are announced as an `annotations` feature in `/v1/capabilities`. New
  clients check that feature before calling them. Old clients keep working against
  a new server; new clients against an old server get a clear Unsupported error
  for the new operations only.
- **Descriptors.** Unchanged: `annotations` is the effective document and
  `annotations_etag` its digest.
- **Request receipts.** Stored results from schema 6 replay unchanged. The new
  operations use the same global receipts (`_once`).
- **Behavior changes.**
  - New comments on deleted artifacts are rejected.
  - `delete_comment` on an unknown ID raises NotFound.
  - The exact-target deletion rule for name-inherited keys described above.

## Validation plan

- **Migration.** A schema 6 fixture repository on SQLite and PostgreSQL, migrated.
  Annotations, ETags, member rows and comment rows must be identical. `repo check`
  passes, and a second run is refused.
- **Projection.** Name and exact properties, with overlay precedence. Concurrent
  name and exact writes keep projections consistent. Discovery finds an artifact
  through a name-level tag on SQLite (member index) and PostgreSQL (GIN).
- **Comments.**
  - Family listing, exact listing and resolved filtering, with direct/HTTP parity.
  - Revision compare-and-swap and receipts for every mutation.
  - Rejection of new comments on deleted artifacts.
- **Garbage collection.** Purge removes exact annotations and keeps name ones.
- **CLI and UI.**
  - The CLI round trip: list, resolve, reopen.
  - The UI suite with resolve/reopen and inherited tags.
  - The existing comment and tag tests unchanged except for routes.

## Decisions

1. **Authorship.** `author` and `by` are free text supplied by the client. Cairn has
   no user identity; adding one is a separate, larger change.
2. **Name-level properties in the UI.** Only tags. Other keys can be set per name
   through the SDK and CLI.
3. **Replies.** Not yet. They can be added later as comments whose `value.parent`
   names another comment, with no schema change.
4. **The old table.** Migration drops `cairn_comments` after copying it.

# Contract

What programs, readers and stored repositories can rely on. The order of priorities is
stability, performance, features. `cairn/cairn.proto` is the protocol; tests fail when it, its
operations or the bytes of a record change.

## Data

**Artifact.** An append-only list of records with one permanent ID (32 hexadecimal digits) and
a name `project/namespace/name`. While *live* it accepts records; *finished* it never changes.
A run is an artifact in the namespace `runs`; namespaces have no other meaning to the server.

**Record.** A map, numbered from 1 without gaps, at most 64 MiB. Records of one process keep
their order. A record is encoded once, by the program that logs it; its bytes are kept.

**Value.** Null, boolean, 64-bit integer, 64-bit float (with NaN and the infinities), text,
bytes, list, map with text keys, blob, or reference.

**Blob.** Bytes, stored once per SHA-256 digest. The value holds digest, size and media type.

**Reference.** The ID of a finished artifact.

Blobs and references may appear anywhere in a record. What they name is kept for as long as
the record is. The server knows records, blobs and references, and nothing else of a value.

**Current value.** Of a key, the value in the last record that has it. Logging a key again
replaces its whole value. A key `a/b` and the `b` inside `a` share a path; the later is found.

**Finishing** appends a last record, seals the artifact, says whether it completed, and gives
it the next version of its name, all at once. `latest` is the newest completed version.

**Labels** are outside the log and may change at any time. A *tag* is a key with one value
(null, boolean, number or text up to 256 characters) per artifact. An *alias* belongs to a
finished artifact and to one version of a name; setting it moves it. Deletion hides an
artifact until it is restored; `collect` frees what is deleted and not referred to.

## Conventions

Keys starting with `_` belong to Cairn. The SDK refuses others of them; the server does not
interpret them, except that files below them are listed only when a pattern asks for them.

| Key | Meaning | Written by |
|---|---|---|
| `_time` | client clock, Unix seconds | every `log` |
| `_use` | reference read, by role | `use` |
| `_config` | configuration | start |
| `_about` | provenance: code, packages, command, machine | start of a run |
| `_console` | a piece of output, as bytes | the run's process |
| `_outcome` | `completed`, `failed`, `cancelled` or `incomplete` | finish |
| `_error` | type and message | finish after an error |

A map with `_type` says how readers interpret it: `directory`, `image`, `array`, `table`,
`figure`, whose content is under `data`. Unknown types stay maps.

Only completed artifacts are used, or any finished one when the caller says `incomplete`. The
server refuses references to live artifacts whatever the client says.

## Writing

- A batch of records is accepted completely or not at all. Sending it again changes nothing:
  each writer numbers its records, and the server skips what it has.
- Records are refused (`pending`) until the content they name is stored.
- Finishing waits (`pending`) while other writers have not left, unless it is forced.
- Content sent in pieces continues where it broke off, and is there only once its digest is
  right.
- A linked file is checked against its digest when it is registered, and its size and time
  whenever it is read; a changed one is missing, never wrong. A server links only below the
  folders given with `--link-root`; the client uploads anything else.
- What a program logged to a server is on its disk when `log` returns, and reaches the server
  in order, also after the program or the server was killed (`recover`).

## Finding

Paths name places in records: `loss`, `_config/lr`, `image/width`. Records of one artifact are
read in order from any position, all or those with a key; numbers at a path are read as
columns. Artifacts are found by name, labels, and for each path by the last, smallest and
largest value. Files are found by path, glob, or part of the path. Numbers, booleans and text
up to 256 characters are found; lists and longer text are not.

Conditions read `path operator value` with `=`, `!=`, `<`, `<=`, `>`, `>=`, `contains`, or are
a path alone. Paths may be `min(path)`, `max(path)`, `tag:key` and `alias:name`. In names, `*`
stays within a level and `**` crosses slashes.

Indexes are derived from the records and can be rebuilt without changing any artifact.

## HTTP

`POST /v1/{operation}` takes and returns a message of `cairn.proto`;
`cairn/operations.py` lists which. `PUT` and `GET /v1/content/{digest}` carry plain bytes; a
piece has the parameters `offset` and `size`. `GET /v1` describes the server and needs no
token; everything else takes `Authorization: Bearer`. Errors are an `Error` message with the
code `invalid`, `unauthorized`, `not_found`, `conflict`, `pending` or `corrupt`.

## Frozen, and not

Frozen: the values and their encoding, the messages and operations of `/v1`, references and
conditions, and the conventions above. Fields, operations and conventions may be added.

Not frozen: the repository's tables and indexes (a schema number guards them), the outbox,
which keeps only what a program has not sent yet, and `cairn-ui` with its workspaces and reports,
which Cairn does not know.

Repositories of Cairn before this contract are refused untouched; the tag `v0.17.1` reads them.

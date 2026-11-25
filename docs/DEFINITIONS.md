# Definitions

Draft for correction. Nothing here is built yet.

**Artifact.** An append-only list of records with one permanent ID and a name
`project/namespace/name`. While *live* it accepts records; *finished* it never changes.
A run is an artifact in the namespace `runs`; namespaces have no other meaning.

**Record.** A map at a position in an artifact, numbered from 1 without gaps. Records from
one process keep their order. A record never changes once accepted.

**Value.** Null, boolean, integer, float (with NaN and infinities), string, bytes, list,
map with string keys, blob or reference.

**Blob.** Bytes stored once per SHA-256 digest; its value holds digest, size and media
type. Large content is a list of blobs.

**Reference.** A link to an artifact by ID.

Blobs and references may appear anywhere in a record. The server keeps what they name for
as long as the record exists. It knows records, blobs and references, and nothing else.

**Type.** A map with `_type` says how readers interpret it. Unknown types stay readable,
with downloadable blobs. Types nest.

**Conventions.** Keys starting with `_` belong to Cairn's SDK and readers, which agree on
their meaning. The SDK refuses unknown ones in `log`; the server does not look at them.

| Key | Meaning | Written by |
|---|---|---|
| `_time` | client clock, Unix seconds | every `log` |
| `_use` | references read, by role | `use` |
| `_config` | configuration; readers merge, later keys win | start of a run |
| `_about` | provenance: code, packages, machine | start of a run |
| `_console` | a piece of the process's output | the run's process |
| `_outcome` | `completed`, `failed`, `cancelled`, `incomplete` | `finish` |

**File.** A value of type `file` under a key, which is its name. A path's value is the one
in the last record that has it, so a file logged again is replaced for readers, and both
records remain. A key `a/b` and the `b` inside `a` are the same path; as always, what was
logged later is found.

**Finish.** Appends a last record, seals the artifact and gives it the next version of its
name, all at once.

**Name state.** Outside the log, changeable at any time: aliases (`latest` and others),
tags, deletion. Liveness (heartbeats) is process state.

## Writing

`log` is the only way to add records. Everything else is shorthand:

    log({"step": 10, "loss": 0.3, "image": Image(x), "mesh.ply": File("out.ply")})
    add(path, name)      = log({name: File(path)})
    add(folder, name)    = records of the folder's files, accepted together
    use(ref, role)       = log({"_use": {role: ref}}) and returns a reader
    run(name, config)    = an artifact in runs, log({"_config": ..., "_about": ...})

A batch is accepted completely or not at all, and sending it again changes nothing.

## Finding

A *path* names a place in a record: `/loss`, `/_config/lr`, `/image/width`.

- **Within an artifact:** its records in order, from any position, filtered by path.
- **Across artifacts:** by name, tags, references, and for each path by the last value and
  by the smallest and largest number. Numbers, booleans, short strings and blobs are
  found; lists and long text are not.
- **Live:** the records after a position, as they arrive.

Indexes are derived from the records, private to the server and rebuilt without changing
any artifact.

## Encoding

To be measured: CBOR (RFC 8949) in storage and transfer, with tags for blobs and
references; JSON only to display records.

## Frozen

The values, their encoding, `/v1` and the query language. Not frozen: indexes, storage
layout, types, conventions added later.

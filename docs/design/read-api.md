# Design: one read API

Status: implemented, not released. It changes the public SDK; open decisions are
listed at the end. Stored data does not change.

## Principle

Whatever a run or report finds or uses becomes its input, so a downstream run
can discover everything it depended on. Searches run in the repository's
database, so only their results travel.

## Previous state

Checked against v0.10.0.

- `Client.resolve`, `Client.find` and `Reader.descriptor` returned the database
  row (`storage.py:485`), with bookkeeping such as `cutoff`, `publication_bytes`,
  `intent` and `deleted_at`.
- A search result contained the results, so a report could read them without
  recording an input. Reports had no search; a `cairn.Client()` opened in a cell
  discovered the repository from the build folder and could open an empty one.
- `Reader.records()` collected every record into one list.
- `cairn.finish(value=...)` inside `with cairn.init()` or `@cairn.run` raised
  `Conflict` on exit.

## Recording reads

```python
cairn.find(pattern, *, where=None, order=None, limit=None) -> list[Reader]
cairn.use(ref, *, role="input") -> Reader
```

Like `cairn.log`, both belong to the current run, artifact or report and raise
without one; `run.find` and `run.use` are the same on an explicit run handle.

- `find` records every artifact it returns with role `input`. Artifacts that a
  `where` or `order` only compared in the database are not recorded: "the best of
  500" records the best one. That is a deliberate simplification.
- `use` records one artifact with the given role. The role is a free-form label
  shown in the UI's lineage view; Cairn attaches no meaning to it.

Each artifact and role is recorded once per run. A finished run can no longer
record, so `find` or `use` on it raises `Conflict`. Lineage says which artifacts
were used, not how.

## Reading without recording

`Client` is the read API for scripts and tools outside lineage, like `wandb.Api()`.
`client.get(ref)` returns one reader and `client.find(pattern, ...)` searches;
neither records anything.

## Patterns

A pattern is a reference with shell-style wildcards: `*` matches within one level
of the name and `**` across levels. Without a selector it means the latest
version of each name, as in a reference. Names and aliases cannot contain `*`.

| Pattern | Matches |
| --- | --- |
| `runs/fit/*` | The latest version of every name one level below `runs/fit/` |
| `runs/fit/**` | The latest version of every name below `runs/fit/` |
| `runs/fit/*@*` | Every version of those names |
| `runs/fit/*@best` | Each name's version with the alias `best` |
| `*/baseline` | The latest `baseline` in every namespace |

`where` filters on fields, as before:
`{"path": "/metadata/config/study", "op": "eq", "value": "lr"}`. `order` sorts by a
field, `"/value/loss"` ascending or `"-/value/loss"` descending; without it,
results are sorted by name, with runs of digits compared as numbers so that
`seed2` comes before `seed10`, then by version. `limit` keeps the first matches. Paths
start with `/value`, `/metadata` or `/annotations`. Only finished artifacts are
returned; nothing matching gives an empty list. The database evaluates all of it.

## Readers

A reader is one finished artifact: `id`, `project`, `namespace`, `name`,
`version`, `outcome`, `created_at`, `finished_at`, `annotations`, `value`,
`config`, `metadata`, `dependencies`, `inputs(role=...)`, `files`, `file(name)`, `download()`,
`console()`, `records(where=None, fields=None)` and `str(reader)`
(`runs/fit/cubic@v2`). Files are fetched into the disk cache and records page by
page, only when asked for. `reader.annotate(patch)` edits the annotations.

## Reports

In a report, `cairn.find` and `cairn.use` read the repository the report is built
from, and so does `cairn.Client()`. A reference resolves once per build. A rebuild
runs the code again, so searches and `@latest` resolve again.

## Backend and HTTP

A new backend method, `find(pattern, project=, where=, order=, limit=, cursor=)`,
returns `{items, next_cursor}` with artifact descriptors. Over HTTP it is
`POST /v1/searches`, announced by the `search` capability, so older servers and
clients keep working with each other; the protocol version stays 4.

## Writing

Unchanged, except that `cairn.finish(value=...)` inside `with cairn.init()` or
`@cairn.run` is allowed, as it is for `cairn.artifact`.

## Compatibility

Stored data, including the dependency format, does not change. Removed, with
their replacements:

| Old | New |
| --- | --- |
| `Client.find(namespace=, where=, state=)` yielding descriptors | `Client.find(pattern, where=, order=, limit=)` returning readers; `client.backend.list_artifacts` still lists live artifacts |
| `reader.read()` | `reader.value` |
| `Client.resolve(ref)` | `Client.get(ref)` |
| `reader.descriptor` | The reader's fields |
| The report `client` | `cairn.find`, `cairn.use` |

Changed: `reader.records()` yields the logged data page by page instead of
returning a list of entries; `list(reader.records())` gives a list, and each item
is what was logged rather than `entry["data"]`. New names and aliases cannot
contain `*`. Natural order uses a SQLite collation registered per connection and,
on PostgreSQL, an ICU collation created when the repository opens.

## Open decisions

1. `Reader.query` pages raw entries with a cursor; `records()` covers the common
   case. Keep it or remove it?

# Cairn2

Cairn stores experiments, files, datasets, models, and reports using one artifact
lifecycle. Choose one repository: a local SQLite database with files, or an HTTP
server backed by SQLite or PostgreSQL and a filesystem or S3 object store.
An artifact keeps one ID throughout its lifetime: writable while live, immutable
once finished. Version labels and aliases point directly to artifacts.

References use `namespace/name@selector`: `runs/training@latest`,
`artifacts/model@v3`, or `reports/summary@v2`. The prefix is an arbitrary namespace,
not an object type. All top-level objects are artifacts; typed value formats
determine how their contents are interpreted and rendered. Names can include
further slashes, as in `models/training/checkpoint@latest`.

This repository contains the Python API, storage implementation, authenticated
HTTP API, CLI, and a client-side executable report builder. Report execution happens on the client, not on the server.

## Install

Python 3.11 or newer is required. From this checkout:

```sh
python -m pip install -e '.[all]'
```

`all` installs every optional feature, including the browser UI. With uv:

```sh
uv run --extra all cairn server --host 0.0.0.0
```

Or install only the extras you need. The base package requires SQLAlchemy and
HTTPX. `server` adds FastAPI/Uvicorn; `reports` adds the Markdown and Jupyter
execution stack; `media` adds NumPy, Pillow, Arrow, Plotly, and Matplotlib;
`postgres` adds Psycopg; `s3` adds Boto3. The command is `cairn`, and the Python
module is `cairn`.

## Record an experiment

Python `log()` and `track()` automatically include `_wall_time` (Unix seconds
from the client clock). It is queryable as `/_wall_time`, stays unchanged during
upload retries, and can be supplied explicitly for imported measurements.

```python
import cairn

run = cairn.init("./results", name="baseline", config={"learning_rate": 0.01})
cairn.log({"step": 0, "loss": 0.8})
cairn.log({"step": 1, "loss": 0.3})
cairn.update({"description": "Two recorded steps"})
publication = cairn.finish(value={"final_loss": 0.3})
assert publication["id"] == run.id
print(run.id, publication["number"])
```

`cairn.init()` installs a context-local default handle and returns that same
explicit handle. `log`, `track`, `add`, `update`, `use`, `flush`,
and `finish` also exist on the handle. Use explicit clients when working with
several repositories or runs:

```python
from cairn import Client

with Client("./results") as client:
    with client.run("comparison", config={"learning_rate": 0.02}) as run:
        baseline = run.use("runs/baseline@latest", role="baseline")
        run.log(step=0, loss=0.25)
        run.update({"baseline_loss": baseline.read()["final_loss"]})
    print(run.id, run.result["number"])
```

For a standalone dataset or other artifact, no explicit client is needed:

```python
with cairn.artifact("datasets", "samples") as dataset:
    cairn.add(cairn.Table([{"sample": 0, "weight": 1.0}]), name="samples")
cairn.use(dataset.id, role="training-data")
```

The artifact inherits the current run's repository and project, or uses normal
discovery when no run exists. Optional `destination=` and `project=` override
these defaults. Inside the block, top-level calls target the artifact; exiting
restores the previous run. Use `cairn.finish(value=...)` inside the block to
set its main value explicitly. Otherwise it finishes automatically.
`add()` replaces the former Python `capture()` method; no alias is retained.
It stores named files or typed values, not environment metadata.

After `cairn.init()`, report `build()` and `publish()` can omit `client=`:
they inherit that run's repository and project even after the run finishes.
Explicit clients remain supported.

An artifact context finishes on normal exit and requests a failed outcome when
its body raises. A successfully finished `cairn.init()` handle closes the client
it created. For explicit clients, `Client.close()` releases connections and stops
remote delivery; use `flush()` or `finish()` to wait for acknowledgements before
closing.

Public `init(config=...)` and `Client.run(config=...)` store configuration under
`metadata["config"]`. They also capture run provenance on the client:
Python/platform/executable/working directory, launch arguments, installed Python
package versions, Git commit/branch/working-tree state, a dirty diff, dependency
manifests/lockfiles, and a bounded source archive. These are ordinary metadata and
named file captures, available through the same reader/download API as other data.

```python
with cairn.init("./results", capture_source=False) as run:
    run.log({"loss": 0.2})  # Environment/dependencies, without source or diffs.

with cairn.init("./results", provenance=False) as run:
    run.log({"loss": 0.1})  # Only explicit metadata, records and captures.
```

Source archives exclude repository internals, common credential paths and symlinks. They
are bounded; provenance metadata records omissions and collection errors. Outside
Git, capture is limited to the entry script and known dependency files in the
working directory. Capture happens at initialization, so files changed later
need an explicit `run.add(...)`. No raw environment-variable dump is taken.
The archive records evidence; it does not promise a fully reproducible container.
Generic `client.start(...)` performs no automatic provenance capture.

Internally, `start(project, namespace, name, request_id=...)` creates only
one live artifact and its first writer. Metadata follows as ordinary ordered
`metadata` entries. There is no separate experiment configuration column or
run-specific storage table.

## Read values and records

```python
from cairn import Client, Table

with Client("./results") as client:
    dataset = client.start("datasets", "small-sample")
    dataset.finish(value=Table([{"x": 1, "y": 2}, {"x": 2, "y": 4}]))
    rows = client.resolve("datasets/small-sample@latest").read().load()

    baseline = client.resolve("runs/baseline@latest")
    records = baseline.records(where={"path": "/loss", "op": "lt", "value": 0.5})
    print(rows, [record["data"] for record in records])
```

Readers pin the resolved finished artifact ID. References accept
`namespace/name@latest`, `namespace/name@vN`, `namespace/name@alias`, a full
artifact ID, or an unambiguous hexadecimal ID prefix of at least eight characters.
Omitting the selector means `latest`. Direct IDs are stable before and after
finish. Live inspection/querying uses the backend; SDK readers and report inputs
require finished artifacts. There is no revision or snapshot API. Starting again
under the same name creates a new artifact ID; finishing assigns the next label.

`File`, `Directory`, `Image`, `Array`, `Table`, and `Figure` wrap common media.
`add(path)` records a named file or directory snapshot. `track(value,
metadata=None, key="value")` writes a record containing a value. `TypedValue`
supports custom formats with explicit blob and artifact references; unknown
formats remain inspectable through `StoredValue`. No pickle-based object codec
is used.

JSON numbers must be finite, and integers must be in the portable exact range
`-(2**53 - 1)` through `2**53 - 1`. Use typed `Array` values for wider integers or
non-finite numeric data. An append batch accepts at most 10,000 entries, 1 MiB
per encoded entry, and 4 MiB of encoded entries in total; large media belongs in
blobs referenced by typed values.

## Annotate and find artifacts

`metadata` records experiment evidence and freezes at finish. `annotations` is a
separate, editable JSON object for tags, display titles, notes, review status or
other organizational fields. Tags are just an array inside that object.

```python
with Client("./results", project="study") as client:
    artifact = client.annotate("runs/training@latest", {
        "tags": ["paper", "baseline"],
        "review": "approved",
        "title": "Main baseline",
    })
    for match in client.find(namespace="runs", where={"and": [
        {"path": "/annotations/tags", "op": "contains", "value": "paper"},
        {"path": "/metadata/config/learning_rate", "op": "lt", "value": 0.01},
    ]}):
        print(match["id"], match["annotations"])
    client.annotate(artifact["id"], {"review": "published", "title": None},
                    expected_etag=artifact["annotations_etag"])
```

`annotate` applies JSON Merge Patch: nested objects merge, arrays replace, and
`null` removes a key. `expected_etag` is optional; supply the fetched ETag when
editing existing information to detect concurrent changes. The repository applies
the patch atomically. A conflict preserves the newer data. Annotation edits do not
create artifact versions or change the captured manifest. Writer and reader handles
also expose `.annotate(patch, expected_etag=...)`.

`find()` yields artifact descriptors and fetches pages automatically; `limit` caps
the total results. Without `state`, live and finished artifacts are included.
Predicate paths start with `/metadata` or `/annotations`. `contains` means exact
scalar array membership, not substring matching. Other operators match record
queries. All filters execute in SQL before pagination. SQLite has a generic indexed
lookup for annotation array members; PostgreSQL uses a GIN annotation containment
index. Arbitrary scalar comparisons are supported but may need field-specific
indexes at larger scale. This is structured filtering, not full-text search.

For explicit pages use `client.backend.list_artifacts(project=..., namespace=...,
where=..., state=..., limit=100, cursor=...)`. Cursors bind all filters; discovery
has refresh semantics rather than a cross-page snapshot. Annotation changes are
synchronous operations, separate from the record-upload journal.

```sh
cairn --repo ./results --project study artifact annotate runs/training@latest \
  '{"tags":["paper","baseline"],"review":"approved"}'
cairn --repo ./results --project study artifact list --namespace runs --state finished \
  --where '{"path":"/annotations/tags","op":"contains","value":"paper"}'
```

## Select a repository and authenticate

Python clients and ordinary CLI commands use the following destination order:

1. An explicit path/URL (`Client(...)`, `--repo`, or `--url`).
2. `CAIRN_URL`, then `CAIRN_REPO`.
3. The nearest project `.cairn` directory, or a `.cairn` JSON file containing
   `url`, `repo`, or `destination`; discovery walks parent directories.
4. `./.cairn/repo`.

A path selects direct local storage. An HTTP(S) URL selects that server alone.
Remote reads query the server; they do not combine server results with pending
local writes. `server` uses the same destination discovery for local repositories;
if discovery selects a remote URL, it asks for an explicit `--repo` instead.
Administrative `repo` commands use direct storage configuration, with
`./.cairn/repo` as their local default.

Bearer tokens are discovered in this order: explicit `token=`/`--token`,
`CAIRN_TOKEN`, `CAIRN_TOKEN_FILE`, project `.cairn/token` or a `.cairn` JSON
`token`/`token_file` field, then `~/.config/cairn/token`. Relative token files in
project JSON resolve beside that file. For example:

```json
{"url": "https://cairn.example.org", "token_file": "./cairn-access-token"}
```

The server uses one shared bearer token. It creates `.cairn/token` with mode
`0600` when no token is found. A `.cairn` JSON file must supply a token or token
file instead. Startup prints credential-free URLs. Clients reject base URLs
containing credentials, query parameters, or fragments. HTTPS termination and
service supervision are deployment responsibilities.

Remote Python writers stage immutable bytes and ordered events in a durable
outbox before `log()` returns. The default location is
`$XDG_STATE_HOME/cairn/outbox/<destination-hash>` or
`~/.local/state/cairn/outbox/<destination-hash>`. `Client(outbox=...)` selects an
explicit location. `flush(timeout=...)` waits for acknowledgements, and
`Client.recover()` reopens recorded writers after a restart. A journal is bound
to its destination and repository identity; it is delivery state, not another
queryable artifact repository. A new remote writer needs server acknowledgement
before it can accept local records. Direct CLI lifecycle commands issue
synchronous backend requests rather than providing offline queuing.

## Run a server

With no arguments, `cairn server` discovers the same local repository as the
Python client (default `.cairn/repo`) and serves it on localhost port 8765.
You can log locally first, then start the server to expose the existing data to
remote clients. Local logging can continue against that same repository.
Startup prints the absolute repository path. Explicit `--repo` or
`--database-url` settings take precedence over discovery.
If that default port is occupied, it warns and selects a free port. An explicit
`--port` is fixed and fails if occupied. Use `--host 0.0.0.0` to listen on all
interfaces. Startup prints URLs using the actual bound port.

When the UI is installed, startup also prints login links with the server token
embedded in the URL fragment. They remain reusable across restarts until that
token changes. Opening the link signs the browser in.

A local SQLite/files deployment:

```sh
cairn --repo /srv/cairn/repository server --host 127.0.0.1 --port 8765
```

The repository contains `metadata.sqlite3` and an `objects/` directory. The token
file belongs to the nearest existing project `.cairn` directory (otherwise the
working directory's `.cairn`) unless supplied through
token discovery.

For PostgreSQL and S3, provision the database and bucket first. Boto3 uses its
normal credential provider chain. Set `CAIRN_TOKEN_FILE` to an existing token file:

```sh
export CAIRN_TOKEN_FILE=/etc/cairn/token
cairn server --host 0.0.0.0 --port 8765 \
  --database-url 'postgresql+psycopg://cairn@db.example.org/cairn' \
  --s3-bucket cairn-artifacts --s3-prefix cairn/ --s3-region eu-central-1
```

For an S3-compatible service, add `--s3-endpoint http://object-store:9000`.
For PostgreSQL with filesystem objects, replace the S3 flags with
`--objects /srv/cairn/objects`. Every explicit database URL requires an explicit
object store. The CLI starts one Uvicorn process; it does not provision databases,
buckets, TLS certificates, or a process supervisor.

On another machine, copy the same token into `~/.config/cairn/token` with private
file permissions. Discovery reads that location automatically:

```sh
cairn --url https://cairn.example.org info
cairn --url https://cairn.example.org artifact list --namespace runs
```

## Use the CLI

Global destination/project/token flags precede the command. JSON inputs accept
inline JSON, `@path.json`, or `-` for standard input.

```sh
cairn --repo ./results artifact start trial --namespace runs --config '{"seed": 7}'
# Use artifact.id and writer.id returned by start in these commands:
cairn --repo ./results log ARTIFACT_ID WRITER_ID '{"step": 0, "loss": 0.4}'
cairn --repo ./results artifact finish ARTIFACT_ID --writer WRITER_ID
cairn --repo ./results inspect runs/trial@latest
cairn --repo ./results query runs/trial@latest --kind record \
  --where '{"path":"/loss","op":"lt","value":0.5}' --limit 100
```

`cairn init` defaults to the `runs` namespace; `artifact start` defaults to
`artifacts`. Both accept `--namespace`, and report publication uses `reports`.
These are client conventions rather than reserved object types. Other lifecycle commands
include `artifact update`, `writer`, `close-writer`, and `versions`.
Use explicit `--sequence` when retrying a CLI log with the same event; changed
content at an acknowledged sequence conflicts. Paginated queries return
`next_cursor`; repeat the same query with `--cursor`. `--field /path` projects
fields and uses those JSON pointers as result keys.

## Browser UI

Install the UI extra and start the server:

```sh
python -m pip install -e '.[ui]'
cairn --repo ./results server
```

The server serves the bundled React UI and prints an immediate login link.
Browser sessions last 12 hours. Use `--no-ui` for API-only operation or
`--no-auth` for local development without a token. Reports open directly with
a table of contents, interactive figures and media, source copying, and comments
that carry across reports with the same project and name. Artifact/run pages
include lineage, records, files, editable tags, and click-to-copy IDs.

The UI source lives in [ui/](ui/README.md). Rebuild the bundled assets after
changes with `npm ci --prefix ui && npm run build --prefix ui`.

## Build a report locally

```sh
cairn report new analysis.qmd --title 'Analysis'
cairn --repo ./results report build analysis.qmd --output ./build/analysis
cairn --repo ./results report publish ./build/analysis --name analysis
cairn --repo ./results report export reports/analysis@latest ./exported-analysis
cairn --repo ./results report copy reports/analysis@latest ./editable-analysis
```

`report new` defaults to `NewReport.qmd`. `report build --publish` combines the
build and publication steps. Outputs must use a new or empty directory.

A report can declare artifacts and access pinned readers:

````markdown
---
title: Baseline analysis
cairn:
  inputs:
    baseline: runs/baseline@latest
---

## Results {#sec-results}

```{python}
print(inputs["baseline"].read())
```
````

All declared inputs resolve together before any cell executes. The builder
captures source/includes and declared local resources, starts a fresh Python
kernel, and emits HTML, native document JSON, an executed notebook, assets,
source mappings, and a manifest with exact dependency IDs and package versions.
The default `python3` kernel uses the builder's Python environment; `--kernel`
selects another installed kernel. Code runs locally in a copy of captured files.
It is trusted Python code, not a security sandbox.

The implemented QMD/Markdown subset includes tables, footnotes, dollar math
rendered as MathML, figures, cross references, callouts, local includes, and
Python stdout/static rich outputs. It supports `execute.echo`/`execute.eval` and
cell options `label`, `fig-cap`, `tbl-cap`, `echo`, `eval`, and `output`.
`cairn.resources` lists additional relative files/directories. Includes use
`{{< include relative.qmd >}}`. Equations can use `$$...$$ {#eq-name}`; section and
figure labels use `{#sec-name}` and `{#fig-name}`. See [the example](examples/report.qmd).

This is a QMD subset, not full Quarto. It does not implement bibliography/citation
processing, arbitrary Quarto extensions, other execution languages, or JavaScript
widgets. Markdown raw HTML is disabled; rich HTML output is placed in an iframe
without scripts or external resource access. External image URLs and source paths
escaping the report directory are rejected. The supplied `client` is a read-only report reader: it accepts only declared
inputs and maps moving references to their pinned IDs. Independently opened
clients retain their normal behavior. Report Python is trusted local code, not
a sandbox; external I/O is outside this reproducibility policy.

Failed builds keep their notebook and diagnostics and cannot be published.
Publication uses an ordinary `report/bundle` artifact value. Export preserves the
complete bundle; rebuilding its captured source uses the verified bundle's
exact input IDs. Copy creates editable source with exact references and a
`cairn-lock.json` that retains old reference spellings used inside code. Removing
that lock and changing declarations permits a deliberate refresh. Neither path
requires server-side execution or editing.

## Transfer and maintain a repository

Repository transfer is a direct administrative operation. Export on the source
machine, transfer the directory with your normal file-transfer tool, then import
on the destination server host using its storage configuration:

```sh
# Source machine: capture the local repository.
cairn --repo ./results repo export ./cairn-transfer

# Destination server host, after transferring cairn-transfer there:
cairn repo \
  --database-url 'postgresql+psycopg://cairn@db.example.org/cairn' \
  --s3-bucket cairn-artifacts --s3-prefix cairn/ --s3-region eu-central-1 \
  import ./cairn-transfer

cairn repo \
  --database-url 'postgresql+psycopg://cairn@db.example.org/cairn' \
  --s3-bucket cairn-artifacts --s3-prefix cairn/ --s3-region eu-central-1 \
  check --verify-blobs
```

For another SQLite/files destination, use
`cairn --repo ./restored repo import ./cairn-transfer`. Export requires a new
destination directory. Artifact, writer and record identities, version labels,
aliases, comments and dependencies are preserved. The destination keeps its own
repository identity. Conflicting data fails rather than merging silently;
reimporting identical data is idempotent. Imported producer handles are closed.
Live artifacts remain live under the same IDs and can attach new producers;
closing artifacts retain their exact finish intent and can complete it. Outbox
journals, authentication configuration and active producer sessions are not
transferred.

Export takes a consistent metadata snapshot while writers can continue; later
writes are outside that snapshot. Finish or stop producers for a final cutover.
There is no HTTP bulk-transfer job service or automatic repository replication.

```sh
cairn --repo ./results repo check --verify-blobs
cairn --repo ./results repo gc                     # Dry run
cairn --repo ./results repo gc --apply             # Apply eligible collection
```

Garbage collection observes retained references, upload/read leases, and a grace
period. It does not delete retained artifacts. Logical artifact deletion and
explicit physical purge are available through the backend/maintenance Python API;
purge refuses artifacts still referenced by retained evidence.

## Development and boundaries

```sh
uv sync --all-extras --group dev
uv run pytest
uv run python tools/count_python.py
```

The line-count check counts every physical line in `cairn/**/*.py`, including
comments and blank lines, and fails above 9,000 lines. Tests, documentation, and
tools are excluded from that runtime count. PostgreSQL integration tests use
`CAIRN_TEST_POSTGRES_URL`; inspect individual test prerequisites before running
against an existing service.

The API provides one artifact lifecycle, multiple producers, alias compare-and-swap,
typed values, comments, SQL-filtered record queries, streaming blobs and explicit
transfer/maintenance. Runs automatically capture client-side provenance. The
backend has one shared token and ordinal query ordering; no server report executor
or AI integration. Unknown schemas fail explicitly: repository schema **6**, HTTP
protocol **4**, durable outbox schema **3**. Typed-value codec envelopes remain
version 1. Earlier prototype schemas require explicit conversion. Names use
slash-separated namespaces; colon-style references are unsupported.

See [architecture and guarantees](docs/architecture.md) and the
[method contract](docs/CONTRACT.md) for details.

Implementation checks are recorded in [validation](docs/validation.md),
with reproducible measurements in [performance](docs/performance.md).

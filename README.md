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

[`examples/`](examples/) has small projects: a Hydra training sweep submitted
with Slurm, PBS, submitit or HyperQueue, a Make/Snakemake pipeline, a minimal report and a
report tour.

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

run = cairn.init("baseline", repo="./results", config={"learning_rate": 0.01})
cairn.log({"step": 0, "loss": 0.8})
cairn.log({"step": 1, "loss": 0.3})
cairn.update({"description": "Two recorded steps"})
publication = cairn.finish(value={"final_loss": 0.3})
assert publication["id"] == run.id
print(run.id, run.version)
```

`cairn.init()` installs a context-local default handle and returns that same
explicit handle. `log`, `track`, `add`, `update`, `get`, `find`, `use`,
`flush`, and `finish` also exist on the handle. Use explicit clients when working with
several repositories or runs:

```python
from cairn import Client

with Client("./results") as client:
    with client.run("comparison", config={"learning_rate": 0.02}) as run:
        baseline = run.use("runs/baseline@latest", role="baseline")
        run.log(step=0, loss=0.25)
        run.update({"baseline_loss": baseline.value["final_loss"]})
    print(run.id, run.version)
```

`cairn.run(...)` is the decorator form of `cairn.init()` with the same
arguments. Every call starts its own run, finishes it when the function returns
or records a failure and re-raises, and then restores the previous default
handle. Without `config=`, a Hydra/OmegaConf `DictConfig` first argument is
resolved and stored as the run config:

```python
@hydra.main(config_path="conf", config_name="config", version_base="1.3")
@cairn.run("train")
def main(cfg):
    cairn.log(loss=cfg.learning_rate)
```

For a standalone dataset or other artifact, no explicit client is needed:

```python
with cairn.artifact("datasets", "samples") as dataset:
    cairn.add(cairn.Table([{"sample": 0, "weight": 1.0}]), name="samples")
cairn.use(dataset, role="training-data")
```

The artifact inherits the current run's repository and project, or uses normal
discovery when no run exists. Optional `repo=` and `project=` override
these defaults. Inside the block, top-level calls target the artifact; exiting
restores the previous run. Use `cairn.finish(value=...)` inside the block to
set its main value explicitly. Otherwise it finishes automatically.
`add()` replaces the former Python `capture()` method; no alias is retained.
It stores named files or typed values, not environment metadata.

From the shell, `cairn add` does the same in one step. Each call publishes the
next version of the name with every given file or folder, named by its base name:

```sh
cairn add datasets/shapes data/shapes data/labels.csv --metadata '{"source": "survey"}'
cairn add models/resnet ckpt.pt --use datasets/shapes@v1
```

Large data that already sits where the repository can read it can be linked
instead of copied: `cairn add datasets/gonio /data/gonio --link`, or
`dataset.add(path, link=True)` in Python. The repository records each file's
location, size, modification time and SHA-256, hashing it in place, and keeps no
copy. Readers on other machines download it through the server like any other
file, and readers of a local repository use the files where they are:
`download()` creates symbolic links to them. The repository needs to read the
path: a local repository can link any file, a server only files under its
`--link-root` folders; otherwise the files are uploaded as a copy, with a
warning. Treat linked files as read-only: reading a changed or missing one raises
`IntegrityError`, `cairn repo check` lists them, and garbage collection never
deletes them. Export copies their bytes.

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
with cairn.init(repo="./results", capture_source=False) as run:
    run.log({"loss": 0.2})  # Environment/dependencies, without source or diffs.

with cairn.init(repo="./results", provenance=False) as run:
    run.log({"loss": 0.1})  # Only explicit metadata, records and captures.
```

Source archives exclude repository internals, common credential paths and symlinks. They
are bounded; provenance metadata records omissions and collection errors. Outside
Git, capture is limited to the entry script and known dependency files in the
working directory. Capture happens at initialization, so files changed later
need an explicit `run.add(...)`. No raw environment-variable dump is taken.
The archive records evidence; it does not promise a fully reproducible container.
Generic `client.start(...)` performs no automatic provenance capture.

Runs also store their console output. Standard output and error, including
output from extensions and subprocesses, are stored as they happen under
`console/` and still reach the terminal; a terminal stays a terminal, so colours
and progress bars are unchanged. The two streams are merged in arrival order.
Chunks are written every few seconds, less often as a run grows, and at finish
or interpreter exit. Storage stops after 64 MiB with a truncation note. Only one
run per process captures at a time. Everything printed is stored, so pass
`capture_output=False` to `cairn.init`, `cairn.run` or `Client.run` when output
may contain secrets. `reader.console()` returns the text, and the UI shows it in
a run's Console tab.

Internally, `start(project, namespace, name, request_id=...)` creates only
one live artifact and its first writer. Metadata follows as ordinary ordered
`metadata` entries. There is no separate experiment configuration column or
run-specific storage table.

## Find and read artifacts

Whatever a run or report finds or uses becomes its input, so a downstream run can
discover everything it depended on. Searches run in the repository's database, so
only their results travel.

```python
import cairn

with cairn.init("summary"):
    runs = cairn.find("runs/fit/*")                                    # latest of each name
    best = cairn.find("runs/fit/*", order="/value/loss", limit=1)[0]  # ranked in the database
    data = cairn.use("datasets/points", role="training-data")
    print(best.name, best.version, best.value["loss"])
    for record in best.records(fields=["/step", "/loss"]):
        print(record)
```

| Call | Returns | Recorded as inputs |
| --- | --- | --- |
| `cairn.find(pattern, where=None, order=None, limit=None)` | A list of readers | Every artifact it returns |
| `cairn.use(ref, role="input")` | One reader | That artifact, with the role |

References accept `namespace/name@latest`, `namespace/name@vN`,
`namespace/name@alias`, a full artifact ID, or an unambiguous hexadecimal ID
prefix of at least eight characters; omitting the selector means `latest`. A
pattern is a reference with shell-style wildcards: `*` matches within one level
of the name and `**` across levels. `runs/fit/*` is the latest version of every
name one level below `runs/fit/`, `runs/fit/**` of every name below it,
`runs/fit/*@*` every version, `runs/fit/*@best` each name's `best` alias and
`*/baseline` every namespace's latest `baseline`. Names cannot contain `*`.
Searches return finished artifacts only, sorted by name, with numbers in names
compared as numbers (`seed2` before `seed10`), unless `order` names a field:
`"/value/loss"` ascending, `"-/value/loss"` descending. `where` takes the
predicates described under [Annotate and find artifacts](#annotate-and-find-artifacts);
paths start with `/value`, `/metadata` or `/annotations`.

A reader has `id`, `project`, `namespace`, `name`, `version`, `outcome`,
`created_at`, `finished_at`, `annotations`, `value`, `config`, `metadata`,
`dependencies`, `inputs(role=...)` (the dependencies recorded with that role),
`files`, `file(name)`, `download()`, `console()` and
`records(where=None, fields=None)`, which streams the logged data page by page;
`str(reader)` is `runs/fit/cubic@v2`. Files and records are fetched only when
asked for.

Whatever `find` returns is recorded, because whatever the code learns from it can
shape the result; artifacts a `where` or `order` only compared in the database
are not. Like `cairn.log`, `cairn.find` and `cairn.use` belong to
the current run, artifact or report and raise without one; a run's own
`run.find` and `run.use` record to that run. A finished run can no longer record
and raises `Conflict`. To read without recording, use a `Client`, with
`client.get(ref)` and `client.find(pattern, ...)`:

```python
from cairn import Client, Table

with Client("./results") as client:
    dataset = client.start("datasets", "small-sample")
    dataset.finish(value=Table([{"x": 1, "y": 2}, {"x": 2, "y": 4}]))
    rows = client.get("datasets/small-sample").value.load()
    baseline = client.get("runs/baseline")
    print(rows, list(baseline.records(where={"path": "/loss", "op": "lt", "value": 0.5})))
```

Readers pin the exact finished artifact. Direct IDs are stable before and after
finish. Live inspection/querying uses the backend; SDK readers and report inputs
require finished artifacts. There is no revision or snapshot API. Starting again
under the same name creates a new artifact ID; finishing assigns the next label.

`File`, `Directory`, `Image`, `Array`, `Table`, and `Figure` wrap common media.
`add(path)` records a named file or directory snapshot. `track(value,
metadata=None, key="value")` writes a record containing a value. `TypedValue`
supports custom formats with explicit blob and artifact references; unknown
formats remain inspectable through `StoredValue`. No pickle-based object codec
is used.

Readers list added names with `reader.files` and return one as a value with
`reader.file(name)` (`.load()`, `.open()`, `.download()`). `reader.download(name=None,
to=None)` returns one path: the artifact folder, or the named file or folder in
it. Added items keep their names; final-value files sit at the top level. Without
`to`, the folder is `<cache>/artifacts/<id>/`. `<cache>` is `$CAIRN_CACHE_DIR`,
else `$XDG_CACHE_HOME/cairn` or `~/.cache/cairn`. Content is fetched once into
`<cache>/blobs/<sha256>`, verified, made read-only and hard-linked (or copied
across filesystems) into the target; copy a file before editing it.

Names under `provenance/` and `console/` are reserved for Cairn and rejected by
`add()`. They are not in `reader.files` or a whole-artifact download;
`reader.provenance` lists the former, and `reader.download("provenance")` or
`reader.download("console")` downloads them.

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
    for match in client.find("runs/*", where={"and": [
        {"path": "/annotations/tags", "op": "contains", "value": "paper"},
        {"path": "/metadata/config/learning_rate", "op": "lt", "value": 0.01},
    ]}):
        print(match, match.annotations)
    client.annotate(artifact["id"], {"review": "published", "title": None},
                    expected_etag=artifact["annotations_etag"])
```

`annotate` applies JSON Merge Patch: nested objects merge, arrays replace, and
`null` removes a key. `expected_etag` is optional; supply the fetched ETag when
editing existing information to detect concurrent changes. The repository applies
the patch atomically. A conflict preserves the newer data. Annotation edits do not
create artifact versions or change the captured manifest. Writer and reader handles
also expose `.annotate(patch, expected_etag=...)`.

`target="name"` annotates every version of a name, including later ones. Each
artifact's `annotations` are its name's properties overlaid key by key by its own,
so removing a version's key shows the name's value again:

```python
client.annotate("reports/direction", {"tags": ["paper"]}, target="name")
client.backend.annotation_layers(artifact["id"])  # {"name": ..., "artifact": ..., ...}
```

`find()` returns readers and fetches pages automatically; `limit` caps the total
results. Predicate paths start with `/value`, `/metadata` or `/annotations`;
`client.backend.list_artifacts` below also lists live artifacts. `contains` means exact
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

## Comments

Comments made in the UI anchor to an exact report version (source path, lines,
quote or media position) and show on every version of that report. An agent or
script can pull the open ones, address them and mark them resolved:

```sh
cairn comments list reports/direction           # open comments on every version
cairn comments list reports/direction --json    # full anchors for agents
cairn comments resolve c_123 --note "Rewrote report.qmd:42-44" --by agent
cairn comments reopen c_123
```

`--all` includes resolved comments and `--exact` limits the list to the referenced
version. Python offers `client.comments(ref, scope="name", include_resolved=False)`,
`client.resolve_comment(id, note=..., by=...)` and `client.reopen_comment(id)`.
The UI hides resolved comments unless "Show resolved comments" is on. Authors and
resolvers are free text; Cairn has no user accounts.

## Select a repository and authenticate

A project configures Cairn with a `.cairn.toml` file:

```toml
repo = "https://cairn.example.org"   # path or URL; relative paths resolve beside the file
project = "neural-rendering"         # default project for init, artifact, Client and the CLI
token_file = "~/.config/cairn/token" # optional; inline tokens are not accepted
```

Discovery walks from the working directory to its parents and merges every
`.cairn.toml`: the nearest file setting a key wins, so a subproject can override
only `project`. A `.cairn/` folder is data: its `repo/` is the repository unless
a `.cairn.toml` in the same directory sets `repo`.

Python clients and ordinary CLI commands select the repo in this order:

1. An explicit path/URL (`repo=`, `Client(...)`, or `--repo`).
2. `CAIRN_REPO`.
3. The nearest `repo` setting or `.cairn/` folder, a `.cairn.toml` shadowing the
   folder beside it.
4. `./.cairn/repo`.

The project is an explicit `project=`/`--project`, then `CAIRN_PROJECT`, then
the nearest `.cairn.toml` `project`, then `default`.

A path selects direct local storage. An HTTP(S) URL selects that server alone.
Remote reads query the server; they do not combine server results with pending
local writes. `server` uses the same repo discovery for local repositories;
if discovery selects a remote URL, it asks for an explicit `--repo` instead.
Administrative `repo` commands use direct storage configuration, with
`./.cairn/repo` as their local default.

Bearer tokens are discovered in this order: explicit `token=`/`--token`,
`CAIRN_TOKEN`, `CAIRN_TOKEN_FILE`, the project token, then
`~/.config/cairn/token`. The project token comes from the same level as the
discovered repo: its `.cairn.toml` `token_file`, or `.cairn/token` for a local
repo. A parent's credentials never pair with a subproject's own repo.

The server uses one shared bearer token. It creates `.cairn/token` with mode
`0600` when no token is found. Startup prints credential-free URLs. Clients reject base URLs
containing credentials, query parameters, or fragments. HTTPS termination and
service supervision are deployment responsibilities.

Remote Python writers stage immutable bytes and ordered events in a durable
outbox before `log()` returns. The default location is
`<state>/outbox/<repo-hash>`. `<state>` is `$CAIRN_STATE_DIR`, else
`$XDG_STATE_HOME/cairn` or `~/.local/state/cairn`. Keep it apart from the
disposable download cache: it may hold the only copy of unsent results.
`Client(outbox=...)` selects an explicit location. `flush(timeout=...)` waits for acknowledgements, and
`Client.recover()` reopens recorded writers after a restart. A journal is bound
to its repo URL and repository identity; it is delivery state, not another
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
interfaces. Startup prints URLs using the actual bound port. `--link-root FOLDER`
(repeatable) lets clients link files under that folder instead of uploading them;
without it, the server links nothing.

When the UI is installed, startup also prints login links with the server token
embedded in the URL fragment. They remain reusable across restarts until that
token changes. Opening the link signs the browser in.

A local SQLite/files deployment:

```sh
cairn --repo /srv/cairn/repository server --host 127.0.0.1 --port 8765
```

The repository contains `metadata.sqlite3` and an `objects/` directory. The token
file belongs to the nearest project directory with `.cairn/` or `.cairn.toml`
(otherwise the working directory's `.cairn`) unless supplied through
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
cairn --repo https://cairn.example.org info
cairn --repo https://cairn.example.org artifact list --namespace runs
```

## Use the CLI

Global repo/project/token flags precede the command. JSON inputs accept
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
Browser sessions end after 7 days without use. Use `--no-ui` for API-only operation or
`--no-auth` for local development without a token. Reports open directly with
a table of contents, interactive figures and media, source copying, and comments
that carry across reports with the same project and name and can be resolved.
Artifact/run pages include lineage, records, files, editable tags for one version
or all versions, and click-to-copy IDs.

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

A report reads artifacts like a run does, and every artifact it reads becomes
an input in its lineage:

````markdown
---
title: Baseline analysis
---

## Results {#sec-results}

```{python}
import cairn

baseline = cairn.use("runs/baseline")
print(baseline.value)
```
````

`cairn.find` and `cairn.use` read the repository the report is built
from and record inputs as in a run. A reference resolves once per build, so
`@latest` gives the same version throughout a build. The manifest lists every
artifact the report read with the reference or pattern that found it. A rebuild runs the
code again and resolves again; write `@vN` or an ID to always read one version.
Front-matter `cairn.inputs` (`baseline: runs/baseline@latest`) resolves several
inputs together before any cell executes and exposes them as `inputs["baseline"]`.

A report can be configured with Hydra. The front matter `config` is the primary
config, `cairn.config_path` optionally names a folder of config groups, and the
build's overrides apply on top:

````markdown
---
title: Ablation
config:
  defaults:
    - model: resnet
    - _self_
  alpha: 0.1
cairn:
  config_path: conf
---

```{python}
print(cfg.model.name, cfg.alpha)
```
````

```sh
cairn report build ablation.qmd model=vit alpha=0.5
cairn report build ablation.qmd --publish -m alpha=0.1,0.2,0.3
```

Cells see the composed config as `cfg`. The resolved config is stored in the
manifest and in the published artifact's metadata `config`, like a run's config,
and the config folder is captured with the sources. `-m` builds once per
combination of Hydra sweep values (`a=1,2`, `range()`, `choice()`), into
numbered subfolders of `--output`; with `--publish` each becomes a version.
Hydra's runtime (the `hydra:` node, output directories, logging, `${hydra:...}`)
and sweeper plugins are not used.

The builder
captures source/includes and declared local resources, starts a fresh Python
kernel, and emits HTML, native document JSON, an executed notebook, assets,
source mappings, and a manifest with exact dependency IDs and package versions.
The default `python3` kernel uses the builder's Python environment; `--kernel`
selects another installed kernel. Code runs locally in a copy of captured files.
It is trusted Python code, not a security sandbox.

The implemented QMD/Markdown subset covers:

- **Markdown:** tables, footnotes, dollar math rendered as MathML, sub- and
  superscript (`H~2~O`, `x^2^`), definition and task lists, and attributes
  (`![A](a.png){width=50% .wide}`, `[text]{.class}`, `` `code`{.py} ``).
- **Python:** `{python}` cells with the options `label`, `fig-cap`, `tbl-cap`,
  `fig-subcap`, `echo`, `eval`, `output`, `layout-ncol`, `column` and
  `cap-location`; `execute.echo`/`execute.eval` in the front matter; and inline
  expressions `` `{python} f"{loss:.3f}"` ``, evaluated in document order.
- **Labels and references:** `{#sec-name}`, `{#fig-name}`, `{#tbl-name}` and
  `$$...$$ {#eq-name}`, referenced as `@fig-name`. `@reports/baseline#fig-loss`
  (or `@reports/baseline@v3#fig-loss`) refers to a label in another published
  report, and `{{< embed reports/baseline#fig-loss >}}` shows that report's
  labelled cell outputs. Both resolve once per build and become inputs.
- **Fenced divs,** which may contain Python cells: callouts
  (`::: {.callout-note}`), `.columns` with `.column width="40%"`,
  `.panel-tabset` (one tab per heading), `.column-margin`, `.column-page`,
  `.column-screen`, and figures `::: {#fig-name layout-ncol=2}` or
  `layout="[[2,1],[1]]"` whose last paragraph is the caption. Images and cells
  inside are lettered subfigures, so a labelled `{#fig-left}` inside reads
  "Figure 1(a)".
- **Citations:** `bibliography: refs.bib` (a file or a list) and `[@key]`,
  `[see @a; @b, p. 3]`, `[-@key]` or `@key`. `citation-style` is `author-year`
  (default) or `numeric`. Cited entries are listed under "References" at the
  end, or inside `::: {#refs}`.
- **Viewers:** `![Scene](viewer.html){#fig-scene height=480}` embeds an HTML
  file of the report as an interactive viewer. It may load scripts from https
  URLs (three.js, WebGL, WebGPU, WebAssembly, workers) and fetch the report's
  other files by relative path, such as `fetch("mesh.glb")` for a file listed
  in `cairn.resources`. In the UI it runs in a sandbox without access to Cairn
  or the reader's session, and reads files through a signed link scoped to
  that report.

`cairn.resources` lists additional relative files/directories. Includes use
`{{< include relative.qmd >}}`. See [the example](examples/minimal-report/report.qmd).

This is a QMD subset, not full Quarto. It does not implement CSL styles,
arbitrary Quarto extensions, other shortcodes, other execution languages, or
Markdown raw HTML. Rich HTML output of cells is placed in an iframe without
scripts or external resource access. External image URLs and source paths
escaping the report directory are rejected. Reading a live artifact fails.
`cairn.Client()` opens the repository the report is built from; explicit clients
are not recorded, and inside a run started by a cell, reads record to that run. Report Python is trusted local code, not
a sandbox; external I/O is outside this reproducibility policy.

Failed builds keep their notebook and diagnostics and cannot be published.
Publication uses an ordinary `report/bundle` artifact value. Export preserves the
complete bundle, and copy extracts its editable source unchanged. Rebuilding
either resolves inputs again. Neither path requires server-side execution or
editing.

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
aliases, annotations, comments and dependencies are preserved. The destination keeps its own
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
```

## Remove artifacts and collect garbage

`remove` soft-deletes one exact artifact: the reference resolves to an ID first.
It disappears from listings and names; `@latest` falls back to the highest
remaining finished version. Artifacts that still depend on it keep it readable by
ID, and `retained_by` lists them. `gc` purges artifacts removed before the grace
period that nothing else references, then content no artifact references. It is a
dry run unless applied, and runs on the server when the repo is a URL.

```sh
cairn remove "runs/training@latest"
cairn gc                                           # Dry run
cairn gc --apply --grace-seconds 3600
```

```python
client.remove("runs/training@v2")                  # or cairn.remove(...)
client.gc(apply=True)
```

`cairn repo gc` runs the same collection directly against storage, for
`--database-url` and S3 configurations. Collection also observes upload/read
leases.

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
transfer/maintenance. Runs automatically capture client-side provenance and console output. The
backend has one shared token and ordinal query ordering; no server report executor
or AI integration. Unknown schemas fail explicitly: repository schema **7**, HTTP
protocol **4**, durable outbox schema **3**. Typed-value codec envelopes remain
version 1. A schema 6 repository opens again after `cairn repo export` (a backup)
and `cairn --repo PATH repo migrate`, which converts it in one transaction; use
`--database-url` with `--objects` or `--s3-bucket` for PostgreSQL. Names use
slash-separated namespaces; colon-style references are unsupported.

See [architecture and guarantees](docs/architecture.md) and the
[method contract](docs/CONTRACT.md) for details.

Implementation checks are recorded in [validation](docs/validation.md),
with reproducible measurements in [performance](docs/performance.md).

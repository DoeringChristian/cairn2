# Cairn2

Cairn stores experiments, files, datasets and models using one artifact
lifecycle, and reports that plan and explain them. Choose one repository: a local SQLite database with files, or an HTTP
server backed by SQLite or PostgreSQL and a filesystem or S3 object store.
An artifact keeps one ID throughout its lifetime: writable while live, immutable
once finished. Version labels and aliases point directly to artifacts.

References use `namespace/name@selector`: `runs/training@latest`,
`artifacts/model@v3`, or `datasets/shapes@v2`. The prefix is an arbitrary namespace,
not an object type. All of them are artifacts; typed value formats
determine how their contents are interpreted and rendered. Names can include
further slashes, as in `models/training/checkpoint@latest`.

This repository contains the Python API, storage implementation, authenticated
HTTP API, CLI, and the browser UI with its report editor.

[`examples/`](examples/) has small projects: a Hydra training sweep submitted
with Slurm, PBS, submitit or HyperQueue, a Make/Snakemake pipeline, and a report tour.

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
HTTPX. `server` adds FastAPI/Uvicorn; `ui` adds the browser UI; `media` adds NumPy, Pillow, Arrow, Plotly, and Matplotlib;
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

Whatever a run finds or uses becomes its input, so a downstream run can
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
the current run or artifact and raise without one; a run's own
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
finish. Live inspection/querying uses the backend; SDK readers and run inputs
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

Large path-backed files and directories use [paged datasets](docs/datasets.md)
automatically: bounded chunk uploads, durable resume checkpoints, and selective
reads on other machines. A directory's `value.iter_files()` lists its files
lazily; `reader.file("images/frame.png")` reads one file from an `images` capture.
Chunked downloads use a separate, evictable cache and assemble verified files in
the destination. Both client and server must support `paged_datasets`; existing
artifacts and shared-path `link=True` captures keep their existing representation.

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
client.annotate("runs/training", {"tags": ["paper"]}, target="name")
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

Comments made in the UI anchor to a report passage (part, lines, quote) or a
figure, or to an exact artifact version. An agent or script can pull the open ones,
answer, address them and mark them resolved:

```sh
cairn comments list 'reports/**'                # open comments on every report
cairn comments list 'reports/**' --json         # full anchors for agents
cairn comments list                             # open comments in the whole project
cairn comments reply c_123 "Narrowed the sweep" --by agent
cairn comments resolve c_123 --note "Now 5 learning rates" --by agent
cairn comments reopen c_123
cairn comments watch 'reports/**'               # threads as they are sent to an agent
```

Replies form a thread under the comment they answer; resolving, reopening or
deleting the first comment covers its replies. In the UI, Enter or the send button queues a message for the agent; Shift+Enter
inserts a newline. Clicking away saves an editable draft without sending it,
including for replies. Sent messages are read-only in the floating conversation.
`cairn comments watch` prints each open thread whenever a comment in it is sent,
so an agent running it in the background is woken by every comment you finish;
`--json` prints each batch as one line. `--once` waits for threads with a comment sent after
their last reply, prints them and exits, for agents that are woken when a command ends.

`--all` includes resolved comments and `--exact` limits the list to the referenced
version. Python offers `client.comments(ref=None, scope="name", include_resolved=False)`,
`client.reply_comment(id, body, by=...)`, `client.send_comment(id)`,
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
outbox before `log()` returns. Large path-backed dataset values instead upload
bounded chunks synchronously before queuing their small reference; they do not
stage the whole dataset in the outbox. The default outbox location is
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
`artifacts`. Both accept `--namespace`.
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
`--no-auth` for local development without a token. Reports are the project's
start page: Markdown you edit in place, where only the block under the cursor shows its source, with the agent's answers, figures
and media from artifacts, a table of contents, source copying, and comments.
Artifact/run pages include lineage, records, files, editable tags for one version
or all versions, and click-to-copy IDs. The project Workspace displays scalar,
image, video, and Plotly cards directly from run records, with comparisons, galleries,
axis sliders, resizable sections, and browser-local saved layouts. See the
[training card tour](examples/training/README.md#workspace-card-tour).

The UI source lives in [ui/](ui/README.md). Rebuild the bundled assets after
changes with `npm ci --prefix ui && npm run build --prefix ui`.

## Reports

A report is where you write down a project: its goal, the direction, the
experiments to run and what to show. It is Markdown, edited in the browser beside its
rendering, and it runs no code. You leave the agent room with fenced divs; the agent
answers in its own block below your text, never in it:

```markdown
# Thin-shell SDF

Goal: beat the baseline on thin features without losing training speed.

::: {.agent #baseline}
Train the baseline at 3 learning rates. Report final Chamfer and wall time.
:::
```

Figures, video, audio and values come from artifacts, never from the report:
`![Error on the test mesh](cairn:runs/train@latest#plots/err.png)` shows the file
captured as `plots/err.png` by the latest `runs/train`, and `![](cairn:runs/train@v3)`
shows that version's value. Reports also render tables, math, callouts
(`::: {.callout-warning}`), code and footnotes.

A viewer is an artifact's HTML file; agents write them, for example a three.js mesh
viewer published as `viewers/mesh` with its libraries beside `index.html`. A `view`
block shows one with settings:

````markdown
```view
viewer: cairn:viewers/mesh@v1#index.html
caption: Lucy decimated to 250k and 50k triangles
up: z
meshes:
  - label: 250k triangles
    file: cairn:datasets/lucy@v2#lucy-250k.obj
  - label: 50k triangles
    file: cairn:datasets/lucy@v2#lucy-50k.obj
```
````

`viewer` and `caption` are reserved; every other key is the viewer's own setting. The
viewer runs in a sandboxed frame that cannot reach the report or the session, and
reads its settings as JSON from its URL fragment
(`JSON.parse(decodeURIComponent(location.hash.slice(1)))`), each `cairn:REF#file`
string replaced by a link to that file. It loads its own files by relative path and
may post `{type: "cairn-html-size", height}` to its parent. Without settings,
`![](cairn:viewers/mesh@v1#index.html)` shows a viewer directly, and a workspace
Viewer card takes the same YAML, so a figure and a card are interchangeable.

Each agent block shows only the agent's result; its request and a conversation with
the agent fold away below it. That conversation is the block's comments without a
quote: `cairn report comment study "…" --part baseline` adds to it, `report watch`
wakes on what you send there, and `cairn comments reply` answers.

A report is not an artifact: it has no versions or lineage, but every saved text of
each part is kept as a revision, and an edit made against an older revision is
refused instead of overwriting. Agents work on reports by name:

```sh
cairn report list
cairn report new study body.md                  # or create it in the UI
cairn report show study                         # every part with its revision
cairn report write study baseline answer.md --by agent   # or - for standard input
cairn report comment study "Three rates may miss the optimum" \
  --quote "Train the baseline at 3 learning rates" --by agent
cairn report watch study --after 4              # wait for edits or sent comments
cairn report delete study
```

`report watch` prints the report and its waiting threads as one JSON line and
exits when the body passes revision `--after` (by default the current one) and has
rested for `--settle` seconds (30), or when a thread has a comment sent after its
last reply, so an agent running it in the background is woken by both. Python
offers the same through `client.reports()`, `client.report(name)`,
`client.create_report(name, body)`, `client.write_report(name, part, source,
expected_revision=..., by=...)`, `client.comment_report(name, body, quote=...)` and
`client.delete_report(name)`. Analyses that used to run inside a report are runs
now: they record what they read and log their figures, which the report references.

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
client.remove("runs/training@v2")  # or cairn.remove(...)
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
backend has one shared token and ordinal query ordering; no code execution
or AI integration. Unknown schemas fail explicitly: repository schema **8**, HTTP
protocol **4**, durable outbox schema **3**. Typed-value codec envelopes remain
version 1. A schema 6 or 7 repository opens again after `cairn repo export` (a backup)
and `cairn --repo PATH repo migrate`, which converts it in one transaction; use
`--database-url` with `--objects` or `--s3-bucket` for PostgreSQL. Names use
slash-separated namespaces; colon-style references are unsupported.

See [architecture and guarantees](docs/architecture.md) and the
[method contract](docs/CONTRACT.md) for details.

Implementation checks are recorded in [validation](docs/validation.md),
with reproducible measurements in [performance](docs/performance.md).

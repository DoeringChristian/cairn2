# Cairn

Cairn logs runs, stores files, datasets and models, and finds them again quickly. A
repository is a folder; a server shares it over HTTP. The browser explorer, `cairn-ui`, is
a separate program that reads either.

- [`docs/CONTRACT.md`](docs/CONTRACT.md): what programs and repositories can rely on.
- [`examples/`](examples/): a Hydra sweep for Slurm, PBS and HyperQueue, a Make/Snakemake
  pipeline, and a tour of media and viewers.

## Install

Python 3.11 or newer, on Linux or macOS. From this checkout:

```sh
python -m pip install -e '.[all]'
```

Logging alone needs no extra. `server`, `ui` and `media` (images, arrays, figures) are the
parts of `all`.

## Log a run

```python
import cairn

with cairn.run("training/mlp", config={"lr": 1e-3}) as run:
    data = run.use("datasets/shapes")                 # recorded as an input
    for step in range(100):
        run.log(step=step, loss=1 / (step + 1))
    run.log({"prediction": cairn.Image(pixels), "mesh.ply": cairn.File("out.ply")})
    run.add("checkpoints")                            # a folder, as one value
```

- `log` is the only way to write; `add` and `use` are shorthand for it. Logging a key again
  replaces its value; the earlier records remain.
- Values are null, booleans, integers, floats (with NaN), text, bytes, lists, maps, files,
  references, and `Image`, `Array`, `Table`, `Figure` (Matplotlib or Plotly) and `Directory`.
- The run finishes when its `with` or the program ends: `completed`, `failed` after an error,
  `cancelled` after Ctrl-C or SIGTERM. Finishing gives it the next version, `runs/training/mlp@v1`.
- A run also keeps what it printed and what made it: Git commit, uncommitted changes, source
  files, packages and command. Turn that off with `console=False`, `provenance=False`, or
  `capture_source=False` (commit and packages, but no code).
- `cairn.artifact("datasets", "shapes")` makes any other artifact; namespaces are yours.
- A file may change once `log` returns. `File(path, immutable=True)` promises it stays
  unchanged until the artifact is finished and saves a copy; `link=True` leaves it where it
  lies for as long as the artifact exists.

`cairn.Client(repo, project=...)` holds a connection for several artifacts. Another process
logs to a live run with `client.attach(run.id)`; finishing waits until it leaves.

## Read and find

```python
with cairn.Client() as client:
    run = client.get("runs/training/mlp@v1")
    run["loss"], run.config, run.outcome               # current values
    run.series("loss", x="step")                       # pairs, quickly, also of a million records
    run["prediction"].array(); run.read("mesh.ply"); run.download("copy")
    for ordinal, record in run.follow(): ...           # records as they arrive
    client.find("runs/training/**", where=["min(loss) < 0.1", "_config/lr = 0.001", "tag:paper"], order="-psnr", values=["psnr"])
    client.files(text="mesh")                          # files of every artifact
```

References are `namespace/name@selector` or an ID. The selector is `v3`, an alias, `live`, or
`latest` (the newest completed version), which is the default. Anything can be read; `use` and
`Ref` take completed artifacts only, or any finished one with `incomplete=True`, such as the
cancelled run whose checkpoint a new one continues.

Tags describe artifacts (`client.tag(run, "paper", topic="sdf")`). An alias points at one
version of a name (`client.alias(run, "best")`).

## From the shell

```sh
cairn find 'runs/**' --where 'loss < 0.1' --show loss
cairn show runs/a;  cairn records runs/a;  cairn console runs/a;  cairn files --text mesh
cairn add datasets/scan scans/ --link;  cairn download datasets/scan copy/
cairn tag runs/a paper topic=sdf;  cairn alias runs/a best;  cairn links datasets/scan
ID=$(cairn start runs/job lr=0.1);  cairn log $ID step=1 loss=0.5;  cairn finish $ID
cairn stop runs/a@live;  cairn delete runs/a@v1;  cairn check;  cairn collect
```

## Settings

`.cairn.toml`, the nearest one upward, or the environment:

| Key | Variable | |
|---|---|---|
| `repo` | `CAIRN_REPO` | a folder (`.cairn/repo` without) or a server's URL |
| `project` | `CAIRN_PROJECT` | `default` without |
| `token_file` | `CAIRN_TOKEN` | the file, or the token; `.cairn/token` is found by itself |
| `provenance`, `capture_source` | `CAIRN_PROVENANCE`, `CAIRN_CAPTURE_SOURCE` | `false` turns them off |
| `outbox_gb` | | what may wait for a server, per process; 20 without |
| | `CAIRN_STATE_DIR` | where it waits; `~/.local/state/cairn` without |

## Server

```sh
cairn server --host 0.0.0.0        # writes .cairn/token when there is none
cairn-ui                           # the explorer of .cairn/repo, at http://127.0.0.1:8766
cairn-ui --repo http://server:8765 # or of a server
```

Programs log through an outbox on disk, so a slow or absent server does not stop them. When
the server is away and the outbox is full, `log` raises `cairn.Full`. `cairn recover` sends
what a killed program left behind. Put HTTPS in front of a server that others reach.

## Develop

```sh
uv sync --extra all
uv run pytest -q                             # tests
uv run ruff check --select F,B,I .
uv run python tools/benchmark.py             # what must stay fast
uv run python tools/ui_schema.py             # after changing cairn.proto
uv run --no-project --python 3.12 --with grpcio-tools==1.62.3 --with 'setuptools<80' \
  python -m grpc_tools.protoc -I cairn --python_out=cairn cairn/cairn.proto
```

That compiler is old on purpose: its code runs with protobuf 4.21 to 7.
[`ui/README.md`](ui/README.md) covers the explorer.

# Training

Fits a polynomial to noisy samples of a sine wave by gradient descent. It is plain
Python, so it runs anywhere.

```sh
python data.py                                                 # publishes datasets/points
python train.py                                                # one run: runs/fit/linear-lr0.1/seed0
python train.py -m model=linear,cubic learning_rate=0.03,0.3   # four runs
python train.py -m model=cubic learning_rate=0.3 seed=1,2,3,4  # four more seeds of one of them
cairn report build report.qmd --publish
```

Each configuration and seed has its own run name, `fit/<model>-lr<rate>/seed<seed>`.
Running the same one again, for example after fixing a bug, publishes its next
version, which replaces the previous one in every search. Seeds are separate runs
side by side, not versions of one run.

The report finds the latest version of every run under `runs/fit/` with
`cairn.find("runs/fit/**")`, where `**` reaches every level below `runs/fit/`. It
groups the runs by configuration and shows the mean and spread of the loss over
the seeds. It picks the best single run with
`cairn.find(..., order="/value/loss", limit=1)`, which the database evaluates, and
compares it with a baseline it reads with `cairn.use("runs/fit/linear-lr0.03/seed0")`.
Its last section finds the seeds of one configuration with
`cairn.find("runs/fit/cubic-lr0.3/*")`. Every run the report found or used is
recorded as an input. Build it for a subset with an override:
`cairn report build report.qmd 'runs=runs/fit/cubic*/*'`.

The Hydra config is in `conf/`. Cairn stores the resolved config and the console
output with every run, so Hydra writes no output folder for single runs and keeps
its sweep files under `build/`.

## Submitting jobs

`jobs/sweep.txt` holds one set of overrides per line. Run from this folder:

| Tool | Command |
| --- | --- |
| xargs, this machine | `jobs/local.sh 4` |
| Hydra, this machine | `python train.py -m model=linear,cubic learning_rate=0.03,0.3` |
| Slurm, one job | `sbatch jobs/slurm.sbatch model=cubic learning_rate=0.3` |
| Slurm, job array | `sbatch --array=1-$(wc -l < jobs/sweep.txt)%2 jobs/slurm-array.sbatch` |
| Slurm through Hydra | `python train.py -m hydra/launcher=slurm model=linear,cubic learning_rate=0.03,0.3` |
| PBS Pro, job array | `qsub -V -J 1-$(wc -l < jobs/sweep.txt) jobs/pbs.sh` |
| HyperQueue | `hq submit --each-line jobs/sweep.txt --env CAIRN_REPO=$CAIRN_REPO jobs/hyperqueue.sh` |

The Hydra launcher (`conf/hydra/launcher/slurm.yaml`) needs
`pip install hydra-submitit-launcher`; `hydra/launcher=submitit_local` runs the
same sweep on this machine.

### Logging from a cluster

Local runs write to `.cairn/repo`, a SQLite database. Cluster jobs log to a Cairn
server instead: jobs on different nodes would share one database file over a
network filesystem, and SQLite's locking is not reliable there.

1. Start a server that the nodes can reach, for example `cairn server --host 0.0.0.0`.
2. Copy its token to `~/.config/cairn/token` on the cluster, readable only by you.
3. `export CAIRN_REPO=https://cairn.example.org`, then publish the dataset there
   with `python data.py` and submit. Slurm passes the environment on by default,
   PBS with `-V`, and HyperQueue with `--env`.

`jobs/env.sh` gives each job its own outbox on node-local disk. The outbox holds
results until the server has acknowledged them, and `finish()` waits for that, so
a job ends only once its results are on the server. The default outbox lives in
the home folder, which is a network filesystem on most clusters.

Runs show up in the server's UI while they log, with their console output, so
the scheduler's log files only matter for jobs that fail before Cairn starts.

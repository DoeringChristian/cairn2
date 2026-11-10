# Examples

| Example | Shows |
| --- | --- |
| [`report-showcase/`](report-showcase/) | A run with media and a report that plans it and shows its results |
| [`training/`](training/) | A Hydra project: a dataset, one run per configuration and seed, sweeps, and the same sweep submitted with Slurm, PBS, submitit and HyperQueue |
| [`pipeline/`](pipeline/) | Stages that pass exact artifact versions to each other, run by Make or Snakemake |

Each folder is a project with a `.cairn.toml`, keeping its repository in its own
`.cairn/` folder. Run the commands from the project folder in an environment with
the `all` extra, for example `uv run --extra all python data.py`.

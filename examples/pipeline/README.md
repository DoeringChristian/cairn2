# Pipeline

Three stages: `prepare.py` publishes `datasets/points`, `fit.py` fits a line to
it, and `evaluate.py` measures the fit. Each prints the ID of the version it
published. The next stage gets that ID as an argument and records it with
`cairn.use`, and the report gets all three as config overrides.

```sh
make                    # or: snakemake --cores 1
```

Make and Snakemake rerun a stage when its script or an upstream ID file changes:
`touch fit.py && make` reruns the fit, the evaluation and the report. The ID files
in `build/` are all they keep. The data, runs and report live in Cairn, and the
Lineage tab of each shows the exact versions it read.

Any workflow tool can drive the stages this way, because they communicate only
through artifact IDs.

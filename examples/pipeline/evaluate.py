"""Measure a fitted line's error on a dataset. Usage: python evaluate.py FIT DATASET"""

import math
import sys

import cairn

with cairn.run("evaluate") as run:
    model = run.use(sys.argv[1], role="model").last("slope", "intercept")
    rows = run.use(sys.argv[2], role="evaluation-data")["points"].load()
    errors = [row["y"] - model["slope"] * row["x"] - model["intercept"] for row in rows]
    for row, error in zip(rows, errors, strict=True):
        run.log(x=row["x"], residual=error)
    run.log(rmse=math.sqrt(sum(e * e for e in errors) / len(errors)))
print(run.id)

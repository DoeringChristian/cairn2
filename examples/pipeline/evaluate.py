"""Measure a fitted line's error on a dataset. Usage: python evaluate.py FIT DATASET"""

import math
import sys

import cairn

run = cairn.init("evaluate")
model = cairn.use(sys.argv[1], role="model").value
rows = cairn.use(sys.argv[2], role="evaluation-data").value.load()
errors = [row["y"] - model["slope"] * row["x"] - model["intercept"] for row in rows]
for row, error in zip(rows, errors):
    cairn.log(x=row["x"], residual=error)
cairn.finish(value={"rmse": math.sqrt(sum(e * e for e in errors) / len(errors))})
print(run.id)

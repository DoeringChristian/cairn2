"""Fit a line to a dataset by least squares. Usage: python fit.py DATASET"""

import sys

import cairn

run = cairn.init("fit")
rows = cairn.use(sys.argv[1], role="training-data").value.load()
n = len(rows)
mean_x = sum(row["x"] for row in rows) / n
mean_y = sum(row["y"] for row in rows) / n
slope = sum((row["x"] - mean_x) * (row["y"] - mean_y) for row in rows) / sum(
    (row["x"] - mean_x) ** 2 for row in rows
)
cairn.finish(value={"slope": slope, "intercept": mean_y - slope * mean_x})
print(run.id)

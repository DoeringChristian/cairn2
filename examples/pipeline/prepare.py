"""Publish noisy samples of a line as datasets/points and print the version's ID."""

import random

import cairn

random.seed(0)
rows = []
for _ in range(100):
    x = random.uniform(0, 10)
    rows.append({"x": x, "y": 2 * x + 1 + random.gauss(0, 1)})
with cairn.artifact("datasets", "points") as dataset:
    cairn.finish(value=cairn.Table(rows))
print(dataset.id)

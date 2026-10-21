"""Publish noisy samples of a sine wave as datasets/points."""

import math
import random

import cairn

random.seed(0)
rows = []
for _ in range(200):
    x = random.uniform(-1, 1)
    rows.append({"x": x, "y": math.sin(3 * x) + random.gauss(0, 0.1)})
with cairn.artifact("datasets", "points") as dataset:
    cairn.finish(value=cairn.Table(rows))
print(dataset.id)

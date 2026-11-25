"""Run an experiment that records metrics, media, a dataset and a viewer.

Run from this folder: uv run --extra all python run.py
Then: uv run --extra all cairn server --no-auth
"""

import math
from pathlib import Path

import cairn

ASSETS = Path(__file__).parent / "assets"


def main():
    project = "neural-rendering"
    cairn.init("showcase-baseline", project=project, config={"seed": 42, "samples": 32})

    with cairn.artifact("datasets", "showcase-samples") as dataset:
        cairn.add(
            cairn.Table([{"sample": n, "weight": 1 / 32} for n in range(32)]),
            name="samples",
        )
    cairn.use(dataset, role="training-data")

    for step in range(60):
        loss = round(math.exp(-step / 18) + 0.02, 6)
        cairn.log({"loss": loss}, step=step)
        if step % 20 == 0:
            print(f"step {step}: loss {loss}")
    for name, file in (
        ("figures/field-start.png", "field-start.png"),
        ("figures/field-end.png", "field-end.png"),
        ("media/wave-evolution.mp4", "wave-evolution.mp4"),
        ("media/signal.wav", "signal.wav"),
    ):
        cairn.add(ASSETS / file, name=name)
    cairn.finish(value={"loss": 0.018, "psnr": 34.2, "converged": True})

    with cairn.artifact("viewers", "field", project=project):
        cairn.add(ASSETS / "field-viewer.html", name="index.html")
    print(f"Run: showcase-baseline (project: {project})")


if __name__ == "__main__":
    main()

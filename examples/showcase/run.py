"""Run an experiment that logs metrics, media, a dataset and a viewer.

Run from this folder: uv run --extra all python run.py
Then: uv run --extra all cairn-ui --no-auth
"""

import math
from pathlib import Path

import plotly.graph_objects as go

import cairn

ASSETS = Path(__file__).parent / "assets"


def main():
    with cairn.Client(project="neural-rendering") as client:
        with client.artifact("datasets", "showcase-samples") as dataset:
            dataset.log(samples=cairn.Table([{"sample": n, "weight": 1 / 32} for n in range(32)]))
        with client.run("showcase-baseline", {"seed": 42, "samples": 32}) as run:
            run.use(dataset, role="training-data")
            for step in range(60):
                loss = round(math.exp(-step / 18) + 0.02, 6)
                run.log(step=step, loss=loss)
                if step % 20 == 0:
                    print(f"step {step}: loss {loss}")
            run.log({"curve": cairn.Figure(go.Figure(go.Scatter(y=[1, 4, 2])))})
            for file, name in (
                ("field-start.png", "figures/field-start.png"),
                ("field-end.png", "figures/field-end.png"),
                ("wave-evolution.mp4", "media/wave-evolution.mp4"),
                ("signal.wav", "media/signal.wav"),
            ):
                run.add(ASSETS / file, name)
            run.log(psnr=34.2, converged=True)
        with client.artifact("viewers", "field") as viewer:
            viewer.add(ASSETS / "field-viewer.html", "index.html")
        print(f"Run: {run.ref} (project: {client.project})")


if __name__ == "__main__":
    main()

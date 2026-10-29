"""Log a small, synthetic workspace tour through the existing Cairn SDK.

From the repository root:
    uv run --extra all python examples/training/workspace_demo.py

The default destination is the running training-example server on port 8765.
Repeated invocations create new run versions; existing experiments are untouched.
"""

import argparse
import math

import numpy as np
import plotly.graph_objects as go
from PIL import Image, ImageDraw

import cairn


def reconstruction(step, model, angle):
    """A synthetic scalar field reconstructed with increasing spatial detail."""
    width, height = 480, 300
    y, x = np.mgrid[-1:1:complex(height), -1.6:1.6:complex(width)]
    theta = math.radians(angle)
    u, v = x * math.cos(theta) - y * math.sin(theta), x * math.sin(theta) + y * math.cos(theta)
    target = np.exp(-((u + 0.45) ** 2 / 0.22 + (v + 0.12) ** 2 / 0.28))
    target += 0.72 * np.exp(-((u - 0.45) ** 2 / 0.10 + (v - 0.24) ** 2 / 0.10))
    progress = 1 - math.exp(-step / (18 - model * 4))
    field = np.clip(target * (0.25 + 0.75 * progress) + (1 - progress) * 0.25 * (1 + np.sin(6 * u) * np.cos(5 * v)), 0, 1)
    paper, teal, ochre = np.array([245, 242, 233]), np.array([23, 107, 91]), np.array([209, 160, 84])
    rgb = paper[None, None, :] * (1 - field[..., None]) + teal[None, None, :] * field[..., None]
    highlight = np.clip((field - 0.65) / 0.35, 0, 1)[..., None]
    rgb = rgb * (1 - highlight) + ochre * highlight
    image = Image.fromarray(rgb.astype(np.uint8))
    draw = ImageDraw.Draw(image)
    draw.text((14, 13), f"SYNTHETIC RECONSTRUCTION   /   CAMERA {angle:+d}°", fill=(32, 38, 33))
    draw.text((14, height - 23), f"STEP {step:02d}   ·   DETAIL {progress:.0%}", fill=(32, 38, 33))
    return image


def figure(step, model):
    x = np.linspace(-2, 2, 81)
    target = 0.3 * x**3 - 0.8 * x + 0.25
    error = math.exp(-step / (15 - model * 3))
    prediction = target + error * (0.5 * np.sin(x * 3) + (2 - model) * 0.12 * x)
    fig = go.Figure([
        go.Scatter(x=x.tolist(), y=target.tolist(), mode="lines", name="Target", line={"color": "#b56231", "dash": "dot", "width": 1.5}),
        go.Scatter(x=x.tolist(), y=prediction.tolist(), mode="lines", name="Prediction", line={"color": "#176b5b", "width": 2}),
    ])
    fig.update_layout(template=None, xaxis={"title": "x", "range": [-2, 2], "gridcolor": "#d9d8ce"}, yaxis={"title": "response", "range": [-1.5, 2], "gridcolor": "#d9d8ce"}, legend={"orientation": "h", "y": -0.3}, margin={"l": 44, "r": 12, "t": 40, "b": 65})
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="http://127.0.0.1:8765")
    parser.add_argument("--project", default="training-example")
    args = parser.parse_args()
    client = cairn.Client(args.repo, project=args.project)
    try:
        for model, name in enumerate(["baseline", "regularized", "improved"]):
            with client.run(f"workspace-demo/{name}", config={"model": name, "learning_rate": [0.01, 0.03, 0.05][model], "seed": 17}, metadata={"description": "Synthetic example for scalar, image and Plotly workspace cards", "synthetic": True}, provenance=False, capture_output=False) as run:
                for step in range(41):
                    loss = 0.85 * math.exp(-step / (13 - model * 3)) + 0.045 + (2 - model) * 0.025 + 0.015 * math.sin(step * 0.7 + model) ** 2
                    run.log(step=step, loss=loss, validation_loss=loss * (1.08 + model * 0.04), elapsed=round(step * 1.5, 2))
                    if step % 8 == 0:
                        for camera, angle in [("front", 0), ("oblique", 35)]:
                            # One missing observation makes exact-vs-previous matching visible.
                            if model == 1 and camera == "oblique" and step == 24:
                                continue
                            quality = round(12 + 20 * (1 - math.exp(-step / (18 - model * 4))) - abs(angle) * 0.03, 3)
                            run.log(step=step, elapsed=step * 1.5, camera=camera, camera_angle=angle, quality=quality, image=cairn.Image(reconstruction(step, model, angle), name=f"{name}-{camera}-{step}.png"))
                        run.log(step=step, elapsed=step * 1.5, figure=cairn.Figure(figure(step, model)))
                run.finish(value={"final_loss": loss, "example": "workspace-cards"})
                print(f"Logged {name}: {run.id}")
    finally:
        client.close()
    print(f"Open {args.repo}/p/{args.project}/workspace?example=cards")


if __name__ == "__main__":
    main()

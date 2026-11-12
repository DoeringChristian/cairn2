"""Run an experiment and write its report.

Run from this folder: uv run --extra all python run.py
Then: uv run --extra all cairn server --no-auth
"""

import math
from pathlib import Path

import cairn
from cairn import NotFound

ASSETS = Path(__file__).parent / "assets"
RUN = "cairn:runs/showcase-baseline@latest"
BODY = f"""# Field guide

This is a working tour of a report: you write the plan, and type @agent where the agent should answer.

## Plan

- Train the baseline for 60 steps.
- Compare the field at the start and the end.
  1. Look for ringing near the boundary.
  2. Check the energy $E = \\frac{{1}}{{2}} \\int |\\nabla u|^2 \\, dx$.

| Quantity | Target |
| --- | --- |
| Loss | < 0.02 |
| PSNR | > 34 dB |

$$
\\mathcal{{L}} = \\lVert u - \\hat u \\rVert_2^2
$$

::: {{.callout-note}}
Seeds are fixed, so every rerun is comparable.[^seed]
:::

[^seed]: The seed is 42.

```python
def summarize(losses):
    return min(losses)
```

## Evidence

![Field at the start]({RUN}#figures/field-start.png)

![Field at the end]({RUN}#figures/field-end.png)

![Wave evolution]({RUN}#media/wave-evolution.mp4)

![Signal]({RUN}#media/signal.wav)

```view
viewer: cairn:viewers/field@latest#index.html
caption: The field at the start and the end, in a custom viewer
title: Field over training
images:
  - {RUN}#figures/field-start.png
  - {RUN}#figures/field-end.png
```

## Questions for the agent

<?cairn agent baseline?>

<?cairn agent ablation?>
"""
REQUESTS = {
    "baseline": "Did the baseline converge? Show the final field.",
    "ablation": "Which ablation should run next?",
}
SLOTS = {
    "baseline": f"Converged: loss **0.018**, PSNR 34.2 dB.\n\n![Final field]({RUN}#figures/field-end.png)\n",
    "ablation": "Drop the boundary term first; it is the likeliest cause of the ringing.\n",
}


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

    with cairn.Client(project=project) as client:
        try:
            client.report("field-guide")
        except NotFound:
            client.create_report("field-guide", BODY)
            for part, request in REQUESTS.items():
                client.comment_report("field-guide", request, part=part)
        for part, source in SLOTS.items():
            client.write_report("field-guide", part, source, by="agent")
    print(f"Report: field-guide (project: {project})")


if __name__ == "__main__":
    main()

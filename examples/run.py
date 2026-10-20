"""Run an experiment and publish its report.

Run: uv run --extra all python examples/build_report_showcase.py
Then: uv run --extra all cairn server --no-auth
"""

import math
from pathlib import Path

import cairn
from cairn.reports import build, publish


def main():
    project = "neural-rendering"
    run = cairn.init("showcase-baseline", project=project, config={"seed": 42, "samples": 32})

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

    cairn.finish(value={"loss": 0.018, "psnr": 34.2, "converged": True})

    source = Path(__file__).parent / "report-showcase/report.qmd"
    output = Path("build/report-showcase") / run.id
    result = build(source, output)
    publication = publish(result.path, name="report-field-guide")
    print(f"Report files: {result.path}")
    print(f"Published report: {publication['id']} (project: {project})")


if __name__ == "__main__":
    main()

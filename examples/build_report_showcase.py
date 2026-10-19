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
    run = cairn.init(project=project, name="showcase-baseline", config={"seed": 42, "samples": 32})
    destination = run.client.destination
    print(f"Repository: {destination}")

    dataset = run.client.start("datasets", "showcase-samples")
    dataset.finish(value=cairn.Table([{"sample": n, "weight": 1 / 32} for n in range(32)]))
    cairn.use("datasets/showcase-samples@latest", role="training-data")

    for step in range(60):
        loss = round(math.exp(-step / 18) + 0.02, 6)
        cairn.log({"loss": loss}, step=step)

    cairn.finish(value={"loss": 0.018, "psnr": 34.2, "converged": True})

    with cairn.Client(destination, project=project) as client:
        source = Path(__file__).parent / "report-showcase/report.qmd"
        output = Path("build/report-showcase") / run.id
        result = build(source, output, client=client)
        publication = publish(result.path, client=client, name="report-field-guide")
        print(f"Report files: {result.path}")
        print(f"Published report: {publication['id']} (project: {project})")


if __name__ == "__main__":
    main()

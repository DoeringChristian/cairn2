"""Build and optionally publish the report field guide into a chosen repository.

Run: uv run python examples/build_report_showcase.py --repo /tmp/showcase --publish
Requires cairn2[reports,media]. Synthetic media is checked in; no FFmpeg is required.
"""

import argparse
import math
import tempfile
from pathlib import Path

from cairn import Client, Table
from cairn.reports import build, publish


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="/tmp/cairn2-report-showcase")
    parser.add_argument("--project", default="report-showcase")
    parser.add_argument("--output")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    output = (
        Path(args.output)
        if args.output
        else Path(tempfile.mkdtemp(prefix="cairn-showcase-")) / "build"
    )
    with Client(args.repo, project=args.project) as client:
        dataset = client.start("datasets", "showcase-samples")
        dataset.finish(value=Table([{"sample": n, "weight": 1 / 32} for n in range(32)]))
        run = client.run("showcase-baseline", config={"seed": 42, "samples": 32})
        run.use("datasets/showcase-samples@latest", role="training-data")
        for step in range(60):
            run.log(step=step, loss=round(math.exp(-step / 18) + 0.02, 6))
        run.finish(value={"loss": 0.018, "psnr": 34.2, "converged": True})
        result = build(Path(__file__).parent / "report-showcase/report.qmd", output, client=client)
        print(result.path)
        if args.publish:
            publication = publish(result.path, client=client, name="report-field-guide")
            print(publication["id"])


if __name__ == "__main__":
    main()

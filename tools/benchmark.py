"""Time what must stay fast, on generated data: uv run python tools/benchmark.py [scale]"""

import hashlib
import io
import math
import random
import sys
import tempfile
import time

from cairn import cairn_pb2 as pb
from cairn import values
from cairn.client import condition
from cairn.store import Repository


def timed(name, call, repeat=15):
    times = []
    for _ in range(repeat):
        start = time.perf_counter()
        call()
        times.append(time.perf_counter() - start)
    print(f"{name:45} {sorted(times)[len(times) // 2] * 1e3:9.2f} ms", flush=True)


def fill(repository, name, count, rate):
    made = repository.open(pb.Open(project="p", namespace="runs", name=name)).id
    repository.append(pb.Append(artifact=made, writer=made, sequence=1, records=[values.record({"_config": {"lr": rate}})]))
    for start in range(0, count, 500):
        records = [values.record({"step": i, "loss": math.exp(-i / 1500) + random.random() / 50, "psnr": random.random()}) for i in range(start, min(start + 500, count))]
        repository.append(pb.Append(artifact=made, writer=made, sequence=start + 2, records=records))
    repository.finish(pb.Finish(artifact=made, writer=made, sequence=count + 2, record=values.record({"_outcome": "completed"}), completed=True))
    return made


def main(scale=1.0):
    random.seed(0)
    runs, long, files = int(1000 * scale), int(1_000_000 * scale), int(1_000_000 * scale)
    with tempfile.TemporaryDirectory() as folder:
        repository = Repository(folder)
        small = [fill(repository, f"sweep/{i:04}", 400, 10 ** -random.uniform(1, 5)) for i in range(runs)][0]
        start = time.perf_counter()
        big = fill(repository, "big", long, 0.5)
        print(f"{long / (time.perf_counter() - start):,.0f} records stored per second")
        digest = hashlib.sha256(b"x").digest()
        repository.put(digest, io.BytesIO(b"x"))
        data = repository.open(pb.Open(project="p", namespace="datasets", name="scenes")).id
        paths = [f"scene_{i // 1000:03}/rgb_{i:06}.png" for i in range(files)]
        for part in range(10):
            held = {path: pb.Blob(digest=digest, size=1) for path in paths[part::10]}
            repository.append(pb.Append(artifact=data, writer="w", sequence=part + 1, records=[values.record({f"raw{part}": held})]))
        middle = paths[files // 2]
        where = [condition(f"_config/lr < {text}") for text in ("0.0001",)]
        timed(f"a curve of {long:,} records", lambda: repository.series(pb.Series(artifact=big, paths=["loss"])), 3)
        timed("a curve of 400 records", lambda: repository.series(pb.Series(artifact=small, paths=["loss"])))
        timed("runs by a configuration value", lambda: repository.find(pb.Find(project="p", where=where)))
        timed("the 10 best runs", lambda: repository.find(pb.Find(project="p", order="loss", limit=10)))
        timed("the last value of a long run", lambda: repository.last(pb.Last(artifact=big, keys=["loss"])))
        timed(f"one of {files:,} files by path", lambda: repository.files(pb.Files(project="p", pattern=f"raw{files // 2 % 10}/{middle}")))
        timed("one by part of its name", lambda: repository.files(pb.Files(project="p", text=middle[-14:-4])))
        timed("100 of a name all have", lambda: repository.files(pb.Files(project="p", text="rgb_", limit=100)))
        repository.close()


if __name__ == "__main__":
    main(*map(float, sys.argv[1:]))

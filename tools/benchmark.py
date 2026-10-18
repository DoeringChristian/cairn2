"""Reproducible single-producer ingest and immutable-query measurements.

Run from the project root with `.venv/bin/python tools/benchmark.py --records 20000`.
The JSON result contains host/workload/source fingerprints and raw timing samples.
Every mode uses a fresh temporary repository; optional PostgreSQL schemas are dropped.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
import uvicorn

from cairn import Client
from cairn.objects import Files
from cairn.server import create_app
from cairn.storage import Repository
from cairn.transport import HTTPRepository

TOKEN = "ephemeral-benchmark-token"
WHERE = {
    "and": [
        {"path": "/split", "op": "eq", "value": "validation"},
        {"path": "/loss", "op": "lt", "value": 0.2},
    ]
}
MODULES = (
    "storage",
    "query",
    "objects",
    "transport",
    "server",
    "sdk",
    "delivery",
    "media",
    "model",
)


def fingerprints():
    root = Path(__file__).resolve().parents[1] / "cairn"
    return {
        name: hashlib.sha256((root / f"{name}.py").read_bytes()).hexdigest() for name in MODULES
    }


def environment():
    details = {
        "os": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version,
        "logical_cpus": os.cpu_count(),
        "sqlite": sqlite3.sqlite_version,
    }
    if platform.system() == "Darwin":
        for key, name in (
            ("machdep.cpu.brand_string", "cpu"),
            ("hw.memsize", "memory_bytes"),
            ("hw.physicalcpu", "physical_cpus"),
        ):
            result = subprocess.run(
                ["sysctl", "-n", key], capture_output=True, text=True, check=True
            )
            details[name] = int(result.stdout) if name != "cpu" else result.stdout.strip()
    details["packages"] = {
        name: importlib.metadata.version(name)
        for name in ("cairn2", "sqlalchemy", "httpx", "fastapi", "uvicorn")
    }
    return details


def summarize(samples):
    ordered = sorted(samples)
    return {
        "count": len(samples),
        "p50_ms": statistics.median(samples) * 1000,
        "p95_ms": ordered[max(0, math.ceil(len(samples) * 0.95) - 1)] * 1000,
        "max_ms": max(samples) * 1000,
        "samples_seconds": samples,
    }


@contextmanager
def serve(repository):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(
        uvicorn.Config(create_app(repository, token=TOKEN), log_level="error", lifespan="off")
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    try:
        while not server.started:
            if time.monotonic() >= deadline or not thread.is_alive():
                raise RuntimeError("Benchmark HTTP server did not start")
            time.sleep(0.01)
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("Benchmark HTTP server did not stop")


@contextmanager
def repository_at(path, postgres_url=None):
    if postgres_url is None:
        repository = Repository(path)
        try:
            yield repository
        finally:
            repository.close()
        return
    schema = "cairn_benchmark_" + uuid.uuid4().hex
    administration = sa.create_engine(postgres_url)
    with administration.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    url = sa.engine.make_url(postgres_url).update_query_dict({"options": f"-csearch_path={schema}"})
    repository = None
    try:
        repository = Repository(
            database_url=url.render_as_string(hide_password=False),
            object_store=Files(path / "objects"),
        )
        yield repository
    finally:
        if repository is not None:
            repository.close()
        with administration.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        administration.dispose()


def data_rows(count):
    return [
        {
            "sample": i,
            "epoch": i // 1000,
            "split": ("train", "validation", "test")[i % 3],
            "loss": ((i * 37) % 1000) / 1000,
            "correct": i % 7 != 0,
        }
        for i in range(count)
    ]


def read_pages(backend, artifact_id, expected, *, where=None, page_size=1000, repeats=3):
    page_times, elapsed, first_page = [], [], []
    for _ in range(repeats):
        cursor, samples = None, []
        began = time.perf_counter()
        while True:
            requested = time.perf_counter()
            page = backend.query(
                artifact_id, where=where, limit=page_size, cursor=cursor, kind="record"
            )
            duration = time.perf_counter() - requested
            page_times.append(duration)
            if cursor is None:
                first_page.append(duration)
            assert len(page["items"]) <= page_size
            assert page["artifact_id"] == artifact_id
            samples.extend(row["data"]["sample"] for row in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        elapsed.append(time.perf_counter() - began)
        assert samples == expected, "Pinned query lost, duplicated, changed or reordered records"
    return {
        "rows": len(expected),
        "page_size": page_size,
        "repeats": repeats,
        "total_seconds": elapsed,
        "first_page": summarize(first_page),
        "page_latency": summarize(page_times),
    }


def finish_backend(backend, response, count):
    backend.close_writer(response["artifact"]["id"], response["writer"]["id"], count)
    return backend.finish(response["artifact"]["id"])


def benchmark_queries(backend, version, data, repeats):
    # Move the artifact head so every measured read must stay pinned to the old version.
    later = backend.start("benchmark", "runs", "ingest")
    backend.append_batch(
        later["artifact"]["id"],
        later["writer"]["id"],
        [{"sequence": 1, "kind": "record", "data": {"sample": -1, "split": "later"}}],
    )
    newer = finish_backend(backend, later, 1)
    assert newer["id"] != version["id"]
    assert backend.resolve("runs/ingest", project="benchmark")["id"] == newer["id"]
    all_samples = [row["sample"] for row in data]
    matching = [row["sample"] for row in data if row["split"] == "validation" and row["loss"] < 0.2]
    return {
        "all": read_pages(backend, version["id"], all_samples, repeats=repeats),
        "filtered": read_pages(
            backend, version["id"], matching, where=WHERE, page_size=200, repeats=repeats
        ),
    }


def batch_run(backend, data, batch_size, repeats):
    response = backend.start("benchmark", "runs", "ingest")
    entries = [{"sequence": i + 1, "kind": "record", "data": row} for i, row in enumerate(data)]
    samples = []
    began = time.perf_counter()
    for offset in range(0, len(data), batch_size):
        batch = entries[offset : offset + batch_size]
        before = time.perf_counter()
        result = backend.append_batch(response["artifact"]["id"], response["writer"]["id"], batch)
        samples.append(time.perf_counter() - before)
        assert result["acknowledged"] == min(offset + batch_size, len(data))
    elapsed = time.perf_counter() - began
    publication_began = time.perf_counter()
    version = finish_backend(backend, response, len(data))
    publication_ms = (time.perf_counter() - publication_began) * 1000
    return {
        "records": len(data),
        "batch_size": batch_size,
        "ingest_seconds": elapsed,
        "acknowledged_records_per_second": len(data) / elapsed,
        "durable_batch_latency": summarize(samples),
        "publication_ms": publication_ms,
        "queries": benchmark_queries(backend, version, data, repeats),
    }


def sdk_run(destination, data, repeats, outbox=None):
    with Client(destination, project="benchmark", token=TOKEN, outbox=outbox) as client:
        artifact = client.start("runs", "ingest")
        backend_samples, backend_sizes, log_samples = [], [], []
        original = client.backend.append_batch

        def observe(artifact_id, writer_id, entries):
            before = time.perf_counter()
            response = original(artifact_id, writer_id, entries)
            backend_samples.append(time.perf_counter() - before)
            backend_sizes.append(len(entries))
            return response

        client.backend.append_batch = observe
        began = time.perf_counter()
        for row in data:
            before = time.perf_counter()
            artifact.log(row)
            log_samples.append(time.perf_counter() - before)
        logged = time.perf_counter()
        artifact.flush(timeout=120)
        flushed = time.perf_counter()
        publication_began = time.perf_counter()
        version = artifact.finish(timeout=120)
        publication_ms = (time.perf_counter() - publication_began) * 1000
        client.backend.append_batch = original
        return {
            "records": len(data),
            "log_return_means": "local_journal_commit" if outbox else "repository_commit",
            "log_seconds": logged - began,
            "flush_tail_seconds": flushed - logged,
            "ingest_seconds": flushed - began,
            "acknowledged_records_per_second": len(data) / (flushed - began),
            "log_call_latency": summarize(log_samples),
            "durable_batch_latency": summarize(backend_samples),
            "actual_batch_rows": {
                "count": len(backend_sizes),
                "p50": statistics.median(backend_sizes),
                "p95": sorted(backend_sizes)[math.ceil(len(backend_sizes) * 0.95) - 1],
            },
            "publication_ms": publication_ms,
            "queries": benchmark_queries(client.backend, version, data, repeats),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=20_000)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--read-repeats", type=int, default=3)
    parser.add_argument("--postgres-config", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.records < 1 or not 1 <= args.batch_size <= 10000 or args.read_repeats < 1:
        parser.error("Counts must be positive; batch size must be at most 10000")
    result = {
        "measured_at": datetime.now(UTC).isoformat(),
        "environment": environment(),
        "source_sha256": fingerprints(),
        "workload": {
            "records": args.records,
            "batch_size": args.batch_size,
            "read_repeats": args.read_repeats,
            "predicate": WHERE,
            "payload": "sample:int, epoch:int, split:str, loss:float, correct:bool",
            "producers": 1,
            "blob_payload_bytes": 0,
        },
        "results": {},
    }
    data = data_rows(args.records)
    postgres = json.loads(args.postgres_config.read_text())["url"] if args.postgres_config else None
    with tempfile.TemporaryDirectory(prefix="cairn-benchmark-") as workspace:
        root = Path(workspace)
        for database, url in [("sqlite", None), *([("postgres", postgres)] if postgres else [])]:
            for http in (False, True):
                name = f"{database}-{'http' if http else 'direct'}-batch"
                with repository_at(root / name, url) as repository:
                    with repository._transaction() as conn:
                        result.setdefault("database_settings", {})[database] = (
                            {
                                "journal_mode": conn.exec_driver_sql(
                                    "PRAGMA journal_mode"
                                ).scalar(),
                                "synchronous": conn.exec_driver_sql("PRAGMA synchronous").scalar(),
                            }
                            if database == "sqlite"
                            else {
                                key: conn.exec_driver_sql(statement).scalar()
                                for key, statement in (
                                    ("version", "SELECT version()"),
                                    ("fsync", "SHOW fsync"),
                                    ("synchronous_commit", "SHOW synchronous_commit"),
                                    ("full_page_writes", "SHOW full_page_writes"),
                                )
                            }
                        )
                    if http:
                        with serve(repository) as endpoint:
                            backend = HTTPRepository(endpoint, token=TOKEN)
                            try:
                                result["results"][name] = batch_run(
                                    backend, data, args.batch_size, args.read_repeats
                                )
                            finally:
                                backend.close()
                    else:
                        result["results"][name] = batch_run(
                            repository, data, args.batch_size, args.read_repeats
                        )
                print(f"Completed {name}", file=sys.stderr, flush=True)
        result["results"]["sqlite-sdk-log"] = sdk_run(
            str(root / "sdk-local"), data, args.read_repeats
        )
        print("Completed sqlite-sdk-log", file=sys.stderr, flush=True)
        with repository_at(root / "sdk-remote") as repository, serve(repository) as endpoint:
            result["results"]["sqlite-http-sdk-log"] = sdk_run(
                endpoint, data, args.read_repeats, root / "outbox"
            )
        print("Completed sqlite-http-sdk-log", file=sys.stderr, flush=True)
    result["source_changed_during_run"] = fingerprints() != result["source_sha256"]
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n")
        print(f"Results: {args.output}", file=sys.stderr)
    else:
        print(rendered)


if __name__ == "__main__":
    main()

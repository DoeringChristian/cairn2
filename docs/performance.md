# Performance measurements

These measurements are the initial implementation baseline, before the namespace
and publication-capacity review and the single-artifact lifecycle rewrite. The source fingerprints below identify that
baseline; the revised implementation has not been re-benchmarked.

These measurements are a reproducible baseline for this implementation on one machine,
not throughput or latency guarantees. Run the same workload on the intended deployment
before choosing a capacity target. The runner is [tools/benchmark.py](../tools/benchmark.py),
and the measured samples are preserved in [performance-results.json](performance-results.json).

Measured on 2026-10-18 with an Apple M5 Pro, 18 physical/logical cores, 64 GiB RAM,
macOS 26.6.2 arm64, Python 3.12.14, SQLite 3.53.1, and PostgreSQL 17.11 (Homebrew).
Packages were Cairn2 0.1.0, SQLAlchemy 2.1.5, HTTPX 0.28.1, FastAPI 0.143.3, and
Uvicorn 0.54.0. SQLite used WAL with `synchronous=FULL`; PostgreSQL reported
`fsync=on`, `synchronous_commit=on`, and `full_page_writes=on`. Both used the local
files object adapter. No blob payloads were included in this workload.

Each mode used a fresh repository, one producer, and 20,000 deterministic structured
records containing `sample`, `epoch`, `split`, `loss`, and `correct`. Batch modes
appended 256 records per request: 79 acknowledged transactions, with 32 records in
the final batch. Records were prepared before timing. Ingest includes serialization,
validation, storage, and acknowledgement; it excludes initial artifact creation and
final publication. Publication was measured separately.

| Batched ingest | Acknowledged records/s | Batch p50 / p95 (ms) | Publication (ms) |
| --- | ---: | ---: | ---: |
| SQLite direct | 68,131 | 3.62 / 4.44 | 2.58 |
| SQLite loopback HTTP | 22,493 | 11.24 / 13.28 | 5.28 |
| PostgreSQL direct | 18,869 | 12.95 / 16.13 | 9.71 |
| PostgreSQL loopback HTTP | 9,250 | 26.35 / 33.52 | 14.44 |

The ordinary SDK path called `artifact.log(record)` 20,000 times. Direct local calls
acknowledge a repository transaction individually. Remote calls acknowledge the local
SQLite outbox; a background dispatcher batches delivery. The throughput below includes
the final `flush()` and therefore counts records acknowledged by the repository.

| Ordinary SDK logging | Acknowledged records/s | `.log()` p50 / p95 (ms) | Median actual batch rows | Repository batch p50 / p95 (ms) |
| --- | ---: | ---: | ---: | ---: |
| SQLite direct | 2,027 | 0.481 / 0.605 | 1 | 0.460 / 0.579 |
| SQLite loopback HTTP with outbox | 2,412 | 0.285 / 1.030 | 15 | 2.85 / 10.35 |

The remote SDK emitted 1,318 batches during this run; batch size p95 was 18 records,
below the configured maximum of 256 because delivery ran concurrently with logging.
The final flush tail was 4.32 ms. Remote `.log()` latency measures durable local
acceptance, so it should not be interpreted as server acknowledgement latency.

Every mode then published a different revision of the same artifact before reading the
original exact version. The runner checks the version ID, page bound, complete record
order, and expected values. Full scans returned all 20,000 records in pages of at most
1,000. Filtering `/split == "validation" AND /loss < 0.2` returned exactly 1,326 records
in pages of at most 200. Each scan ran three times; totals are median elapsed times,
and page percentiles pool the 60 full-scan or 21 filtered requests.

| Frozen-version reads | Full scan total (ms) | Full page p50 / p95 (ms) | Filtered total (ms) | Filtered page p50 / p95 (ms) |
| --- | ---: | ---: | ---: | ---: |
| SQLite direct batch repository | 73.75 | 3.18 / 3.45 | 20.34 | 3.00 / 3.24 |
| SQLite HTTP batch repository | 369.14 | 17.76 / 20.23 | 54.90 | 8.15 / 8.39 |
| PostgreSQL direct batch repository | 157.29 | 7.03 / 7.74 | 123.14 | 18.28 / 21.33 |
| PostgreSQL HTTP batch repository | 515.33 | 24.81 / 29.20 | 334.12 | 21.03 / 101.70 |
| SQLite direct SDK repository | 179.61 | 7.68 / 10.64 | 55.16 | 8.18 / 10.04 |
| SQLite HTTP SDK repository | 402.73 | 19.65 / 21.59 | 56.22 | 8.27 / 8.66 |

Reproduce the SQLite modes from the project root with the server extra installed:

```sh
.venv/bin/python tools/benchmark.py \
  --records 20000 --batch-size 256 --read-repeats 3 \
  --output /tmp/cairn2-benchmark-results.json
```

For PostgreSQL, install the postgres extra and add
`--postgres-config /path/to/postgres.json`. That file contains `{"url":
"postgresql+psycopg://..."}` for a reachable PostgreSQL database. The runner creates
isolated schemas and drops them when done; the account needs schema creation privileges.
It also removes temporary local repositories and stops its HTTP servers.

The JSON output contains host details, database settings, source SHA-256 fingerprints,
workload parameters, and raw timing samples. Runtime source fingerprints were unchanged
throughout the recorded run. The checked-in JSON preserves that run; rerun the command
to generate a separate result for another machine or code revision.

These are single-run measurements on an ordinary developer machine, with each mode
executed sequentially. Read repetitions reuse operating-system and database caches.
The HTTP measurements use real authenticated requests over `127.0.0.1`, with one
Uvicorn thread in the benchmark process. They include client/server serialization but
do not model WAN latency, TLS, a separate server host, multiple producers, saturation,
large media, or S3. No emulator throughput is reported. The benchmark exercises the
normal synchronous commit paths; it does not test physical power loss. The sample
counts are too small to establish production tail-latency guarantees.

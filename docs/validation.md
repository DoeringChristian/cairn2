# Implementation validation

On 2026-10-18 the complete integration suite passed **228 tests in 39.00 seconds**,
with PostgreSQL integration enabled. The base wheel passed an isolated install
smoke check for artifact creation, logging, annotation updates, SQL discovery,
stale-edit rejection, immutable publication content and CLI.
The source distribution and wheel built successfully.

Ruff passed for the backend, SDK, reports, tests, examples and tools, excluding
the independently maintained `cairn/webui.py`. Formatting checks passed.
UI files were deliberately left untouched.

The runtime gate counted **7,747 physical Python lines** across the package,
including the unchanged UI integration helper, comments, blank lines, provenance,
report building, CLI, delivery and administrative operations. The limit is 9,000.

The suite covers:

- Mutable JSON annotations separate from immutable captured metadata; merge-patch
  deletion, ETag conflicts, exact retries, transfer integrity and concurrent finish.
- SQL artifact discovery across metadata and annotations, with generic scalar-array
  membership. Query-plan checks confirm SQLite indexed membership lookup without
  an artifact scan and PostgreSQL GIN availability; these are not load benchmarks.

- One permanent artifact ID from live creation to immutable completion; version
  labels and aliases select artifacts directly, with no revisions or snapshots.
- Automatic client-side run provenance, source/dependency capture and opt-outs.
- Short-ID ambiguity rejection, finish request-ID collision rollback and exact
  pending-intent replay.

- Lazy delivery initialization: readers do not create or resume an outbox.
- Local/HTTP contract signature parity and explicit scoped report readers.
- Early atomic publication capacity rejection, validated import accounting, and
  corrected finalization after definitive rejection; uncertain retries stay fixed.
- The same lifecycle through direct calls and actual authenticated HTTP, using
  SQLite/files, PostgreSQL/files, and PostgreSQL/S3-compatible storage.
- Exact retry receipts, overlapping batches, atomic rejection, producer sequence
  checks, concurrent writers, alias conflicts, stable artifact IDs, and immutable finish.
- Interrupted remote delivery, lost acknowledgements, process exit, journal
  recovery, bounded queues, destination identity changes, and connection cleanup.
- Numeric and Unicode portability, typed scalar predicates, escaped JSON
  pointers, deep object/array queries, fixed-cutoff pagination, and query limits.
- Complete dependency retention, including pending publication values, cleanup
  racing an append, active uploads, and failed cleanup followed by retry.
- Transfer checksums, source mutation during import, malformed repository
  structure, atomic conflict rollback, exact IDs, aliases, comments, provenance,
  and transfer/resumption of unfinished artifacts.
- Local report kernels, declared-input pinning, includes/resources, source maps,
  rich static output, failed/tampered build rejection, publication, export, and
  reproducible source-copy/export replay.
- Token discovery, authentication boundaries, streamed blob transfers, CLI
  repo discovery, and typed media read/download behavior.

PostgreSQL tests used a temporary local PostgreSQL 17 instance. S3 tests used
Moto's HTTP server with real Boto3 requests; they do not constitute an AWS or
production object-store qualification. These are application fault and
concurrency tests, not hardware power-loss tests or a sustained production load
test. The suite emitted one upstream Starlette TestClient deprecation warning
about its HTTPX integration.

Build checks produce both the source distribution and wheel. A separate clean
environment installs the base wheel and runs local creation, logging, publication,
annotation/filter operations, stale-write rejection and CLI help without
server/report extras.

To reproduce the checks:

```sh
uv sync --all-extras --group dev
# Point at an expendable PostgreSQL database whose user may create test databases.
export CAIRN_TEST_POSTGRES_URL='postgresql+psycopg://localhost/cairn_tests'
uv run pytest -q
uv run ruff check cairn tests tools examples --exclude cairn/webui.py
uv run ruff format --check cairn tests tools
uv run python tools/count_python.py
uv build
```

Without `CAIRN_TEST_POSTGRES_URL`, PostgreSQL cases skip. See
[performance measurements](performance.md) for the workload and measured
throughput; those results are separate from correctness testing.

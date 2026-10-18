# Cairn2 implementation plan

This is a clean project implementing the architecture approved on 2026-10-18. The existing Cairn and Prism2 implementations are not copied or modified. Scope: common repository, local and HTTP access, SQLite/files and PostgreSQL/S3, Python API with durable remote batching, CLI, client-side QMD build/publish. No browser application or server report execution/editor.

The implementation has four parallel ownership areas:

1. Root: model/errors, SQL repository, files/S3 object storage, query semantics, transfer/GC, shared integration and performance validation.
2. SDK: typed values/media, explicit/default Python API, durable remote delivery journal and recovery.
3. HTTP: named transport methods, one-token server/configuration, HTTP conformance and auth tests.
4. Reports/CLI: QMD parsing and client execution, captured source/evidence/output packaging and HTML, command-line workflows and tests.

All areas implement CONTRACT.md. Changes to a seam must be coordinated before dependent edits. New artifact kinds use payload formats and common dependencies, not type-specific tables or routes.

Validation proceeds through a local vertical slice; direct/HTTP parity and retry/conflict tests; real PostgreSQL and an S3-compatible test service; report build/publish/pinning; transfer and cleanup; packaging and CLI smoke; measured batched ingest/query benchmarks. Count all handwritten runtime Python, including operational and report code, with a hard target below 9,000 physical lines. Record measured outcomes and unsupported/deferred features honestly.

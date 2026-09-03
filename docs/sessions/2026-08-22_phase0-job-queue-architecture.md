# Session 004 — Phase 0 Job/Queue Architecture Research

- **Date:** 2026-08-22
- **Phase:** 0 — Architecture & Research
- **Depends on:** Session 003 (ADR-0001, Python runtime, accepted)

## Objective

Research and decide ATHENA AI-BRAIN's job/queue architecture: how long-running work (indexing, research/web-ingestion, reindexing, duplicate-detection/merge, Git commit/push) runs without blocking event handlers, while meeting the Testing Strategy's crash-recovery/durability requirements, on a local-first single-machine deployment.

## Completed Work

- Researched seven candidates (Celery, Dramatiq, Taskiq, RQ, arq, Huey, hand-rolled asyncio+SQLite queue) against ecosystem maturity, async-native fit, durability, broker/infrastructure requirements, retry/dead-letter handling, scheduling, security (serialization), resource footprint, testability, and maintenance status, using current 2026 primary sources.
- Wrote `docs/research/2026-08-22_job_queue_architecture.md` following the Documentation Standards research-doc structure.
- Drafted `docs/adr/0002-job-queue-architecture.md` recommending Huey with SQLite backend, initially status Proposed.
- Maintainer reviewed and **accepted ADR-0002 as proposed** — status now Accepted.

## Key Decision

**Huey with the SQLite backend (`SqliteHuey`)** is ATHENA AI-BRAIN's job/queue library, with the default pickle serializer swapped to Huey's built-in `SignedSerializer` or JSON. Rationale: it is the only widely-used, actively-maintained, "Production/Stable" library offering durable, zero-extra-infrastructure SQLite-native job storage, with retry and cron scheduling already built and tested — avoiding ATHENA AI-BRAIN having to build and test that reliability engineering itself.

## Key Finding

Every mainstream Python task queue (Celery, Dramatiq, Taskiq, RQ, arq) requires Redis or RabbitMQ — infrastructure the local-first design doesn't otherwise need. RQ and arq carry additional disqualifying issues (RQ: open unresolved CVSS 8.1 pickle-default issue; arq: maintainer-declared maintenance-only status).

## Files Changed

- `docs/research/2026-08-22_job_queue_architecture.md` (new)
- `docs/adr/0002-job-queue-architecture.md` (new, Proposed → Accepted)
- `CURRENT_STATE.md` (updated)
- `NEXT_SESSION.md` (updated)
- `CHANGELOG.md` (updated)
- `SESSION_LOG.md` (updated)
- `docs/sessions/2026-08-22_phase0-job-queue-architecture.md` (this file, new)

## Tests

None — research-only session per CLAUDE.md Phase discipline; no code was written.

## Unresolved Issues

- Huey's sync-core/`aget_result()` async bridge should be validated against one real job type early in Phase 1; the hand-rolled asyncio+SQLite queue is the documented fallback if that integration proves awkward.
- Whether Huey's own SQLite job-store file should be the same database as ATHENA AI-BRAIN's metadata store, or a separate file, is not yet decided — carried forward to the SQLite-access-layer research topic.
- The MCP `enqueue_job`/`get_job_status` tool pair design is deferred to the MCP protocol implementation research topic.

## Next Steps

Proceed to the next Phase 0 research queue items per `NEXT_SESSION.md`: RAG orchestration approach, SQLite access layer (including the Huey-database-sharing question above), Git automation library, reranking approach, MCP/Qdrant/embeddings/chunking implementation details.

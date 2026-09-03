# Session 006 — Phase 0 SQLite Access Layer Research

- **Date:** 2026-08-22
- **Phase:** 0 — Architecture & Research
- **Depends on:** Session 004 (ADR-0002, Huey/SQLite), Session 005 (ADR-0003, RAG orchestration incl. FTS5)

## Objective

Research and decide ATHENA AI-BRAIN's SQLite access layer for its metadata store (note metadata, provenance/lineage, knowledge lifecycle/status, duplicate-detection records, FTS5 keyword-search indexes), and resolve the open question from ADR-0002: should Huey's job-store SQLite file share ATHENA AI-BRAIN's metadata database, or stay separate?

## Completed Work

- Researched five access-layer approaches (raw `sqlite3` alone, `aiosqlite`+SQLAlchemy 2.x async, SQLModel, Peewee, hand-rolled thin repository layer) against async fit, FTS5 integration, migrations, type safety, security, maintainability, concurrency handling, and maturity, using current 2026 primary sources.
- Researched the Huey-database-sharing question directly against Huey's source (`huey/storage.py`) and SQLite's own WAL/locking documentation.
- Researched FTS5 integration patterns (external-content tables, trigger-based sync, `bm25()` ranking) against SQLite's official documentation.
- Wrote `docs/research/2026-08-22_sqlite_access_layer.md` following the Documentation Standards research-doc structure.
- Drafted `docs/adr/0004-sqlite-access-layer.md` recommending a hand-rolled repository layer plus a separate Huey database file, initially status Proposed.
- Maintainer reviewed and **accepted ADR-0004 as proposed** — status now Accepted.

## Key Decisions

1. ATHENA AI-BRAIN's SQLite access layer will be a **hand-rolled thin repository layer over `aiosqlite`**: typed async functions per query, parameterized SQL, a minimal `PRAGMA user_version`-driven migration runner, and FTS5 external-content tables with hand-written trigger sync. Peewee documented as fallback.
2. **Huey's job store uses a separate SQLite file** from ATHENA AI-BRAIN's metadata database, to avoid single-writer-lock contention and keep Huey's opaque schema out of ATHENA AI-BRAIN's own migration story.

## Key Findings

- FTS5's `CREATE VIRTUAL TABLE` DDL forbids the constraints ORM table-definition APIs require — confirmed via SQLAlchemy's own GitHub discussion (#9466) and an open reflection issue (#4867) — so SQLAlchemy and SQLModel provide no real advantage over hand-rolled SQL for exactly the schema ATHENA AI-BRAIN's RAG design (ADR-0003) depends on most.
- SQLAlchemy's async SQLite driver has a documented, still-current transactional caveat (no implicit `BEGIN` for `SELECT`/DDL) requiring manual workaround code.
- Peewee's `FTS5Model` is the one exception researched with genuine first-class FTS5 support, and shares an author (Charles Leifer) with the already-accepted Huey.
- `PRAGMA busy_timeout` is per-connection and resets to zero on every new connection — every connection opener (ATHENA AI-BRAIN's app and any Huey worker process) must set it explicitly, regardless of the file-sharing decision.

## Files Changed

- `docs/research/2026-08-22_sqlite_access_layer.md` (new)
- `docs/adr/0004-sqlite-access-layer.md` (new, Proposed → Accepted)
- `CURRENT_STATE.md` (updated)
- `NEXT_SESSION.md` (updated)
- `CHANGELOG.md` (updated)
- `SESSION_LOG.md` (updated)
- `docs/sessions/2026-08-22_phase0-sqlite-access-layer.md` (this file, new)

## Tests

None — research-only session per CLAUDE.md Phase discipline; no code was written.

## Unresolved Issues (carried forward as implementation-time checks, not blockers)

- Prototype Peewee in parallel as a fallback if the hand-rolled repository layer proves more effort than expected.
- Verify connection-management pattern (single long-lived connection vs. small pool) once Huey and the MCP server are both live.
- Ensure every connection opener sets `PRAGMA busy_timeout` explicitly.

## Next Steps

Proceed to the next Phase 0 research queue items per `NEXT_SESSION.md`: Git automation library, MCP tool contract design, Qdrant deployment specifics, embeddings model choice, filesystem event architecture.

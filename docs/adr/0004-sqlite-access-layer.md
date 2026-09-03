# ADR-0004: SQLite Access Layer for ATHENA AI-BRAIN

- **ID:** ADR-0004
- **Title:** SQLite Access Layer for ATHENA AI-BRAIN
- **Status:** Accepted
- **Date proposed:** 2026-08-22
- **Date accepted:** 2026-08-22
- **Depends on:** ADR-0001 (runtime: Python, accepted), ADR-0002 (job queue: Huey/SQLite, accepted), ADR-0003 (RAG orchestration incl. SQLite FTS5, accepted)

## Context

ATHENA AI-BRAIN needs an access layer for its SQLite metadata store (note metadata, provenance/lineage, knowledge lifecycle/status, duplicate-detection records, FTS5 keyword-search indexes), consistent with the constitution's "small composable modules" principle and its asyncio-native runtime. ADR-0002 also left open whether Huey's SQLite job-store file should be the same database as ATHENA AI-BRAIN's metadata store or a separate file.

Five access-layer approaches were researched: raw stdlib `sqlite3` (ruled out standalone — not asyncio-compatible), `aiosqlite` + SQLAlchemy 2.x async, SQLModel, Peewee, and a hand-rolled thin repository layer over `aiosqlite`. Full findings: [`docs/research/2026-08-22_sqlite_access_layer.md`](../research/2026-08-22_sqlite_access_layer.md).

Key finding: **FTS5 — central to ATHENA AI-BRAIN's already-accepted RAG design (ADR-0003)** — gets no real help from SQLAlchemy or SQLModel. FTS5's `CREATE VIRTUAL TABLE` DDL forbids the types/constraints/PRIMARY KEY declarations ORM table-definition APIs require, confirmed by SQLAlchemy's own GitHub discussion (#9466) and an open reflection issue (#4867) — both frameworks reduce to raw SQL for exactly the schema ATHENA AI-BRAIN cares about most. SQLAlchemy's async SQLite driver also carries a documented, still-current transactional caveat requiring manual workaround code (the driver's "legacy transaction control" doesn't `BEGIN` for `SELECT`/DDL, breaking DDL-in-transaction and nested `SAVEPOINT`s).

## Decision

**Accepted:**
1. Build ATHENA AI-BRAIN's SQLite access layer as a **hand-rolled thin repository layer over `aiosqlite`**: small, explicit, typed async functions per query (using `dataclasses`/`TypedDict`/`NamedTuple` for row shapes), parameterized SQL throughout, a minimal `PRAGMA user_version`-driven migration runner (numbered `.sql` files), and FTS5 external-content tables with trigger-based sync written directly per SQLite's documented pattern.
2. Use a **separate SQLite file for Huey's job store**, distinct from ATHENA AI-BRAIN's metadata database.

The maintainer reviewed the research and comparison and accepted this ADR as proposed on 2026-08-22.

## Alternatives Considered

| Option | Verdict |
|---|---|
| `aiosqlite` + SQLAlchemy 2.x (Core or ORM) | Rejected as primary — the async SQLite driver's documented transactional caveat requires manual workaround plumbing, FTS5 support reduces to raw `text()`/`exec_driver_sql()` with no real advantage over hand-rolled, and it adds SQLAlchemy + `greenlet` + Alembic for what is a metadata store, not a complex relational domain. Remains viable later if non-FTS5 queries prove more complex than expected. |
| SQLModel | Rejected — inherits every SQLAlchemy caveat above verbatim while adding a Pydantic-model-duality dependency whose benefit (API request/response validation) doesn't apply to an internal, non-API-facing store. |
| Peewee | Rejected as primary, recommended as fallback — the only library researched with genuine first-class FTS5 modeling (`FTS5Model`), and shares an author (Charles Leifer) and design philosophy with the already-accepted Huey. Held back one rank because its native async support (`AsyncSqliteDatabase`, added in the 4.x line) is newer and less battle-tested than SQLAlchemy's, and its migration tooling (`playhouse.migrate`) is thinner than Alembic's. |
| Raw stdlib `sqlite3` alone | Not a real fifth option — not asyncio-compatible; `aiosqlite` is required baseline infrastructure under every approach evaluated, including the chosen one. |
| Combining Huey's job store with ATHENA AI-BRAIN's metadata DB in one file | Rejected — SQLite's single-writer-per-file constraint means Huey's per-task state writes and ATHENA AI-BRAIN's metadata/FTS5-trigger writes would compete for the same write slot; Huey's opaque, independently-evolving schema would couple ATHENA AI-BRAIN's migration runner to a dependency it has no business tracking; and the two subsystems have different backup semantics (disposable job state vs. durable knowledge state). |

## Rationale

1. **FTS5 friction is identical across every framework option**, so the hand-rolled approach loses nothing there relative to SQLAlchemy or SQLModel while avoiding their added dependency weight and SQLAlchemy's transactional-driver workaround requirement.
2. **This is a well-precedented pattern at ATHENA AI-BRAIN's scale**, not under-engineering: Simon Willison's `sqlite-utils` (actively maintained, FTS5-first-class) is essentially the same design — thin Python over raw SQLite — productized as a library, and the repository pattern (typed functions over raw SQL) is independently documented as standard practice, not an ORM-avoidance hack.
3. **Zero new heavyweight dependencies**, directly serving the constitution's "small composable modules" and "do not add dependencies beyond what's needed" principles.
4. **Security is not a differentiator** — every approach researched uses parameterized queries, structurally preventing SQL injection regardless of which is chosen.
5. **Huey file separation avoids single-writer-lock contention** between two independently write-heavy subsystems, keeps Huey's opaque internal schema out of ATHENA AI-BRAIN's own schema-versioning story, and gives the two subsystems appropriately different backup/recovery treatment (Huey's queue state is disposable/re-derivable; ATHENA AI-BRAIN's metadata is durable knowledge state).

## Consequences

- A minimal migration runner (`PRAGMA user_version` + numbered `.sql` files, roughly 30 lines per the research's well-precedented pattern) must be built as part of Phase 1 setup — this is a required, if small, piece of custom infrastructure, not optional.
- FTS5 external-content tables and their `AFTER INSERT/UPDATE/DELETE` sync triggers will be written directly as SQL per SQLite's documented pattern (`content=`/`content_rowid=`, `bm25()` ranking) — this SQL is identical regardless of any future access-layer reconsideration.
- Every SQLite connection opener (ATHENA AI-BRAIN's own app connections and any future separate Huey worker processes) must explicitly set `PRAGMA busy_timeout` on each new connection, since it is per-connection and resets to zero — this is a required implementation detail flagged here so it isn't missed.
- Huey's `SqliteHuey` will be configured to point at a separate `.db` file from ATHENA AI-BRAIN's own metadata database.
- If ATHENA AI-BRAIN's non-FTS5 metadata queries grow substantially more complex than currently expected, revisiting SQLAlchemy Core (not the ORM, not SQLModel) remains a documented fallback option — this is not precluded by this ADR, but is not the default.
- Peewee remains a documented fallback if the hand-rolled repository layer proves more effort than expected in practice, given how closely it was rated in the research.

## References

See [`docs/research/2026-08-22_sqlite_access_layer.md`](../research/2026-08-22_sqlite_access_layer.md) §11 for the full primary-source citation list.

## Open Questions

Resolved: the maintainer accepted this ADR as proposed on 2026-08-22, with no modifications requested.

Remaining open items, carried forward as implementation-time checks rather than blocking questions:
- Should Peewee be prototyped in parallel as a fallback before committing fully to the hand-rolled approach, given how close the research rated the two options?
- What connection-management pattern (single long-lived connection vs. small pool) best fits ATHENA AI-BRAIN's actual concurrency needs once Huey and the MCP server are both live? Flagged for Phase 1 verification.

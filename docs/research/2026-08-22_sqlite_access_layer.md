# Research: SQLite Access Layer for ATHENA AI-BRAIN

- **Research date:** 2026-08-22
- **Researcher:** Claude Code (ATHENA AI-BRAIN Phase 0)
- **Status:** Candidate evaluation — feeds ADR-0004 (SQLite access layer)
- **Depends on:** ADR-0001 (Python runtime), ADR-0002 (Huey/SQLite job queue), ADR-0003 (RAG orchestration, including SQLite FTS5 for keyword search)

## 1. Executive Summary

Five access-layer approaches were evaluated: raw stdlib `sqlite3` (ruled out standalone — asyncio-native requirement makes `aiosqlite` mandatory baseline infrastructure regardless of layer chosen), `aiosqlite` + SQLAlchemy 2.x async, SQLModel, Peewee, and a hand-rolled thin repository layer over `aiosqlite`. The decisive finding is that **FTS5 — central to ATHENA AI-BRAIN's RAG design — gets no real help from SQLAlchemy or SQLModel**: FTS5's `CREATE VIRTUAL TABLE` DDL forbids the types/constraints/PRIMARY KEY declarations that ORM table-definition APIs require, so both frameworks reduce to raw SQL for exactly the part of the schema ATHENA AI-BRAIN cares about most. SQLAlchemy's async SQLite driver also carries a documented, still-current transactional caveat requiring manual workaround code. **Peewee** is the one library researched with genuine first-class FTS5 modeling (`FTS5Model`), and shares an author with the already-accepted Huey. The **hand-rolled thin repository layer** is recommended as the primary approach — well-precedented at ATHENA AI-BRAIN's scale, adds no new heavyweight dependency, and doesn't lose anything to FTS5 friction that the framework options don't already lose. Huey's job-store SQLite file should be **separate** from ATHENA AI-BRAIN's metadata database, primarily to avoid single-writer-lock contention between two independently write-heavy subsystems.

## 2. Problem Being Solved

ATHENA AI-BRAIN needs an access layer for its SQLite metadata store (note metadata, provenance/lineage records, knowledge lifecycle/status, duplicate-detection records, FTS5 keyword-search indexes), asyncio-native, testable, and consistent with the constitution's "small composable modules" principle. It also needs to resolve whether Huey's own SQLite job-store file (ADR-0002) should be the same database as ATHENA AI-BRAIN's metadata store or a separate file.

## 3. Technology Overview

`aiosqlite` (0.22.1, Dec 2025) bridges stdlib `sqlite3` into asyncio via a single dedicated thread per connection processing a shared request queue — it is thread-offload, not true async I/O ("SQLite databases are not socket-based"), and is required baseline infrastructure under every approach evaluated, since raw synchronous `sqlite3` alone doesn't fit ATHENA AI-BRAIN's asyncio-native constraint. SQLAlchemy 2.x (2.0.52 stable / 2.1.0b3 beta) uses `aiosqlite` as its SQLite async driver. SQLModel (0.0.38) layers Pydantic onto SQLAlchemy models. Peewee (4.3.0, same author as Huey) added native first-party asyncio support in its 4.x line via `AsyncSqliteDatabase`.

## 4. Architecture Fit

- **FTS5 DDL cannot be expressed declaratively by any ORM researched.** SQLite's own documentation shows the canonical pattern: a content table, an external-content FTS5 virtual table (`content=`/`content_rowid=` to avoid duplicating text), and `AFTER INSERT/UPDATE/DELETE` triggers keeping them in sync, with `bm25()` for ranking. This must be executed as literal SQL in every approach — SQLAlchemy's own GitHub discussion (#9466) confirms there is no first-class Core/ORM support, and `MetaData.reflect()` has an open issue failing on FTS5-containing databases (#4867).
- **SQLAlchemy's async SQLite driver has a real, still-current transactional caveat**: the `sqlite3`/`aiosqlite` driver's "legacy transaction control" diverges from PEP 249 (no implicit `BEGIN` for `SELECT`/DDL, only DML), breaking DDL-in-transaction and nested `SAVEPOINT`s. SQLAlchemy's documented workaround requires either `connect_args={"autocommit": False}` (Python 3.12+) or manually disabling the driver's BEGIN handling and re-implementing it via SQLAlchemy's `ConnectionEvents.begin()` hook — non-trivial plumbing that must be understood and maintained regardless of how thin the rest of the usage is.
- **Peewee's `FTS5Model`** (in `playhouse.sqlite_ext`) is the one exception: it directly models external-content tables, prefix indexes, tokenizer choice, and `bm25()`-based ranking as a reusable Python API — the only library researched where FTS5 is a first-class citizen rather than an escape hatch to raw SQL.
- **Huey's own storage layer** (`huey/storage.py`, `SqliteStorage`) creates four namespaced tables (`kv`, `schedule`, `task`, `counter`), defaults to WAL mode, and serializes its own writes via `BEGIN EXCLUSIVE` behind an internal `threading.Lock` — confirming Huey is a well-behaved, independent SQLite writer regardless of file-sharing choice.

## 5. Alternatives Considered — Comparison Against Evaluation Criteria

| Approach | Async fit | FTS5 integration | Migrations | Type safety | Dependency footprint | Verdict |
|---|---|---|---|---|---|---|
| Raw stdlib `sqlite3` alone | Not viable — sync only, would require manual executor wrapping (exactly what `aiosqlite` already does) | N/A | N/A | N/A | N/A | Not a real fifth option — `aiosqlite` is required baseline under every approach |
| `aiosqlite` + SQLAlchemy 2.x (Core or ORM) | Async via `aiosqlite` driver, but with a real, documented transactional caveat requiring manual workaround | Reduces to raw `text()`/`exec_driver_sql()` — no real advantage over hand-rolled | Alembic (1.19.1) is mature but needs async-around-sync plumbing (`run_sync()`) for its default sync `env.py` | Strong for non-FTS5 tables via Core's typed query builder | Adds SQLAlchemy + `greenlet` + Alembic for a use case that's a metadata store, not a complex relational domain | Viable if non-FTS5 queries turn out more complex than expected; not clearly worth the cost otherwise |
| SQLModel | Inherits SQLAlchemy's async story verbatim — adds no async machinery of its own | Inherits SQLAlchemy's FTS5 friction verbatim | Inherits Alembic, same as SQLAlchemy | Pydantic+SQLAlchemy dual model — solves API validation, not ATHENA AI-BRAIN's problem | Adds a dependency (Pydantic-model duality) with no benefit for an internal, non-API-facing store | Not recommended — solves a problem ATHENA AI-BRAIN doesn't have while inheriting every SQLAlchemy caveat |
| Peewee | Native first-party async since 4.x (`AsyncSqliteDatabase`), but newer/less battle-tested than SQLAlchemy's multi-year async support | **Best of all options** — `FTS5Model` models the whole external-content + trigger + `bm25()` pattern as reusable Python API | `playhouse.migrate` exists but is thinner/less documented than Alembic | Good — models derive typed access | Small; shares design philosophy and author with already-accepted Huey (soft but real benefit) | Strong second choice; held back only by newer async maturity and thinner migration tooling |
| Hand-rolled thin repository layer over `aiosqlite` | Full control, no framework transactional caveats to work around | Native — same trigger/DDL SQL as every approach requires anyway for FTS5 | Well-precedented minimal pattern: `PRAGMA user_version` or `schema_version` table + numbered `.sql` files (~30 lines) | Achieved via `dataclasses`/`TypedDict`/`NamedTuple` per query result, checked manually | Zero new heavyweight dependencies | **Top recommendation** |

## 6. ATHENA AI-BRAIN Relevance

Since FTS5 is central to ATHENA AI-BRAIN's already-accepted hybrid retrieval design (ADR-0003) and every framework except Peewee reduces FTS5 handling to raw SQL anyway, the hand-rolled approach loses nothing there relative to SQLAlchemy or SQLModel while avoiding their added dependency weight and SQLAlchemy's documented transactional workaround requirement. This is well-precedented at ATHENA AI-BRAIN's scale: Simon Willison's `sqlite-utils` (actively maintained, FTS5-first-class) is essentially this exact pattern — thin Python over raw SQLite — productized as evidence it's a credible, widely-used design point in this specific ecosystem, not a niche or under-engineered choice. The repository pattern (small typed functions/classes over raw SQL) is independently documented as standard practice (Cosmic Python-style DDD writeups), not an ORM-avoidance hack.

## 7. Security

All approaches structurally prevent SQL injection via parameterized queries — this is not a differentiator (every option researched uses parameter binding, not string interpolation). The hand-rolled and Peewee approaches keep the query surface small and auditable; SQLAlchemy's larger surface (Core expression language, ORM session machinery) is a larger area to review but not inherently less safe when used correctly.

## 8. Performance

Not a strong differentiator at ATHENA AI-BRAIN's single-user scale. `aiosqlite`'s thread-offload model applies identically underneath every approach. WAL mode (used by both ATHENA AI-BRAIN's own connections and Huey's `SqliteStorage`) allows concurrent readers with a single writer; SQLite's single-writer-per-file constraint is the more significant practical concern, addressed by the Huey-file-separation recommendation below rather than by access-layer choice.

## 9. Operational Concerns — The Huey-Database-Sharing Question

**Recommendation: use a separate SQLite file for Huey's job store**, distinct from ATHENA AI-BRAIN's metadata database. Huey's own guide confirms file-sharing is a supported, intended usage pattern (not a hack) — this is a judgment call, not a compatibility constraint. Reasons to separate anyway:

1. **Write-lock isolation**: WAL still permits only one writer at a time per database *file*. Combining ATHENA AI-BRAIN's metadata writes (note updates, provenance records, FTS5-trigger-driven index maintenance) with Huey's per-task state writes (queue pop/push, schedule updates, retries) would make bursts of task churn compete with metadata writes for the same single-writer slot, and vice versa.
2. **Schema/migration independence**: Huey owns and evolves its own schema opaquely across library versions; keeping it inside ATHENA AI-BRAIN's own schema-versioned file would couple the migration runner to an external library's internal DDL it has no business tracking.
3. **Operational separation**: backups/restores/integrity checks on ATHENA AI-BRAIN's durable metadata shouldn't need to also handle the job queue's WAL/shm files, and vice versa — Huey's queue state is disposable/re-derivable (in-flight/pending jobs), while ATHENA AI-BRAIN's metadata is durable knowledge state; treating them identically for backup purposes is the wrong default.
4. Multi-process Huey workers (`huey_consumer` with `worker_type=process`) already handle the multi-process WAL case fine — this is Huey's designed use case — so process count alone doesn't force separation; write-contention and backup blast-radius do.

A practical corollary: `PRAGMA busy_timeout` is per-connection and resets to zero on every new connection — every connection opener (ATHENA AI-BRAIN's own app connections and every Huey worker process) must explicitly set it, or writers get immediate `SQLITE_BUSY` under contention rather than retrying gracefully. This applies regardless of the file-sharing decision.

## 10. Recommendation

**Hand-rolled thin repository layer over `aiosqlite`**: small, explicit, typed async functions per query (using `dataclasses`/`TypedDict`/`NamedTuple` for row shapes), parameterized SQL, a minimal `PRAGMA user_version`-driven migration runner (numbered `.sql` files, a well-precedented ~30-line pattern), and FTS5 external-content tables with trigger-based sync written directly per SQLite's documented pattern.

**Peewee is the recommended fallback/alternative** if the team wants ORM-level convenience for non-FTS5 CRUD without sacrificing FTS5 quality — it's the only library researched with genuine first-class FTS5 support, and its shared authorship/design philosophy with Huey is a soft but real alignment benefit. Its newer async maturity and thinner migration tooling are the reasons it's ranked second rather than first.

**SQLAlchemy Core** (not the ORM, not SQLModel) remains viable if ATHENA AI-BRAIN's non-FTS5 metadata queries prove more complex than currently expected, but should not be adopted by default given its documented transactional-driver caveat and the fact that it provides no real FTS5 advantage over the hand-rolled approach.

**SQLModel is not recommended** — it solves API request/response validation, a problem ATHENA AI-BRAIN's internal metadata store doesn't have, while inheriting every SQLAlchemy caveat with no offsetting benefit.

**Huey's job-store file should be separate from ATHENA AI-BRAIN's metadata database.**

## 11. References

- [aiosqlite PyPI](https://pypi.org/project/aiosqlite/) · [GitHub](https://github.com/omnilib/aiosqlite) · [Changelog](https://aiosqlite.omnilib.dev/en/stable/changelog.html) · [Issue #14 — performance](https://github.com/omnilib/aiosqlite/issues/14) · [Issue #19 — transactions](https://github.com/omnilib/aiosqlite/issues/19)
- [Python `sqlite3` stdlib docs](https://docs.python.org/3/library/sqlite3.html)
- [SQLAlchemy PyPI](https://pypi.org/project/SQLAlchemy/) · [SQLite dialect docs (2.0)](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html) · [Asyncio docs](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) · [2.1.0b1 announcement](https://www.sqlalchemy.org/blog/2026/01/21/sqlalchemy-2.1.0b1-released/) · [Discussion #9466 — FTS5](https://github.com/sqlalchemy/sqlalchemy/discussions/9466) · [Issue #4867 — FTS5 reflect](https://github.com/sqlalchemy/sqlalchemy/issues/4867)
- [Alembic PyPI](https://pypi.org/project/alembic/) · [Async cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html)
- [SQLModel GitHub/releases](https://github.com/fastapi/sqlmodel/releases) · [Release notes](https://sqlmodel.tiangolo.com/release-notes/) · [Discussion #597](https://github.com/fastapi/sqlmodel/discussions/597) · [Discussion #1453](https://github.com/fastapi/sqlmodel/discussions/1453)
- [Peewee GitHub](https://github.com/coleifer/peewee) · [PyPI](https://pypi.org/project/peewee/) · [Async docs](https://docs.peewee-orm.com/en/latest/peewee/asyncio.html) · [Charles Leifer — "Peewee 4: Async, JSON, Eager-Loading and Types"](https://charlesleifer.com/blog/peewee-4-async-json-eager-loading-and-types/) · [SQLite extensions (FTS5Model) docs](https://docs.peewee-orm.com/en/latest/peewee/sqlite.html)
- [Huey GitHub](https://github.com/coleifer/huey) (`huey/storage.py`) · [Guide](https://huey.readthedocs.io/en/latest/guide.html) · [PyPI](https://pypi.org/project/huey/)
- [SQLite official FTS5 documentation](https://sqlite.org/fts5.html) · [SQLite official WAL documentation](https://www.sqlite.org/wal.html)
- [sqlite-utils (Simon Willison) GitHub](https://github.com/simonw/sqlite-utils)
- [Litestream](https://litestream.io/)

## 12. Open Questions

- Should the hand-rolled migration runner be a genuinely minimal ~30-line implementation, or should a very small existing library be adopted instead if one fits the "small composable modules" bar? (Low-stakes, can be decided at implementation time.)
- Should Peewee be prototyped in parallel as a fallback before committing fully to the hand-rolled approach, given how close the research rated the two options?
- What connection-management pattern (single long-lived connection vs. small pool) best fits ATHENA AI-BRAIN's actual concurrency needs once Huey and the MCP server are both live? Needs verification during Phase 1 prototyping.

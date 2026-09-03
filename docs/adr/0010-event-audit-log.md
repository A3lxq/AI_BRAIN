# ADR-0010: Event Audit/Replay Log

- **ID:** ADR-0010
- **Title:** Event Audit/Replay Log (`events` table)
- **Status:** Accepted
- **Date proposed:** 2026-08-27
- **Date accepted:** 2026-08-27
- **Depends on:** ADR-0002 (Huey/SQLite job queue), ADR-0004 (SQLite access layer, dual-DB decision), ADR-0009 (filesystem event architecture)

## Context

Formalizing ATHENA AI-BRAIN's event model (`docs/EVENT_MODEL.md`) surfaced a taxonomy of ~22 concrete event types across five domains (filesystem, Git, job lifecycle, duplicate detection, reconciliation) that the master specification's event model (§5) requires to be "durable enough to recover from failures." Huey (ADR-0002) already provides durable execution for jobs, but a large share of this taxonomy is not a Huey job at all: `fs.path_changed` occurs before any job exists, `vault.note_created` can be emitted directly by a synchronous MCP tool call that never touches Huey, and `git.commit_completed` is a subprocess call, not a job. No existing accepted ADR provides a durable, queryable record spanning these domains, and none was asked to — this is genuinely new infrastructure discovered during event-model design, not a gap in a prior decision.

Full design rationale: `docs/EVENT_MODEL.md` §2.1 (written as this ADR's Context/Decision source material).

## Decision

**Accepted:** Add a narrow, append-only `events` table to ATHENA AI-BRAIN's own metadata SQLite database (the file ADR-0004 already separated from Huey's `SqliteHuey` job-store file) — **not** inside Huey's job-store file, and **not** a duplicate of Huey's internal job-state tables.

```sql
CREATE TABLE events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL UNIQUE,
    event_type      TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    occurred_at     TEXT NOT NULL,
    source          TEXT NOT NULL,
    correlation_id  TEXT NOT NULL,
    causation_id    TEXT,
    idempotency_key TEXT,
    actor           TEXT,
    payload_json    TEXT NOT NULL
);
CREATE INDEX idx_events_correlation ON events(correlation_id);
CREATE INDEX idx_events_type_time   ON events(event_type, occurred_at);
CREATE INDEX idx_events_idempotency ON events(idempotency_key);
```

Every event conforms to the envelope schema defined in `docs/EVENT_MODEL.md` §2 (`event_id`, `event_type`, `schema_version`, `occurred_at`, `source`, `correlation_id`, `causation_id`, `idempotency_key`, `actor`, `payload`). The table is used strictly as an append-only audit/replay/correlation log of **domain-meaningful** transitions — it records `job.enqueued`/`job.started`/`job.retried`/`job.completed`/`job.failed`/`job.cancelled` as a thin translation layer, not Huey's full internal retry/backoff representation.

The maintainer reviewed the design and accepted this ADR as proposed on 2026-08-27.

## Alternatives Considered

| Option | Verdict |
|---|---|
| Rely solely on Huey's own job-store tables for durability/audit | Rejected — different scope (Huey only knows about jobs it executed; most of the taxonomy isn't a job at all), different question (Huey answers "what is job X's current state," not "what happened, in what order, across all domains, correlated end-to-end"), and different lifespan (ADR-0004 already classified Huey's queue state as disposable/re-derivable, while domain events are provenance-adjacent durable facts per master spec §9 and CLAUDE.md rule 24). |
| No dedicated event log at all — rely on ad hoc logging (stdout/log files) for traceability | Rejected — an unstructured log cannot be queried by `correlation_id` to reconstruct a causal chain ("filesystem event → debounce → job → index update → MCP notification"), which the master specification's durability requirement and `EVENT_MODEL.md`'s traced primary pipeline both depend on; it also isn't a source `vault_status`/`note_provenance`/`system_diagnostics` (ADR-0007) can query structurally. |
| Store the event log inside Huey's existing SQLite file | Rejected — couples ATHENA AI-BRAIN's own schema-versioning story to an external library's opaque, independently-evolving internal schema, exactly the write-contention and coupling concerns ADR-0004 already used to justify keeping the two databases separate. |
| Mirror Huey's full internal retry/backoff state into the `events` table | Rejected — redundant with Huey's own authority over "current live job state" and would create two sources of truth for the same fact; the `events` table only needs the domain-meaningful transitions, not a full internal representation. |

## Rationale

1. **Different scope, question, and lifespan than Huey's own state**, as detailed in `EVENT_MODEL.md` §2.1 — this is not solved by anything already accepted.
2. **Directly satisfies the master specification's durability requirement** ("events should be durable enough to recover from failures") for the pre-job segment of the pipeline (a raw filesystem signal, before any Huey job exists), which would otherwise be in-memory/log-line ephemeral only.
3. **Gives `vault_status`, `note_provenance`, and `system_diagnostics` (ADR-0007) a structural query surface** for job-failure history, reconciliation summaries, and correlation-chain tracing that no other accepted component provides.
4. **Minimal, narrowly-scoped addition** — a single table plus three indexes, consistent with the constitution's "small composable modules" and "do not add dependencies beyond what's needed" (this adds no new dependency at all, only a table in an already-accepted database).
5. **Does not duplicate Huey's own job-state authority** — the design explicitly keeps Huey as the sole source of truth for "what is job X's live state right now," with the `events` table serving only as the durable historical/correlation record.

## Consequences

- The event envelope (`docs/EVENT_MODEL.md` §2) must be implemented as a shared `Event` dataclass/TypedDict used by every emitter (debounce layer, job-completion translator, Git wrapper, MCP tool handlers) — a required Phase 1 component.
- Every domain-meaningful transition in the taxonomy (`docs/EVENT_MODEL.md` §1) must append a row to this table at the point specified in that document's pipeline walkthrough (§3) — this is now a concrete implementation obligation, not just a design description.
- **Retention is an open sizing decision, deliberately not resolved here**: a periodic Huey task (mirroring the Git-backup job pattern) should archive/prune old `events` rows once table growth is actually measured, per the constitution's "do not optimize prematurely" — no retention policy is mandated by this ADR.
- The interim MCP `job_status` tool (ADR-0007) will query this table (joined with Huey's live state) to enrich terminal-state responses with domain-completion payloads (e.g., `index.update_completed`'s chunk counts) — this is now a concrete dependency of that tool's implementation.
- This table's presence does not change any other accepted ADR's decision — it is additive infrastructure, not a redesign.

## References

See `docs/EVENT_MODEL.md` §1–§2 for the full event taxonomy, envelope schema, and detailed rationale this ADR formalizes.

## Open Questions

Resolved: the maintainer accepted this ADR as proposed on 2026-08-27, with no modifications requested.

Remaining open items, carried forward as implementation-time decisions:
- Should retention/pruning be designed now or deferred until table growth is actually measured in Phase 1? Recommend deferring, per the constitution's "measure before optimizing."
- Should `event_id` use UUIDv4 (simplest) or UUIDv7 (time-ordered, marginally more useful for the table's primary key ordering)? Low-stakes, decide at implementation time.

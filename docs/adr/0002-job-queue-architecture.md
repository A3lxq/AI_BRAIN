# ADR-0002: Job/Queue Architecture for AI_BRAIN

- **ID:** ADR-0002
- **Title:** Job/Queue Architecture for AI_BRAIN
- **Status:** Accepted
- **Date proposed:** 2026-08-22
- **Date accepted:** 2026-08-22
- **Depends on:** ADR-0001 (runtime: Python, accepted 2026-08-22)

## Context

AI_BRAIN needs to run long-running work — indexing (chunk + embed + upsert to Qdrant), research/web-ingestion, reindexing, duplicate-detection/merge, and Git commit/push — without blocking event handlers driven by filesystem events (`watchdog`) or MCP tool calls. Per `docs/TESTING_STRATEGY.md`, this work must survive interrupted indexing, database failure, partial writes, network failure, duplicate events, and repeated jobs (idempotency). Some jobs are periodic (Git backups, full reindex, stale-knowledge sweeps). The deployment target is a single local Kali Linux machine — local-first, low-ops, single user.

Seven candidates were researched against ecosystem maturity, async-native fit, durability guarantees, broker/infrastructure requirements, retry/dead-letter handling, scheduling, security (serialization format), resource footprint, testability, and maintenance status, using current 2026 primary sources. Full findings: [`docs/research/2026-08-22_job_queue_architecture.md`](../research/2026-08-22_job_queue_architecture.md).

Key finding: **every mainstream option (Celery, Dramatiq, Taskiq, RQ, arq) requires Redis or RabbitMQ** — infrastructure the master specification's local-first principle doesn't call for. Two carry additional disqualifying issues: RQ defaults to pickle serialization with an open, unresolved CVSS 8.1 GitHub issue; arq's original maintainer has explicitly declared it maintenance-only.

## Decision

**Accepted:** Use **Huey with its SQLite backend** (`SqliteHuey`) as AI_BRAIN's job/queue library, with its default pickle serializer swapped to Huey's built-in `SignedSerializer` or a JSON-based serializer.

The maintainer reviewed the research and comparison and accepted this ADR as proposed on 2026-08-22.

## Alternatives Considered

| Option | Verdict |
|---|---|
| Celery | Rejected — requires Redis/RabbitMQ, no native asyncio support, adds a separate unmonitored `beat` process for scheduling. Built for distributed clusters, not a single-machine tool. |
| Dramatiq | Rejected as primary — best security posture of the broker-based options (JSON-only, no pickle) with genuine retry/dead-letter maturity, but still requires standing up Redis/RabbitMQ, which AI_BRAIN doesn't otherwise need. Held in reserve if requirements grow toward multi-machine scale. |
| Taskiq | Rejected — no durable local broker option (only a non-durable in-memory broker); self-classified "Alpha" on PyPI despite active development. |
| RQ | Rejected — synchronous by design (real friction bridging into an asyncio codebase, multiple open GitHub issues), and defaults to pickle serialization with an open, unresolved High-severity (CVSS 8.1) security issue. |
| arq | Rejected — requires Redis, defaults to pickle, and its original maintainer has explicitly declared the project maintenance-only (a real long-term-viability risk directly counter to this project's "leave a recoverable project state" ethos). |
| Hand-rolled asyncio + SQLite queue | **Strong alternative, not selected as primary.** A well-precedented pattern (not exploratory) that maximizes control and dependency minimalism. Not chosen because Huey already provides the same SQLite-native durability plus retry and cron scheduling as a maintained, tested library — adopting it avoids AI_BRAIN having to build and test its own crash-recovery sweep, backoff policy, and dead-letter handling from scratch, while remaining a small, focused dependency consistent with the constitution's "small composable modules" principle. |

## Rationale

1. **Zero new infrastructure.** Huey's SQLite backend requires no Redis, RabbitMQ, or other external service — it reads/writes a SQLite file, matching AI_BRAIN's already-accepted SQLite-centric metadata/state design and its local-first principle exactly.
2. **Durability matches the Testing Strategy's requirements.** Huey's SQLite backend defaults to WAL mode; job state survives a process crash/restart, satisfying the mandatory interrupted-indexing/partial-write recovery tests.
3. **Retry and cron scheduling are already built and tested**, avoiding AI_BRAIN having to implement and test its own backoff/dead-letter/periodic-scheduling logic — directly reduces the surface area the constitution's "every implementation gets tests" rule would otherwise apply to custom reliability code.
4. **Security posture is addressable.** Huey defaults to pickle like several rejected candidates, but (a) its SQLite backend is a local file with no network exposure, meaningfully shrinking the practical attack surface pickle depends on, and (b) it ships a built-in `SignedSerializer` and pluggable serializer interface, so AI_BRAIN can swap off pickle entirely as a day-one configuration choice rather than an unresolved open issue (unlike RQ).
5. **Actively maintained with a long track record** (continuous history since 2011, "Production/Stable" PyPI classifier, current release Aug 2026) — favorable on the long-term-viability criterion where arq scored poorly.
6. **The one real integration cost — bridging Huey's synchronous worker core into AI_BRAIN's asyncio codebase via `aget_result()`** — is well-documented and bounded, not an open design question.

## Consequences

- The MCP server will enqueue jobs and query status via Huey, using the community `enqueue_job`/`get_job_status` two-tool convention until AI_BRAIN's target MCP SDK version supports the official `io.modelcontextprotocol/tasks` extension (added in the 2026-07-28 spec revision).
- Huey's default pickle serializer must be swapped to `SignedSerializer` (or JSON) as part of Phase 1 setup — this is a required security configuration step, not optional, per the constitution's security-first posture.
- Periodic jobs (Git backup commits, full reindex, stale-knowledge sweeps) will use `@huey.periodic_task` cron scheduling.
- This decision explicitly does not scale beyond one machine — acceptable at AI_BRAIN's current single-user scope, but should be revisited (with its own ADR) if multi-machine or multi-user deployment is ever pursued.
- If Huey's sync-core/async-bridge integration proves awkward once prototyped against a real job type (e.g. single-note indexing), the hand-rolled SQLite-queue alternative documented in the research remains a credible fallback and should be reconsidered rather than forcing an unsuitable fit.

## References

See [`docs/research/2026-08-22_job_queue_architecture.md`](../research/2026-08-22_job_queue_architecture.md) §11 for the full primary-source citation list.

## Open Questions

Resolved: the maintainer accepted this ADR as proposed on 2026-08-22, with no modifications requested.

Remaining open item, carried forward as an implementation-time check rather than a blocking question: validate Huey's sync-core/`aget_result()` async bridge against one real job type (e.g. single-note indexing) early in Phase 1, given how close the research rated Huey against the hand-rolled alternative. If that prototype reveals excessive friction, fall back to the hand-rolled approach per the Consequences section above.

# Research: Job/Queue Architecture for ATHENA AI-BRAIN

- **Research date:** 2026-08-22
- **Researcher:** Claude Code (ATHENA AI-BRAIN Phase 0)
- **Status:** Candidate evaluation — feeds ADR-0002 (job/queue architecture)
- **Depends on:** ADR-0001 (Python runtime, accepted 2026-08-22)

## 1. Executive Summary

Every mainstream Python task-queue library (Celery, Dramatiq, Taskiq, RQ, arq) forces a Redis or RabbitMQ dependency — infrastructure ATHENA AI-BRAIN's local-first, single-machine, SQLite-centric design doesn't otherwise need. Two of them carry additional red flags: RQ defaults to pickle serialization with an open, unresolved High-severity (CVSS 8.1) GitHub issue, and arq's original maintainer has explicitly declared it maintenance-only. **Huey**, however, natively supports SQLite as a durable broker/result-store with zero required dependencies, is actively maintained and "Production/Stable"-classified, and already provides retry, dead-letter-equivalent, and cron scheduling. A **hand-rolled asyncio+SQLite job queue** is a close second — a well-precedented pattern (not exploratory), maximizing control and dependency minimalism at the cost of owning the reliability engineering (crash recovery, backoff, dead-letter) yourself.

## 2. Problem Being Solved

ATHENA AI-BRAIN needs to run long-running work — indexing (chunk + embed + upsert to Qdrant), research/web-ingestion, reindexing, duplicate-detection/merge, and Git commit/push — without blocking event handlers (filesystem events from `watchdog`, MCP tool calls). Per the Testing Strategy, this work must survive interrupted indexing, database failure, partial writes, network failure, duplicate events, and repeated jobs (idempotency) — job state must not be silently lost on a crash. Some jobs are periodic (Git backups, full reindex, stale-knowledge sweeps), so cron-like scheduling is needed. The deployment target is a single local Kali Linux machine, not a distributed cluster.

## 3. Technology Overview

Seven candidates were researched: Celery, Dramatiq, Taskiq, RQ, arq, Huey, and a hand-rolled asyncio+SQLite queue.

## 4. Architecture Fit

ATHENA AI-BRAIN already committed (ADR-0001) to Python + asyncio + SQLite as its metadata/state store. The job/queue layer should compose with this rather than introduce a second storage system or an external broker service, per the constitution's "small composable modules" and "do not add dependencies beyond what's needed" principles. This significantly narrows the field before individual library quality is even considered.

## 5. Alternatives Considered — Comparison Against Evaluation Criteria

| Candidate | Broker/infra | Async-native | Durability model | Default serializer | Cron scheduling | Maintenance (2026) | Verdict |
|---|---|---|---|---|---|---|---|
| **Celery** | Redis/RabbitMQ (required) | No — no native asyncio support, only third-party bridges | Broker-dependent; no production-safe SQLite broker | JSON default (pickle optional, historical CVE-2021-23727) | Yes, but via a separate, unmonitored `beat` process | Active (5.6.3, Mar 2026) | Overkill — built for distributed multi-worker clusters |
| **Dramatiq** | Redis/RabbitMQ (required) | Thread-bridged via official `AsyncIO` middleware, not fully native | Broker-dependent | **JSON only** (no pickle option — best security posture of the broker-based options) | Not built-in (relies on thinly-maintained `periodiq` add-on) | Active (2.2.0, Jun 2026) | Good security, still needs a broker |
| **Taskiq** | RabbitMQ/NATS/Redis recommended; `InMemoryBroker` explicitly non-durable | Native asyncio-first | No durable local backend at all | JSON default, pluggable | Built-in cron, but no leader election (duplicate execution risk with >1 scheduler instance) | Active but **self-classified "Alpha"** (0.12.5, Aug 2026) | No durable local option; not production-hardened |
| **RQ** | Redis (required) | **No** — synchronous by design, documented friction with asyncio (multiple open GitHub issues) | Redis persistence config-dependent | **Pickle default**, open unresolved High-severity (CVSS 8.1) issue (#2389) | Built-in but flagged "beta" | Very active, "Production/Stable" (2.11.0, Aug 2026) | Sync + pickle-by-default risk |
| **arq** | Redis (required) | Native asyncio, well-designed | Redis persistence (AOF must be explicitly configured) | Pickle default (swappable to msgpack) | Built-in, good | **Self-declared maintenance-only** by original maintainer (Issue #510); community discussion titled "ARQ is officially dead" | Real organizational/long-term-risk signal |
| **Huey** | **None required — SQLite native** (also supports Redis/Postgres/filesystem/memory) | Sync core + async bridge via `aget_result()` | **SQLite WAL**, matches ATHENA AI-BRAIN's existing metadata store | Pickle default, but built-in `SignedSerializer` (HMAC-SHA1) and pluggable JSON/MessagePack | Built-in, first-class (`@huey.periodic_task`) | Active, healthy, continuous history since 2011 (3.3.4, Aug 2026), "Production/Stable" | **Strongest fit** |
| **Hand-rolled asyncio+SQLite** | None | Fully native (your own code) | SQLite WAL, full control (`BEGIN IMMEDIATE` compare-and-swap claim pattern) | Your choice (JSON recommended) | DIY (`next_run_at` polling), trivial for ATHENA AI-BRAIN's ~3-4 periodic job types | N/A — you own it | **Strong fit, more code/tests to own** |

## 6. ATHENA AI-BRAIN Relevance

- **Huey's SQLite backend** (`SqliteHuey`/`CySqliteHuey`) defaults to WAL mode — directly consistent with ATHENA AI-BRAIN's existing SQLite-centric app-state design. Task priorities, locking (`@huey.lock_task`), retries, result storage, and scheduling all work identically on SQLite as on Redis. The gap versus Redis (no multi-machine fan-out, no low-latency push — SQLite polls instead) is irrelevant at ATHENA AI-BRAIN's single-user, single-machine scale.
- **Hand-rolled** is not exploratory territory: multiple independent sources converge on the same design (a `jobs` table with status/attempts/next_run_at/locked_by columns; atomic claim via `BEGIN IMMEDIATE` + guarded `UPDATE`; WAL mode; a startup "reaper" sweep to requeue stale `running` rows past a visibility timeout; retry via incrementing `attempts` + exponential backoff; dead-letter via a `status='dead'` terminal state; idempotency via content-hash-derived keys — which also solves duplicate-`watchdog`-event handling for free). SQLite's documented concurrency ceiling (issues reported only at 1000+ concurrent writers, or 50-100 writes/sec) is two to three orders of magnitude above ATHENA AI-BRAIN's expected load.
- **MCP integration**: the core MCP spec (through 2025-03-26) only supports in-request progress notifications, not "return early, poll later." The **2026-07-28 spec revision** added an official `io.modelcontextprotocol/tasks` extension (`tasks/get`/`tasks/update` polling + `subscriptions/listen`) that partially standardizes this. Until ATHENA AI-BRAIN's chosen MCP SDK version fully adopts it, the community-convention pattern (an `enqueue_job` tool returning a job id, plus a separate `get_job_status` tool) is the practical design, backed by whichever job store is chosen here.

## 7. Security

- **Celery**: CVE-2021-23727 (deserialization injection via result backend metadata, fixed in 5.2.2); downstream pickle-RCE incidents recur in projects that enable pickle.
- **RQ**: pickle is the *default* serializer; RQ's own docs warn it's insecure against untrusted broker data; an open GitHub issue (#2389) rates this CVSS 8.1 (High) and remains unresolved.
- **arq / Huey**: both default to pickle, but arq's Redis broker is a separate network-exposed service (larger attack surface if ever misconfigured), while Huey's SQLite backend is a local file with no network exposure — the practical attack surface pickle depends on (a remote party writing to your broker) doesn't exist for ATHENA AI-BRAIN's threat model as currently scoped. Huey ships a built-in `SignedSerializer` (HMAC-SHA1) and a documented pluggable serializer interface; per the constitution's security-first posture, ATHENA AI-BRAIN should still swap off pickle (SignedSerializer or JSON) regardless of the practical risk being lower.
- **Hand-rolled**: serializer choice is fully controlled; JSON is the straightforward safe default since job payloads (file paths, note ids, job parameters) don't need Python-object fidelity.

## 8. Performance

Not a differentiating criterion at ATHENA AI-BRAIN's scale — SQLite's single-writer constraint and WAL-mode read concurrency comfortably exceed the expected job volume (a single user's indexing/research/Git jobs) by two to three orders of magnitude per the concurrency-ceiling research above. No further performance evaluation was necessary.

## 9. Operational Concerns

- Every Redis/RabbitMQ-based option adds a second long-running service to operate, monitor, and back up on a single-user local machine — directly against the "local-first, low-ops" principle in the master specification.
- Celery additionally requires a separate `beat` process for scheduling, which is an unmonitored single point of failure for periodic jobs.
- Taskiq's scheduler has no leader-election — running more than one instance (e.g. after a crash-restart overlap) causes duplicate execution.
- Huey's integration cost is real but bounded: bridging its sync worker core into ATHENA AI-BRAIN's asyncio codebase via `aget_result()` is a well-understood, well-documented pattern, not an open design question.
- Hand-rolled's integration cost is also real but bounded differently: the reliability engineering (crash-detection sweep, backoff policy, dead-letter, heartbeats, graceful shutdown) must be built and tested by ATHENA AI-BRAIN itself, comparable in effort to what would be needed to properly harden Huey's SQLite path anyway (custom serializer swap, monitoring tooling).

## 10. Recommendation

**Huey with the SQLite backend** is the recommended choice: it is the only widely-used, actively-maintained, "Production/Stable"-classified library that natively supports SQLite as a durable broker/result-store with zero required dependencies, and it already provides retry and cron scheduling that ATHENA AI-BRAIN would otherwise have to build and test itself. The hand-rolled approach remains a credible, well-precedented alternative and should be revisited if Huey's sync-core/async-bridge integration proves awkward in practice, or if ATHENA AI-BRAIN's job semantics grow beyond what Huey's task model comfortably expresses.

**Explicitly not recommended, as currently scoped:**
- Celery — too heavy/ops-intensive for a single-machine tool.
- RQ — synchronous (real bridging cost in an asyncio codebase) plus an open, unresolved pickle-by-default security issue.
- Taskiq — no durable local broker option and a self-classified Alpha status.
- arq — forces a Redis dependency ATHENA AI-BRAIN doesn't otherwise need, and its original maintainer has declared it maintenance-only, a real long-term-viability risk.

## 11. References

- Celery: [PyPI](https://pypi.org/project/celery/) · [Releases](https://github.com/celery/celery/releases) · [Backends and Brokers](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/index.html) · [Security docs](https://docs.celeryq.dev/en/stable/userguide/security.html) · [NVD CVE-2021-23727](https://nvd.nist.gov/vuln/detail/cve-2021-23727)
- Dramatiq: [Changelog](https://dramatiq.io/changelog.html) · [User Guide](https://dramatiq.io/guide.html) · [Cookbook](https://dramatiq.io/cookbook.html)
- Taskiq: [PyPI](https://pypi.org/pypi/taskiq/json) · [GitHub](https://github.com/taskiq-python/taskiq) · [Brokers doc](https://taskiq-python.github.io/available-components/brokers.html) · [Scheduling docs](https://taskiq-python.github.io/guide/scheduling-tasks.html)
- RQ: [PyPI](https://pypi.org/pypi/rq/json) · [Docs](https://python-rq.org/docs/) · [Cron docs](https://python-rq.org/docs/cron/) · [Issue #2389 — pickle security](https://github.com/rq/rq/issues/2389)
- arq: [PyPI](https://pypi.org/project/arq/) · [GitHub Releases](https://github.com/python-arq/arq/releases) · [Docs](https://arq-docs.helpmanual.io/) · [Issue #510 — maintenance-only](https://github.com/python-arq/arq/issues/510)
- Huey: [PyPI](https://pypi.org/project/huey/) · [GitHub](https://github.com/coleifer/huey) · [Guide](https://huey.readthedocs.io/en/latest/guide.html) · [Async integration doc](https://huey.readthedocs.io/en/latest/asyncio.html)
- Hand-rolled pattern: [SQLite WAL docs](https://www.sqlite.org/wal.html) · [SQLite Locking docs](https://www.sqlite.org/lockingv3.html) · [SQLite forum thread](https://sqlite.org/forum/forumpost/0f73062d64) · [Jason Gorman blog](https://jasongorman.uk/writing/sqlite-background-job-system/) · [SkyPilot concurrency blog](https://skypilot.ai/blog/abusing-sqlite-to-handle-concurrency/) · [litequeue](https://github.com/litements/litequeue) · [persist-queue](https://github.com/peter-wangxu/persist-queue) · [SAQ (reference design)](https://github.com/tobymao/saq) · [procrastinate (reference design)](https://github.com/procrastinate-org/procrastinate) · [APScheduler](https://pypi.org/project/APScheduler/)
- MCP async job status: [MCP Progress spec](https://modelcontextprotocol.io/specification/2025-03-26/basic/utilities/progress) · [MCP 2026-07-28 spec blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/) · [Tasks extension discussion](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/982) · [Python SDK docs](https://py.sdk.modelcontextprotocol.io/get-started/installation/)

## 12. Open Questions

- Does Huey's sync-core/`aget_result()` async bridge integrate cleanly enough with ATHENA AI-BRAIN's asyncio-native MCP server, or does it introduce enough friction to favor the hand-rolled approach instead? (Recommend prototyping both against one real job type — e.g. single-note indexing — before committing further.)
- Should Huey's default pickle serializer be swapped to `SignedSerializer` or JSON immediately, or is this deferred to the security-threat-model design step for the indexing subsystem?
- Should the MCP `enqueue_job`/`get_job_status` tool pair be designed now against the community convention, or deferred until the official `tasks` extension is supported by ATHENA AI-BRAIN's target MCP SDK version?

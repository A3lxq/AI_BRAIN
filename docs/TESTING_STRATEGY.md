# AI_BRAIN — Testing Strategy

## Testing layers

### Unit

Pure functions and isolated components.

### Integration

Filesystem, SQLite, vector database, embeddings, Git, and provider integrations.

### Contract

MCP tools and provider abstractions.

### Retrieval evaluation

Use a curated evaluation corpus containing:
- representative notes,
- questions,
- expected relevant documents/chunks,
- relevance judgments.

Track metrics such as:
- Recall@K
- Precision@K
- MRR
- nDCG where appropriate
- latency

### Security

Test:
- path traversal,
- malicious metadata,
- prompt injection,
- poisoned documents,
- unsafe shell/Git arguments,
- secrets leakage,
- unauthorized MCP operations.

### Recovery

Simulate:
- interrupted indexing,
- database failure,
- partial writes,
- Git failure,
- network failure,
- duplicate events,
- repeated jobs.

## Quality gate

A feature cannot be marked complete solely because a happy-path test passes.

---

## Elaboration (Phase 0 exit-criteria deliverable, 2026-08-26)

The sections above state the principles. This elaboration makes them concrete against AI_BRAIN's actual accepted architecture (ADR-0001 through ADR-0009), so a Phase 1 implementer has real test cases to build against, not just categories. Security *test methodology* is covered here; the security *threat model* itself lives in `SECURITY_MODEL.md` and is only cross-referenced.

### Per-subsystem test coverage plan

**Vault Watcher / Debouncer (ADR-0009).** Unit: a burst of 50 rapid modify events for one path enqueues exactly one reindex job; two events on two different paths enqueue two separate jobs; a move event normalizes into two independent "path changed" signals; `.git`/`.obsidian`/plugin-cache paths never reach the debounce map; the "path settled" callback fires exactly once per settled path (no double-fire race); a delete event with a still-open debounce window resolves to a single terminal signal. Integration: a real `watchdog.Observer` against a temp directory simulating an actual editor temp-file-write+rename produces exactly one enqueue; a cross-boundary move (confirmed watchdog issue #308) degrades to delete+create but still yields correct final index state; the debounce layer's direct synchronous Huey-enqueue call (no asyncio bridge, per ADR-0009) doesn't block or deadlock the asyncio event loop under concurrent load; a low `fs.inotify.max_user_watches` produces a clear, actionable startup error, not silent watch-dropping. Recovery: modify N files while the watcher is stopped, restart, assert startup reconciliation reindexes exactly those N; simulate `IN_Q_OVERFLOW` (or document as a manual load test) and assert the next reconciliation cycle converges regardless of lost events; kill mid-burst and assert reconciliation catches the in-flight path. Security: a symlink inside the vault pointing outside it must not cause the watcher or its triggered job to read/index outside the configured vault root; a path containing `../` is rejected or canonicalized before use.

**Job Queue — Huey/SQLite (ADR-0002).** Unit: assert Huey is configured with `SignedSerializer`/JSON, never pickle — a config-assertion test that fails loudly on drift; job-layer idempotency (same input invoked twice performs expensive work once); `huey.lock_task` prevents two concurrent jobs for the same path; retry/backoff policy distinguishes retryable vs. non-retryable error types; periodic (`@huey.periodic_task`) jobs are registered with the expected schedule (assert the schedule object, don't sleep-wait for real cron firing). Integration: enqueue against a real temp-file `SqliteHuey`, run a real consumer, assert state transitions and result retrieval via `aget_result()`; **validate the sync-core/`aget_result()` async bridge specifically against one real job type (single-note indexing) as a standing regression test, not a one-off manual check** — this is the explicit early-Phase-1 validation ADR-0002 flags; assert Huey's job-store file is distinct from AI_BRAIN's metadata file. Recovery: kill a worker mid-job and assert the job is recoverable on restart, with the reconciliation job as an independent second safety net if job-level recovery also fails; simulate `SQLITE_BUSY` and assert `busy_timeout`-bounded retry, never an indefinite hang; enqueue the same logical job twice in succession and assert the second is a safe no-op (no duplicate Qdrant upsert, no duplicate Git commit). Security: assert the configured serializer is never pickle in CI, read directly off the live Huey instance's serializer attribute; assert no job payload content is ever passed to anything that could interpret it as a shell command.

**SQLite Repository Layer (ADR-0004).** Unit: each repository function tested against a temp-file/`:memory:` DB, correct row shape on read, correct parameter binding on write; SQL-metacharacter-laden input stored/retrieved literally, never executed; migration runner applies the numbered `.sql` sequence correctly, is idempotent on re-application, and fails cleanly (leaving `user_version` unchanged) on a broken migration file; FTS5 external-content sync triggers correctly mirror insert/update/delete into the FTS index, including empty/whitespace-only content. Integration: every new connection explicitly sets `PRAGMA busy_timeout` — assert via the real connection-opening code path, not documentation; two concurrent writers eventually both succeed via `busy_timeout` retry; WAL mode is actually enabled. Recovery: a corrupted/truncated DB file fails loudly with a clear diagnostic (surfaced via `system_diagnostics`) rather than silently returning empty results; a process killed mid-transaction leaves the DB in a fully-applied-or-fully-unapplied state on restart; sustained `SQLITE_BUSY` contention surfaces as a bounded, catchable error, tested with a timeout shorter than a hang would take.

**RAG Pipeline — Chunking/Embedding/Fusion/Reranking (ADR-0003, ADR-0008).** Unit (each stage standalone, no MCP/Qdrant/Huey dependency, per the decoupling requirement): chunking correctly handles a note's YAML frontmatter where present, and — per `DATA_MODEL.md` §0's real-vault finding — correctly uses conversational-turn headers (`# you asked`/`### USER`) as chunk boundaries for the legacy, frontmatter-less chat-export content that makes up most of the actual vault; chunking respects code-fence/table boundaries; empty/frontmatter-only/single-word notes don't raise; embedding is deterministic on identical input; an over-long chunk (>8192 tokens for BGE-M3) is truncated/split per a defined policy, not silently dropped; the hand-written cross-store RRF module produces the mathematically expected fused ranking against synthetic rank lists; the reranker ranks an obviously-relevant passage above an obviously-irrelevant one (a coarse sanity check — real quality measurement is the evaluation corpus's job, see below); duplicate detection flags near-identical notes (content hash differs by one character) and does not flag unrelated ones — the real vault's `Grok-_04.md`/`Grok-_04(1).md`-style filename pairs (per `DATA_MODEL.md` §2.6) are a concrete, non-hypothetical fixture source for this; every chunk carries a complete provenance record conforming to the W3C-PROV-derived schema. Integration: full pipeline (chunk → embed → sparse-vectorize → upsert to a real ephemeral Qdrant → hybrid query → fuse → rerank) against a small fixture vault retrieves a known-relevant note for a known query; re-embedding after a simulated model swap correctly re-derives vectors from retained original chunk text without re-reading the vault; querying via the collection alias returns correct results both before and immediately after an alias repoint. Security: a chunk containing an embedded instruction passes through unchanged as inert text — nothing in the pipeline interprets or executes chunk content; a note containing a plausible secret pattern is excluded from indexing by a pre-ingestion scan, or — until that scan exists (see `SECURITY_MODEL.md`'s P0 item) — this test exists as a tracked, visible `xfail`, not a silent gap.

**Qdrant Integration (ADR-0006, ADR-0008).** Unit: collection-config construction (vector size, distance, sparse-vector config) produces the expected request shape, testable against `QdrantClient(":memory:")` since this is non-fusion-critical. Integration (must run against a real, even ephemeral, Qdrant server — never `:memory:`, per ADR-0006): hybrid RRF fusion against a populated real collection returns results consistent with documented fusion semantics, specifically guarding against the local-mode fusion/prefetch parity bug (qdrant-client#713) by never testing fusion against local mode; sparse-vector (miniCOIL) upsert/query round-trips correctly; collection alias creation/query/repoint all behave as expected; the running container is confirmed reachable only on `127.0.0.1`. Recovery: Qdrant connection failure mid-upsert — assert the job fails cleanly, retries per Huey policy, and does **not** leave SQLite marked "indexed" while the Qdrant upsert actually failed (the dual-write consistency risk, tested explicitly, not just as a generic "Qdrant is down" smoke test); Qdrant unreachable at query time returns a clear degraded-service error (optionally FTS5-only fallback), never an unhandled exception across the MCP boundary; post-upgrade collection health verification (point count, sanity query) as the automated counterpart to the manual upgrade runbook (see Git Workflow below). Contract: a CI check flags any test using `:memory:` while also asserting fusion-specific behavior, guarding against drift from the ADR-0006 rule.

**Git Automation Module (ADR-0005).** Named security-sensitive in its own ADR — every mutating-operation test doubles as a security test. Unit: every mutating wrapper rejects a branch/ref/pathspec argument beginning with `-` before it reaches argv (allow-list validation as a first line of defense, independent of the `--` separator); every subprocess invocation is argument-list-only, never `shell=True` (a structural/grep-based CI check, not just a per-function test); the `--`/`--end-of-options` separator is verified per-subcommand against the actual installed git version (`checkout`/`reset` only gained it in git 2.43.1); the exit-code+stderr failure taxonomy correctly classifies merge conflict, auth failure, network failure, and nothing-to-commit; dry-run mode never mutates repository state (bit-for-bit unchanged HEAD/working-tree/remote-refs before and after). Integration (against a real, disposable local Git repo, not a mock): a commit round-trips correctly via real `git log`/`git show`; a simulated network-unreachable push failure classifies as network-failure and the local commit remains intact regardless of push outcome; a simulated auth failure classifies distinctly from network failure; if Dulwich's read-only convenience path is used, assert its `ProcessMergeDriver` surface (source of CVE-2026-42563) is never invoked anywhere in AI_BRAIN's code. Security: argument-injection attempts (`-`-prefixed branch/path values) are rejected at two independent layers (allow-list, and separately the `--` separator, tested independently so a regression in one is still caught by the other); path-traversal attempts via a Git pathspec are rejected; shell-metacharacter-laden commit-message-source strings are stored literally, never interpreted; a structural/coverage test enumerates every function the module exports and asserts none constructs `git reset --hard`, force-push, or branch-deletion; a commit containing a known secret pattern is blocked by the pre-commit/gitleaks hook before the module's `git_commit` wrapper is even invoked. Recovery: an interrupted Git operation (killed subprocess) leaves a repo state `git status` can still interpret, with the module detecting and clearing a stale `.git/index.lock` rather than failing generically.

**MCP Server / Tool Contract (ADR-0007).** Structural: every registered tool is backed by a business-logic function independently callable/testable with zero MCP context — enumerate registered tools and assert each wraps a separately-unit-tested function. Contract/behavior: `note_delete` without a prior MRTR confirmation echoing the exact path is rejected outright on a single-shot call; `note_merge` is unreachable without a prior `note_duplicates`/`duplicates_scan` context; `note_move` targeting an existing destination requires confirmation, a non-existent destination does not (the gate is conditional, not blanket); `note_create` targeting an existing path fails without overwriting (byte-identical original content after); `note_update`'s patch mode never silently falls through to full overwrite; `dry_run=true` produces zero observable side effects on every dry-run-capable tool (run as one parametrized test across all such tools, not hand-written per tool); `research_commit` defaults to `dry_run=true` when omitted — assert the default, not just that the parameter works; task-backed tools (`duplicates_scan`, `research_start`, `reindex_start`) return a job handle before the underlying work necessarily completes; `job_status`/`job_cancel` correctly mirror real underlying Huey job state; a structural test enumerating the full registered tool list asserts force-push/hard-reset/branch-delete are reachable by none of them. Security: a `note_delete` request whose path echo doesn't exactly match the target is rejected; a planted injection-style string in a fixture vault note is exercised end-to-end through real chunking→retrieval→whatever consumes it, asserting it never alters AI_BRAIN's own tool-call behavior (a behavioral/integration test, not a unit check); every mutating/destructive tool row in ADR-0007's table has a corresponding negative test attempting to skip its required confirmation; read-only tools (`system_diagnostics` etc.) never expose secrets/API keys/connection strings in their output.

### The retrieval evaluation corpus (Master Spec §6)

Build a hand-curated evaluation set of **30–60 representative notes** — for AI_BRAIN specifically, this should be drawn from the real vault sample validated in `DATA_MODEL.md` §0 (a mix of ChatGPT/Claude/Grok/Qwen-style chat exports and OWASP-style reference material), not synthetic fixtures alone, since the real corpus's structural quirks (no frontmatter, turn-header chunking, near-duplicate filename pairs) are exactly what needs to be evaluated against. For each note, author 2–5 realistic questions (including at least one requiring cross-note fusion, and at least one deliberately non-lexically-matching the target's vocabulary), hand-label expected relevant chunk(s) with binary or graded relevance judgments, and include a few deliberately unanswerable questions to catch confident-but-wrong top hits. Track Recall@K/Precision@K (K=3,5,10), MRR, nDCG@10 where graded judgments exist, and p50/p95 query latency. Store the corpus as versioned fixtures in Git, not generated at runtime.

**Re-run cadence**: run the full corpus automatically in CI on every change touching chunking, embedding, fusion, or reranking code — not a periodic manual check, since Master Spec §6 says quality "must be measured... before claiming a configuration is good," which only means something if it's enforced. Record each metric alongside the commit hash and model/config version; gate the build on any regression beyond a defined tolerance unless the change is an intentional, ADR-documented model swap. Because ADR-0008 is explicitly provisional (6–12 month re-evaluation window) and Qdrant collections are alias-addressed, the harness should support running the same corpus against two configurations (current vs. candidate model) side by side, targeting an explicit collection name rather than assuming the production alias — this is the actual evidence a future ADR superseding ADR-0008 would need to cite.

### Security test methodology

(Cross-referencing, not duplicating, `SECURITY_MODEL.md` — this is about *how* these tests are written, using AI_BRAIN's concrete design.) Maintain one shared, parametrized fixture of malicious path inputs (`../../etc/passwd`, absolute paths outside the vault root, symlink-based escapes, null-byte-embedded paths) and run it against every path-accepting entry point (`note_create`/`update`/`move`/`read`, the Git module's pathspec arguments, the vault watcher's own path normalization) so coverage doesn't silently gap when a new path-accepting tool is added. Test Git argument-injection at two independent layers (allow-list, and the `--` separator in isolation via a test double that disables the allow-list). Assert Huey's serializer is never pickle as a standing CI check reading the live config object, and that any `yaml.load` usage anywhere in the codebase is `yaml.safe_load` (grep-based CI check) — directly relevant since the real vault's chat-export content is exactly the untrusted-content class this guards. Scan captured logs/MCP responses in test runs for planted fake secrets, asserting they never leak. Run the prompt-injection/retrieved-content-conflation test (§ above) as an integration-level, not unit-level, check. Enumerate every mutating/destructive ADR-0007 tool row and assert a negative test exists for skipping its confirmation. Run `pip-audit`/equivalent as a periodic (not per-commit) CI job.

### Recovery / failure test scenarios (cross-subsystem matrix)

| Scenario | Mechanism under test | Expected behavior |
|---|---|---|
| Interrupted indexing (worker killed mid-job) | Huey job + reconciliation job | Job-level recovery on restart; reconciliation independently catches it if that fails too |
| SQLite locked/busy | Repository layer's `busy_timeout` | Bounded wait then success or a specific catchable error — never an indefinite hang |
| Qdrant failure during indexing | RAG pipeline + job retry | Clean failure, retried per policy; SQLite and Qdrant never diverge into "marked indexed but not upserted" |
| Qdrant failure during query | `vault_search`/`note_related` | Clear degraded-service response, never an unhandled exception across the MCP boundary |
| Git push failure — network vs. auth | Git automation module | Classified distinctly; local commit remains intact either way |
| Duplicate/repeated filesystem events | Debounce + job idempotency | Cheap no-ops at the job layer; no duplicate Qdrant upserts or Git commits |
| Process crash mid-DB-write | SQLite atomic commit | Fully-applied or fully-unapplied on restart |
| Stale `.git` lock after interruption | Git automation module | Detected and cleared, or a specific actionable error |
| Downtime blind spot | Startup reconciliation | Full-scan on startup catches everything changed while the process was down |

### CI / test-tooling recommendations

**Fixtures**: temp-file SQLite per test (or per module) for anything touching migrations/FTS5 triggers (`:memory:` is fine for pure repository-function unit tests); an ephemeral, real Qdrant server per test session (testcontainers-style) for anything touching hybrid fusion/sparse vectors/aliasing, with `:memory:` restricted to narrowly-scoped non-fusion tests via a marker convention; Huey in both "immediate mode" (fast unit tests of job logic) and a real consumer/worker process against a temp `SqliteHuey` file (the only way to genuinely validate the `aget_result()` bridge); a real disposable local Git repo (`git init` in a temp dir) for anything beyond the narrowest argument-construction unit tests, since mocking subprocess would defeat the purpose of the security-sensitive argument-injection tests specifically; MCP contract tests exercised through the actual tool-dispatch layer, not by calling business-logic functions directly.

**CI vs. periodic**: run on every PR — all unit tests; SQLite/Git(real disposable repo)/Huey(immediate mode + a lightweight real-worker smoke test) integration tests; the retrieval-evaluation corpus for any RAG-pipeline-touching change; the cheap structural security checks (no `shell=True`, no pickle serializer, path-traversal fixture sweep, destructive-Git-operation-not-exposed check). Run on a schedule (nightly/weekly) or before releases: the full ephemeral-Qdrant hybrid-fusion suite if container startup cost makes every-PR impractical (measure first); the larger/extended evaluation corpus; dependency/supply-chain audit scans; a Qdrant snapshot-upgrade runbook rehearsal against a scratch instance. Keep manual, deliberately not automated: any rehearsal of an actual production-like Qdrant version upgrade or a real disaster-recovery restore against real data, consistent with the constitution's rule against auto-triggering destructive/high-consequence operations without explicit intent.

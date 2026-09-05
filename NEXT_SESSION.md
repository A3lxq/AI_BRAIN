# ATHENA AI-BRAIN — Next Session

## Start Here

Read, in order:

1. `CLAUDE.md`
2. `docs/DEVELOPMENT_CONSTITUTION.md`
3. `CURRENT_STATE.md`
4. `docs/00_MASTER_PROJECT_SPECIFICATION.md`
5. `docs/ARCHITECTURE.md`
6. `docs/adr/0001-*.md` through `docs/adr/0011-*.md` (all Accepted)
7. `docs/DATA_MODEL.md`, `docs/EVENT_MODEL.md`, `docs/SECURITY_MODEL.md`, `docs/LONGEVITY_NOTES.md`
8. `docs/design/vault-safety-boundary.md`, `docs/design/os-level-process-sandboxing.md`, `docs/design/storage-runtime-hardening.md`, `docs/design/pre-ingestion-secret-scanning.md`
9. `docs/design/migration-runner-and-vault-ingestion.md` (Phase 2)
10. `docs/design/indexing-pipeline.md` (Phase 3)
11. `docs/design/retrieval-pipeline.md` (Phase 4 — read §8's degradation finding before touching `athena.retrieval`)
12. `docs/design/knowledge-intelligence.md` (Phase 5 — implemented this session; read this before touching `athena.intelligence`, especially §2.4/§2.5's accepted lifecycle policies)
13. `docs/sessions/2026-09-03_project-rename.md` (the full technical rename), `docs/sessions/2026-09-04_knowledge-intelligence.md` (this session's own record)

## Objective

**Phase 0 through Phase 4 are fully closed and committed. Phase 5 (Knowledge Intelligence) is now implemented and tested.** The project was fully renamed (package/CLI/env vars/GitHub repo) in a prior session — see `docs/sessions/2026-09-03_project-rename.md`. What exists as real, tested code as of this session:

- `athena.intelligence.duplicates` — four-signal duplicate detection: exact `content_hash`, lexical `MinHash-LSH` (`datasketch` 2.0.0, `scheme="affine32"` pinned), semantic cosine similarity (Qdrant query-by-point-ID), and `difflib` metadata/filename matching (annotation-only, never promotes a pair alone). Fused into `combined_score`, upserted into `duplicate_candidates`.
- `athena.intelligence.merge` — `list_pending_duplicates`/`resolve_duplicate` (explicit confirm/reject) and `merge_notes` (only reachable from a `'confirmed'` candidate — never automatic). Appends absorbed content under a `## Merged from` heading, re-indexes the kept note (best-effort), tombstones/supersedes the absorbed note, writes a `provenance` row.
- `athena.intelligence.related` — on-demand "related notes" via the same query-by-point-ID mechanism, lower threshold, not persisted.
- `athena.intelligence.lifecycle` — `promote_on_first_index` (`'draft' -> 'active'`, accepted policy) and `run_stale_sweep` (`'active'/'verified' -> 'stale'` after 180 days, accepted policy), both via the existing `transition_status()`.
- `athena.retrieval.vector_search.find_similar_by_point_id` — new: query Qdrant by an existing point's ID directly, self-excluded, powering both the semantic-duplicate signal and related-notes.
- `athena.db.repository.duplicates` (new module) and `athena.db.repository.provenance` extended with `insert_derivation`/`get_lineage`.
- CLI: `athena duplicates {scan,list,resolve,merge}`, `athena lifecycle stale-sweep`. Worker gained a daily `stale_sweep_task` periodic job alongside the existing hourly reconciliation.

356/356 tests passing (67 new this session, 5 correctly `skip`-marked pending Docker access), mypy --strict clean, ruff clean. **Nothing from this session has been committed to git yet** — Phases 1-4 and the full rename were already committed and pushed in prior sessions (`a4050d3`/`d97840d`, `aa76ce7`, `561f8d4`, `3cc946e`, `93a195a`); this session's Phase 5 work is still untracked, awaiting explicit user go-ahead.

## Real findings from this implementation session (verify-before-trust discipline)

1. **`datasketch` 2.0.0 changed its default MinHash permutation scheme**, and signatures built under different schemes aren't comparable/mergeable. Every `MinHash(...)` in this codebase pins `scheme="affine32"` explicitly — do not remove this as "redundant with the default" without re-verifying the installed version's actual default first.
2. **Confirmed via the Qdrant API reference, not assumed**: `query_points`'s `query` field accepts an existing point ID directly (the server resolves the stored vector internally) — no need to `retrieve()` then resubmit. Self-exclusion is a `must_not`/`HasIdCondition` filter, not a separate parameter. Verified this works correctly even in embedded (`:memory:`) mode via direct testing before building on it.
3. **A background agent (the duplicate-detection module builder) failed mid-task due to a session rate limit**, after having already written `src/athena/intelligence/duplicates.py` but before writing its tests. The orchestrating session picked up directly: reviewed the agent's code line-by-line, wrote the missing test suite itself, and found one real test-design mistake in the process (expecting `detection_method='content_hash'` for byte-identical notes, when identical content also legitimately triggers the lexical signal — `'combined'` was the correct outcome, not a bug in the implementation).
4. **A real duplicated-code bug caught during self-review, before it reached any test run**: an early draft of `merge_notes` had a stray leftover `return asyncio.run(_run())` line duplicated in `athena.worker.run_stale_sweep` (a copy-paste artifact from adjacent functions) — caught by a `grep` sanity check across all `return asyncio.run(_run())` call sites before running the test suite, not by a test failure.
5. **Live verification confirmed the merge engine's full effect on database state**, not just that it didn't crash: after a real scan → confirm → merge sequence against a scratch vault, direct SQLite inspection showed exactly the expected `notes.status='superseded'`/`deleted_at` on the absorbed note, `duplicate_candidates.status='merged'`, and a new `provenance` row with the correct `supersedes_note_id` — including correct graceful degradation (logged warning, merge still completed) when the post-merge re-index hit an unreachable Qdrant server.

## What is genuinely still missing before Phase 5/6 are "done"

1. **Resolve the Docker-access blocker** — unchanged since Phase 3; 5 real, correct integration tests remain `skip`-marked across Phases 3-4 pending this.
2. **Phase 4's zero-results-on-full-degradation gap** (`docs/design/retrieval-pipeline.md` §8) — still open, a design decision deferred to a future phase/addendum ADR, unaffected by Phase 5's work.
3. **`fastembed` revision-pinning**, **`watchdog` supply-chain review** — both still open, unchanged since earlier phases.
4. **The retrieval-evaluation corpus** still ships at 10 notes/17 questions, not `TESTING_STRATEGY.md`'s 30-60 target.
5. **No regression-gating threshold on `athena retrieval evaluate`** — unchanged.
6. **Status promotion beyond `'draft' -> 'active'`** — Phase 5 resolved only that one transition; `'active' -> 'verified'` and `-> 'archived'` remain fully manual, no policy proposed for either.
7. **The duplicate-detection default thresholds (`0.5` scan, `0.85` semantic, `0.5` related-notes) are untuned against real vault data** — flagged in the design doc §8, revisit once this runs against the real vault.
8. **Secret re-scanning of merged content** — `merge_notes` doesn't re-run `athena.security.secrets` on the combined text (design doc §6, a deliberate scope decision, not an oversight).
9. **`athena.mcp_server`** — still a placeholder; Phase 6's job, and Phase 6 is next. `note_duplicates`/`note_merge` as actual MCP tools will wrap Phase 5's engine then, per `TESTING_STRATEGY.md`'s already-written expectations for those tool names.
10. Real install/venv path decision for the deployment configs (still placeholder paths).
11. Adding `secret_findings_list`/`secret_finding_resolve` to ADR-0007's MCP tool contract table (still open from ADR-0011).

## Do not

- silently alter accepted architecture — Phase 4's zero-results degradation gap and Phase 5's untuned thresholds are documented, not patched, for exactly this reason,
- downgrade `sentence-transformers` below 6.0.0 without re-verifying the CVE-2026-68770 fix is still present,
- remove the per-`Prefetch` filter in `athena.retrieval.vector_search.search()` as "redundant" without first verifying the embedded-mode filter bug doesn't also exist on a real Qdrant server,
- remove the explicit `scheme="affine32"` from any `MinHash(...)` construction in `athena.intelligence.duplicates` without re-verifying `datasketch`'s current default and the comparability implications for already-persisted `note_minhash_signatures` rows,
- assume `merge_notes` can be called directly on a `'pending'` duplicate candidate — it's a hard rejection, by design (Master Spec §10's "must not automatically imply interchangeable"); the two-step confirm-then-merge gate is load-bearing, not a formality,
- assume the 5 skipped Qdrant integration tests pass just because the rest of the suite does — they haven't been run at all in this environment,
- assume "keyword-only degradation" actually returns useful results in a fully-Qdrant-down environment — verified live that it currently does not, until Phase 4 §8's gap is addressed,
- assume the package is still importable as `ai_brain` or the CLI is still `ai-brain` — both were fully renamed to `athena`; grep for `ai_brain`/`ai-brain` before trusting any pre-2026-09-03 note, snippet, or memory that uses the old names literally,
- run `git remote set-url`/`git config` in this environment on the user's behalf — the local `origin` remote may still say `git@github.com:A3lxq/AI_BRAIN.git` unless the user has already fixed it themselves; check `git remote -v` before assuming either way,
- commit the current Phase 5 work without checking with the user first (nothing has been committed yet by design).

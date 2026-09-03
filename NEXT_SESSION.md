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
11. `docs/design/retrieval-pipeline.md` (Phase 4 — implemented this session; read this before touching `ai_brain.retrieval`, especially §8's degradation finding)
12. `docs/sessions/2026-09-02_indexing-pipeline.md`, `docs/sessions/2026-09-03_retrieval-pipeline.md` (this session's own record)

## Objective

**Phase 0 through Phase 3 are fully closed and committed. Phase 4 (Retrieval) is now implemented and tested.** What exists as real, tested code as of this session:

- `ai_brain.retrieval.keyword_search` — SQLite FTS5 keyword search over chunks and note titles/tags, with `sanitize_fts5_query` closing `SECURITY_MODEL.md` P1 item 10 (TB-7) for the first time.
- `ai_brain.retrieval.vector_search` — Qdrant hybrid dense+sparse query construction (RRF fusion query), filter applied defensively on every `Prefetch` (not just the outer query) per a real bug found in embedded-mode testing.
- `ai_brain.retrieval.fusion` — hand-written three-way Reciprocal Rank Fusion (vector + chunk-keyword + note-title, `k=60`).
- `ai_brain.retrieval.reranking` — `BAAI/bge-reranker-v2-m3` cross-encoder reranking, revision-pinned.
- `ai_brain.retrieval.context` — token-budgeted greedy context assembly with citations.
- `ai_brain.retrieval.evaluation` — Recall@K/Precision@K/MRR/nDCG@10/latency-percentile harness plus a 10-note/17-question starter eval corpus.
- `ai_brain.retrieval.search` — the orchestrator, degrading to keyword-only fusion (not crashing) when Qdrant is unreachable at query time.
- CLI: `ai-brain retrieval evaluate [--corpus PATH]`.

296/296 tests passing (5 correctly `skip`-marked pending Docker access), mypy --strict clean, ruff clean. **Nothing from this session has been committed to git yet** — Phases 1, 2, and 3 were already committed and pushed in prior sessions (`a4050d3`/`d97840d`, `aa76ce7`, `561f8d4`); this session's Phase 4 work is still untracked, awaiting explicit user go-ahead.

## Real findings from this implementation session (verify-before-trust discipline)

1. **A real Qdrant embedded-mode filter bug was found empirically**: a `models.Filter` set only on the outer `query_filter` of a hybrid `query_points` call was silently ignored; the same filter set on every `Prefetch` entry worked correctly. Mitigated defensively (filter on every prefetch) regardless of whether a real server has the same bug — unconfirmed there, Docker-blocked. Do not remove the per-`Prefetch` filter as "redundant" without re-verifying against a live server first.
2. **A documentation-derived assumption about SQLite FTS5 was tested and found wrong before it reached the implementation**: two quoted strings joined by whitespace do NOT concatenate into an adjacency phrase in FTS5's `MATCH` grammar — they behave as implicit AND. The actual sanitizer (`sanitize_fts5_query`) quotes each word individually, confirmed by direct empirical testing against a real FTS5 table, not assumed from docs.
3. **A real `sentence-transformers`/`CrossEncoder` API-drift finding**: the activation-function constructor parameter is `activation_fn` in the current installed API, not `default_activation_function` as older documentation describes. Caught before it silently broke reranker construction.
4. **Reciprocal Rank Fusion is hand-written, not via `ranx`** — per ADR-0003's own "hand-rollable" allowance; a few lines of pure math (`score(d) = Σ 1/(k+rank_i(d))`, `k=60`) didn't justify a new dependency.
5. **A significant architectural finding, confirmed live, not just analytical**: because `ai_brain.indexing.index_note()` only ever writes `chunks` rows after a successful Qdrant upsert, and `ai_brain.retrieval.fusion.fuse()` correctly drops any note-title FTS hit whose note has zero chunks (each individually correct, each already tested), their composition means a fully-Qdrant-down environment's "keyword-only degradation" path returns **zero** results end-to-end, not degraded-quality ones. Verified live against the real 17-question eval corpus: every metric came back `0.000`. See `docs/design/retrieval-pipeline.md` §8 for the full writeup — this is flagged as an open item for a future phase/addendum ADR, not silently patched into the accepted Phase 4 design.
6. **This development environment still cannot reach Docker** — unchanged since Phase 3. Every test needing a live Qdrant server is written as real, correct code but marked `pytest.mark.skip` with a clear reason, not silently omitted (1 new skip this session, on top of Phase 3's 4).

## What is genuinely still missing before Phase 3/4 are "done"

1. **Resolve the Docker-access blocker** — add the current user to the `docker` group and restart the session (or have the user run the 5 skipped integration tests themselves) before Phase 3 and Phase 4 can be called fully verified end-to-end the way Phases 1-2 were. This would also let the "keyword-only degradation returns zero results" finding be re-checked against a real, successfully-indexed vault, not only the Qdrant-down scenario.
2. **The zero-results-on-full-degradation gap (§8 above)** — a real design decision (e.g. anchoring note-title hits to a synthetic non-chunk result, or a repository-level content preview instead of requiring a `chunks` row) that this session deliberately did not make retroactively into an already-accepted design.
3. **`fastembed` revision-pinning** — either wait for upstream to add a mechanism, or build a pre-downloaded-snapshot wrapper. Not urgent but a real open security-model item.
4. **`watchdog` supply-chain review** — still flagged as required since Phase 2, still not done.
5. **The retrieval-evaluation corpus ships at 10 notes/17 questions**, not `TESTING_STRATEGY.md`'s 30-60 target — explicitly flagged, a reasonable fixture-authoring follow-up against an already-built harness.
6. **No regression-gating threshold on `ai-brain retrieval evaluate`** — always exits 0; a future CI step would need to add its own threshold check.
7. **Status promotion from `draft`** — still no mechanism decided (carried from Phase 2).
8. **`ai_brain.mcp_server`** — still a placeholder (Phase 6).
9. Real install/venv path decision for the deployment configs (still placeholder paths).
10. Adding `secret_findings_list`/`secret_finding_resolve` to ADR-0007's MCP tool contract table (still open from ADR-0011).

## Do not

- silently alter accepted architecture — the zero-results degradation gap found this session is documented, not patched, for exactly this reason,
- downgrade `sentence-transformers` below 6.0.0 without re-verifying the CVE-2026-68770 fix is still present,
- remove the per-`Prefetch` filter in `ai_brain.retrieval.vector_search` as "redundant" without first verifying the embedded-mode filter bug doesn't also exist on a real Qdrant server,
- rely on the filesystem watcher or `fastembed`'s sparse embeddings in a real deployment before `watchdog` and the miniCOIL pinning gap get the scrutiny/resolution they still need,
- assume the 5 skipped Qdrant integration tests pass just because the rest of the suite does — they haven't been run at all in this environment,
- assume "keyword-only degradation" actually returns useful results in a fully-Qdrant-down environment — verified live that it currently does not, until §8's gap is addressed,
- commit the current Phase 4 work without checking with the user first (nothing has been committed yet by design).

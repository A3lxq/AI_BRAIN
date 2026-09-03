# Session 019 — Retrieval Pipeline (Phase 4)

**Date:** 2026-09-03
**Phase:** 4 (Retrieval), building on Phases 1-3
**Status:** Complete — no git commit made yet

## Objective

Following "start phase 4 retrieval," researched the two technology areas
Phase 4 depends on most heavily (Qdrant's hybrid query API, SQLite FTS5
query safety), drafted and got acceptance ("yes i accept") for
`docs/design/retrieval-pipeline.md`, then implemented it using the same
"shared foundation first, then parallel agents for independent modules,
then direct integration" pattern that worked for Phases 1-3.

## Research performed before designing

Two fronts, per the constitution's "research before implementation" rule:

- **Qdrant hybrid dense+sparse query API** — `client.query_points(...,
  prefetch=[Prefetch(...), Prefetch(...)], query=FusionQuery(fusion=
  Fusion.RRF))`. **A real bug found empirically, not from documentation**:
  in embedded (`:memory:`) mode, a filter set only on the outer
  `query_filter` was silently ignored; the identical filter set on each
  `Prefetch.filter` worked correctly. Unconfirmed against a real server
  (Docker-blocked, same as Phase 3) — mitigated defensively regardless,
  since filtering on every prefetch is cheap and removes dependence on
  unverified version-sensitive behavior either way.
- **SQLite FTS5 query-syntax injection** (`SECURITY_MODEL.md` TB-7, P1 item
  10) — resolved via a per-word quoting sanitizer. **A documentation-derived
  assumption was tested and found wrong**: two quoted strings joined by
  whitespace do not concatenate into an FTS5 adjacency phrase; they behave
  as implicit AND. Confirmed the correct behavior by direct empirical
  testing against a real FTS5 table before writing the sanitizer, rather
  than trusting the docs' phrasing.
- The reranker's revision hash (`BAAI/bge-reranker-v2-m3`,
  `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`) was resolved directly via
  `HfApi().model_info(...).sha` and cross-checked against the raw
  HuggingFace HTTP API, mirroring Phase 3's embedding-model pinning
  discipline.

## What was built

### Shared foundation (done directly, before parallel agents)

- `athena.db.repository.chunks`: added `ChunkRow`, `get_by_ids`,
  `get_first_chunk_id_for_note` — needed by fusion/reranking/context but out
  of scope for any one parallel module.
- `athena/retrieval/` and `tests/retrieval/` package skeletons.

### Parallel agents (narrow, non-overlapping scope, each required to
install/test/mypy/ruff itself before reporting)

1. **Keyword search** (`athena/retrieval/keyword_search.py`) —
   `sanitize_fts5_query`, `search_chunks`, `search_notes`. Tags are filtered
   in Python rather than via SQL `LIKE`, a documented over-fetch-then-filter
   trade-off favoring correctness/simplicity over query-time efficiency.
2. **Vector search** (`athena/retrieval/vector_search.py`) — hybrid
   dense+sparse query construction. The filter-bug mitigation from research
   was explicitly verified via mock request inspection (filter present on
   every `Prefetch`), not just asserted in a comment.
3. **Fusion, reranking, context** (`athena/retrieval/{fusion,reranking,
   context}.py`) — hand-written RRF (`k=60`, ADR-0003's "hand-rollable"
   allowance, not `ranx`); `BAAI/bge-reranker-v2-m3` cross-encoder
   reranking; token-budgeted greedy context assembly with citations. **A
   real API-drift finding**: `CrossEncoder`'s activation-function
   constructor parameter is `activation_fn` in the current installed API,
   not `default_activation_function` as older documentation describes —
   caught before it silently broke reranker construction.

### Integration work (done directly, since these are highly interdependent)

- `athena/retrieval/search.py` — the orchestrator (`search()` and
  `search_ranked_note_paths()`), sharing a `_reranked_results()` helper. A
  mid-edit scaffolding mistake (a broken placeholder function with a
  nonsensical type and `raise NotImplementedError`) was caught and replaced
  before it reached tests. `_vector_search_or_degrade()` catches any
  exception from `vector_search.search` and falls back to keyword-only
  (two-way) RRF fusion, logging a warning rather than propagating.
- `athena/retrieval/evaluation.py` — Recall@K/Precision@K (K=3,5,10), MRR,
  nDCG@10 (graded relevance), latency p50/p95, plus a distinct
  `unanswerable_top1_false_positive_rate` metric tracked separately from the
  other averages (which exclude deliberately-unanswerable questions
  entirely, per the design's explicit split between "the harness computes
  correct numbers" and "the pipeline retrieves well"). Ships with a 10-note/
  17-question starter corpus (`tests/retrieval/fixtures/eval_corpus/`) —
  below `TESTING_STRATEGY.md`'s 30-60 note target, explicitly flagged, not
  silently under-delivered.
- `athena/worker.py` / `athena/cli.py` — `run_retrieval_evaluate()` and
  `athena retrieval evaluate [--corpus PATH]`, printing the full report
  and always exiting 0 (no regression-gating threshold in this pass,
  explicitly flagged as a scope reduction, not an oversight).

## Quality gates

- `pytest`: 296/296 passing (55 new this session), 5 correctly
  `skip`-marked (not silently omitted) pending Docker/Qdrant access
- `mypy --strict` across all of `src/`: clean
- `ruff check`: clean across the whole repo
- Live end-to-end verification against the real 10-note/17-question eval
  corpus, Qdrant unreachable throughout: `athena migrate` (applied
  cleanly) → `athena ingest bootstrap` (`outcome_counts={'created': 10}`,
  degraded gracefully) → `athena index bootstrap` (failed cleanly as
  expected — indexing requires Qdrant, no metadata-only fallback exists for
  it) → `athena retrieval evaluate`.

## A real architectural finding, confirmed live (not left as an assumption)

Before running the live evaluation, it was reasoned analytically that
`chunks`/`chunks_fts` would stay empty in a fully-Qdrant-down environment,
since `index_note()` only ever writes a `chunks` row after a successful
Qdrant upsert. The live run confirmed this, and something stronger: **every
metric in the report came back exactly `0.000`** —
`recall@3/5/10`, `precision@3/5/10`, `mrr`, `ndcg@10`, and
`unanswerable_top1_false_positive_rate` (meaning not even a false-positive
hit came back for the 3 deliberately-unanswerable questions — nothing came
back for *any* question).

Reading `athena/retrieval/fusion.py` confirmed the exact mechanism: it
already has documented, tested logic (§5 of the design doc, row "a note
matched via `notes_fts` has zero chunks") to drop any note-title hit whose
note has no chunk to anchor a `chunk_id` to. That logic is correct in
isolation. But combined with the fact that *no* note has any chunks at all
when Qdrant has never been reachable, the composition of these two
individually-correct, already-tested mechanisms produces a 100% miss rate,
not the "degraded-but-functional" search the design doc's failure-modes
table (§5, "Qdrant unreachable at query time") otherwise describes.

This has been added to `docs/design/retrieval-pipeline.md` §8 as a
confirmed open item, not silently patched into the accepted design — fixing
it (e.g. anchoring note-title hits to something other than a `chunks` row)
is itself a design decision this session deliberately left for a future
phase or a small addendum ADR.

## What remains (see `NEXT_SESSION.md` for full detail)

- Resolve the Docker-access blocker so the 5 skipped integration tests
  (Phase 3's 4 plus Phase 4's 1) can actually run against a live Qdrant
  server, and so the zero-results degradation finding can be re-checked
  against a real, successfully-indexed vault.
- The zero-results-on-full-degradation gap found and documented this
  session — a real design decision, not fixed in this pass.
- `fastembed`/miniCOIL revision pinning — no mechanism exists upstream yet.
- `watchdog` supply-chain review — still outstanding since Phase 2.
- The retrieval-evaluation corpus ships at 10 notes/17 questions, not
  `TESTING_STRATEGY.md`'s 30-60 target.
- Phase 5 (Knowledge Intelligence): duplicate detection, merge engine,
  provenance, lineage — all out of Phase 4's scope.
- This session's work has not been committed to git — awaiting explicit
  user go-ahead, per standing practice established in prior sessions.

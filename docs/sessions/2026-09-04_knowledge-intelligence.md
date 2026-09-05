# Session 022 — Knowledge Intelligence (Phase 5)

**Date:** 2026-09-04
**Phase:** 5 (Knowledge Intelligence), building on Phases 1-4 and the full project rename
**Status:** Complete — no git commit made yet

## Objective

Following "start phase 5 knowledge intelligence" (given after the full
technical rename), researched the two real technology questions Phase 5
depends on (MinHash-LSH library choice, Qdrant's query-by-point-ID
mechanism), drafted and got acceptance ("yes i accept, go ahead with
implementation") for `docs/design/knowledge-intelligence.md`, then
implemented it using the same "shared foundation first, then parallel
agents for independent modules, then direct integration" pattern that
worked for Phases 1-4 — with one real wrinkle this time: one of the three
parallel agents failed mid-task on a session rate limit.

## Research performed before designing

- **`datasketch` (MinHash/MinHashLSH)** — current version 2.0.0 (released
  2026-07-05), no known CVEs. **A real, version-specific gotcha found**:
  2.0.0 changed `MinHash`'s default permutation scheme to `"affine32"`, and
  signatures built under different schemes are not comparable/mergeable.
  Resolved by pinning `scheme="affine32"` explicitly in every `MinHash(...)`
  construction, rather than trusting the installed version's default —
  protects `note_minhash_signatures`' persisted `BLOB`s against a future
  `datasketch` upgrade silently changing that default again. Verified
  `LeanMinHash.serialize()`/`.deserialize()` round-trips correctly by direct
  testing before building on it.
- **Qdrant "query by existing point ID"** — confirmed via the official API
  reference (`api.qdrant.tech`) that `query_points`'s `query` field accepts
  a point ID directly (the server resolves the stored vector internally),
  with self-exclusion via a `must_not`/`HasIdCondition` filter, not a
  separate parameter. Verified this works correctly even against embedded
  (`:memory:`) Qdrant via direct testing, including confirming self-exclusion
  and correct similarity ranking, before building `find_similar_by_point_id`
  on top of it.
- Metadata-match similarity was decided to need no new dependency — stdlib
  `difflib.SequenceMatcher` is adequate for one signal among four, and
  `rapidfuzz` wasn't justified against "don't add dependencies you don't
  need."

## What was built

### Shared foundation (done directly, before parallel agents)

- `athena.db.repository.provenance`: extended `insert_activity` with a
  `supersedes_note_id` parameter, added `insert_derivation` (PROV
  `wasDerivedFrom` for multi-source merges) and `get_lineage` (walks
  `provenance`/`provenance_derivations` both directions from the same
  table — no `superseded_by_note_id` write-side support needed; the query
  answers "what superseded this note" by looking the other way through the
  same rows).
- `athena.db.repository.notes`: added `list_active` (full rows, for
  duplicate scanning) and `list_stale_candidates` (status/cutoff-filtered,
  for the stale sweep).
- `athena.db.repository.duplicates` (new module): `duplicate_candidates`/
  `note_minhash_signatures` CRUD, including an `ON CONFLICT ... WHERE
  status = 'pending'` upsert that refreshes a candidate's scores on rescan
  without ever clobbering an already-reviewed (`confirmed`/`rejected`/
  `merged`) candidate's audit trail.
- `athena.retrieval.vector_search`: added `find_similar_by_point_id` and a
  `score` field on `VectorHit` (previously unused by `search()`'s RRF-fused
  results, needed here for the raw single-vector-space cosine score).
- `athena/intelligence/` and `tests/intelligence/` package skeletons.

### Parallel agents (narrow, non-overlapping scope, each required to
install/test/mypy/ruff itself before reporting)

1. **Related notes** (`athena/intelligence/related.py`) — completed
   cleanly. On-demand semantic similarity reusing
   `find_similar_by_point_id`, deliberately not persisted.
2. **Lifecycle/stale-sweep** (`athena/intelligence/lifecycle.py`) —
   completed cleanly. `promote_on_first_index` and `run_stale_sweep`, both
   exclusively through the existing `transition_status()`.
3. **Duplicate detection** (`athena/intelligence/duplicates.py`) — **failed
   partway through on a session rate limit** (HTTP 429), after writing the
   full module but before writing its test suite or running any
   verification. Picked up directly: read the agent's code line-by-line
   (not assumed correct just because it looked complete), confirmed the
   four-signal design was implemented faithfully to the spec, then wrote
   the entire test suite from scratch and ran it. **One real test-design
   mistake caught in the process, not a bug in the implementation**: a test
   asserted `detection_method == 'content_hash'` for two byte-identical
   notes, but identical content also produces identical shingles, so the
   lexical signal legitimately co-fires too — `'combined'` was the
   correct, more accurate label the code was already producing; the test's
   expectation was wrong, fixed to accept either.

### Integration work (done directly, since these are highly interdependent)

- `athena/intelligence/merge.py` — `list_pending_duplicates`,
  `resolve_duplicate`, `merge_notes`. Ordering mirrors
  `athena.indexing.index_note`'s established crash-safety pattern: the
  vault file write (the one genuinely fallible step) happens before any
  database write; re-indexing the merged content is treated as best-effort
  (caught, logged, merge still completes) rather than as something a
  failure there should undo, since `index_note()`'s own `index_state`
  bookkeeping already makes the note naturally re-indexable later.
- **A real bug caught before it reached any test run**: a copy-paste
  artifact left a stray duplicated `return asyncio.run(_run())` line at the
  end of `athena.worker.run_stale_sweep` (harmless — unreachable dead code,
  since the first `return` already exits — but a clear sign of a sloppy
  edit). Caught by a `grep -n "return asyncio.run(_run())"` sanity sweep
  across the whole file before running the test suite, not by a test
  failure.
- `athena.indexing.index_note`: wired `promote_on_first_index` in after
  `mark_indexed`, conditional on `chunks` being non-empty (an empty note
  that indexes to zero chunks has nothing to promote yet — a detail the
  design doc's summary didn't spell out but the accepted policy's own
  wording, "at least one real, embedded, searchable chunk," already
  implied).
- `athena.worker` / `athena.cli`: `run_duplicates_scan/list/resolve/merge`,
  `run_stale_sweep`, and a new daily `stale_sweep_task` periodic job
  (`crontab(minute="0", hour="3")`, alongside the existing hourly
  reconciliation); CLI subcommands `athena duplicates {scan,list,resolve,
  merge}` and `athena lifecycle stale-sweep`.

## Quality gates

- `pytest`: 356/356 passing (67 new this session), 5 correctly
  `skip`-marked (unchanged from Phase 4, not silently omitted) pending
  Docker/Qdrant access
- `mypy --strict` across all of `src/`: clean (one new override added:
  `datasketch.*` ships no `py.typed` marker, same treatment as `huey.*`)
- `ruff check`: clean across the whole repo
- Live end-to-end verification against a real 3-note scratch vault (two
  near-duplicates differing by one word, one unrelated note), Qdrant
  unreachable throughout:
  - `athena migrate` → `athena ingest bootstrap` (3 notes ingested,
    degraded gracefully)
  - `athena duplicates scan --threshold 0.3` — correctly flagged only the
    near-duplicate pair (`method=minhash_lsh combined_score=0.867`),
    correctly logged and skipped the semantic signal for all three notes
    (no chunks exist, Qdrant unreachable), correctly did not flag the
    unrelated note against either
  - `athena duplicates resolve 1 --confirm` → `athena duplicates merge 1
    --keep 2` — merge completed (exit 0) despite the post-merge re-index
    attempt failing with a real `ConnectionRefusedError` against Qdrant,
    logged as a warning exactly as designed
  - Direct SQLite inspection confirmed exact expected state: absorbed
    note's `status='superseded'`, `deleted_at` set; kept note's file on
    disk contains both texts under a `## Merged from` heading; absorbed
    note's original file untouched on disk (soft-delete is DB-only,
    matching the established convention); `duplicate_candidates.status
    ='merged'`; a new `provenance` row with `activity_type='merge'`,
    `supersedes_note_id` correctly pointing at the absorbed note
  - `athena lifecycle stale-sweep --stale-after-days 0` ran cleanly,
    correctly flagged zero notes (all three notes are still `'draft'`,
    since indexing never succeeded without Qdrant, so `promote_on_first_
    index` never ran — the stale-sweep policy only applies to
    `'active'`/`'verified'` notes, exactly as designed)

## What remains (see `NEXT_SESSION.md` for full detail)

- Resolve the Docker-access blocker — unchanged since Phase 3, now 5
  skipped integration tests across Phases 3-4.
- Phase 4's zero-results-on-full-degradation gap — still open, unaffected
  by this session's work.
- The duplicate-detection default thresholds are untuned against real
  vault data — flagged in the design doc §8.
- Secret re-scanning of merged content — a deliberate scope decision
  (design doc §6), not built in this pass.
- Status promotion beyond `'draft' -> 'active'` — `'active' -> 'verified'`
  and `-> 'archived'` remain fully manual; no policy proposed for either,
  consistent with not inventing unreviewed lifecycle policy.
- Phase 6 (MCP): the unified MCP server, tool contracts — including
  `note_duplicates`/`note_merge` finally wrapping this session's engine,
  per `TESTING_STRATEGY.md`'s already-written expectations for those tool
  names.
- This session's work has not been committed to git — awaiting explicit
  user go-ahead, per standing practice established in every prior phase.

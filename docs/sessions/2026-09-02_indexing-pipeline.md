# Session 017 — Indexing Pipeline (Phase 3)

**Date:** 2026-09-02
**Phase:** 3 (Indexing), building on Phases 1-2
**Status:** Complete — no git commit made yet

## Objective

Following "start phase 3 indexing," researched the three technology areas
Phase 3 depends on (chunking, embedding, Qdrant), drafted and got acceptance
for `docs/design/indexing-pipeline.md`, then implemented it using the same
"shared foundation first, then parallel agents for independent modules, then
direct integration" pattern that worked for Phases 1-2.

## Research performed before designing

Three parallel research fronts, per the constitution's "research before
implementation" rule, since Phase 0's research for these libraries was from
2026-08-24 and a library's security posture can move in under two weeks:

- **`chonkie`**: clean bill of health — zero CVEs, org-backed, active
  maintenance, no risky install-time code execution, light dependency
  footprint. Satisfies `SECURITY_MODEL.md` P1 item 12 (the same scrutiny
  GitPython/Dulwich/LiteLLM already received) for the first time. Two
  corrections found: the canonical repo is now `feyninc/chonkie` (a benign
  GitHub org rename PyPI's own metadata hasn't caught up to), and
  `RecursiveChunker.from_recipe("markdown")` makes a live HuggingFace Hub
  network call — avoided in favor of a hand-built `RecursiveRules`.
- **Embeddings/reranker/sparse stack**: model IDs/APIs from ADR-0008 still
  current. New requirement found: Qdrant's sparse-vector config for miniCOIL
  needs `modifier=Modifier.IDF` explicitly, or it silently produces
  meaningless vectors, not an error.
- **A critical CVE, found and then fully resolved by direct verification,
  not assumed.** `sentence-transformers` had CVE-2026-68770 (CVSS 9.8): a
  trust-gate bypass (`trust_remote_code or os.path.exists(model_name_or_path)`)
  allowing RCE via a planted `modeling_*.py` file. The CVE tracker claimed no
  fixed version existed. This was checked directly against GitHub source at
  the exact version the exploit PoC targeted (v5.5.1 — confirmed the
  vulnerable line is present) and the current release (v6.0.1 — confirmed
  the bypass was removed entirely in v6.0.0, three weeks after disclosure,
  per that release's own changelog explicitly closing the same GitHub issue
  the CVE describes). Resolution: pin `sentence-transformers>=6.0.0`.
- **Environment finding, not a library issue**: this development
  environment's user account is not in the `docker` group and interactive
  `sudo` is unavailable in this session — a live Qdrant server cannot be
  started here. Flagged as a blocker for live integration testing, not a
  design gap.

## What was built

### Shared foundation (done directly, sequentially, before parallel agents)

- `pyproject.toml`: added `chonkie>=1.7.0`, `sentence-transformers>=6.0.0`
  (the lower bound is a security requirement, not a preference), `fastembed`,
  `qdrant-client>=1.16.0` (closes a separate, already-patched Qdrant server
  CVE). All four ship `py.typed` — no mypy overrides needed.
- Migration 0004 (`notes.index_state`/`last_index_error`) — resolves the
  item Phase 2's own design doc deliberately deferred.
- `athena.config`: `ATHENA_QDRANT_URL`.
- `athena.db.repository.notes`: `get_by_id`, `mark_indexed`,
  `mark_index_failed`, `list_ids_needing_index`.
- `athena.db.repository.chunks` (new module) — Phase 2's repository task
  explicitly excluded this table since it belongs to Phase 3.

### Parallel agents (narrow, non-overlapping scope, each required to
install/test/mypy/ruff itself before reporting)

1. **Chunking** (`athena/indexing/chunking.py`) — verified `chonkie`
   1.7.0's real API directly (no `chunk_size`/overlap parameter on
   `RecursiveChunker` — overlap is a separate `OverlapRefinery` pass;
   `RecursiveLevel.delimiters` match as literal substrings, not regex).
   **The design's flagged empirical question was resolved positively**: a
   hand-built ATX-heading-aware `RecursiveRules` correctly splits on
   ATHENA AI-BRAIN's real `# you asked`/`### USER`-style turn headers — no custom
   pre-splitter needed. Honestly documented one real limitation found:
   code fences longer than one chunk's worth of text are not protected from
   mid-fence splitting (chonkie has no fence-boundary awareness at all).
2. **Embedding** (`athena/indexing/embedding.py`) — resolved and pinned a
   real BGE-M3 revision hash via the HuggingFace Hub API, cross-checked two
   ways. **Real gap found and honestly documented, not faked**: `fastembed`
   has no revision-pinning mechanism whatsoever for miniCOIL, confirmed by
   reading its installed source directly (`MiniCOIL.__init__` doesn't
   forward `**kwargs` to the download path at all). `SECURITY_MODEL.md` P1
   item 15 stays open for the sparse leg specifically.
3. **Qdrant store** (`athena/indexing/qdrant_store.py`) — atomic,
   lock-guarded alias mutation (resolves P1 item 11), IDF-modifier sparse
   config, payload indexes. Integration tests needing a real server written
   correctly and marked `skip` (not `xfail`, not omitted), citing the
   Docker-access blocker. Surfaced a design gap during implementation (see
   below), which the integration phase resolved.

### Integration work (done directly, since these are highly interdependent)

- **A real bug caught and fixed during integration, not shipped**: the
  qdrant_store agent's original design (per the doc's literal ordering)
  needed the SQLite `chunks.id` primary key in the Qdrant payload, which
  meant inserting `chunks` rows *before* the Qdrant upsert — but that
  directly conflicts with the design's own "zero partial `chunks` rows on
  failure" test requirement (§7), since a `chunks` row would already exist
  if the subsequent embed/upsert step then failed. Resolved by reverting to
  embed → Qdrant upsert → *then* insert `chunks` rows using the real
  point-ids Qdrant returned — `chunk_id` is not in the Qdrant payload
  (documented as a deliberate, non-load-bearing trade-off: nothing filters
  or deletes by it, only by `note_id`), in exchange for the correctness
  guarantee. Caught by writing the failure-path test and having it fail
  first, not by inspection alone.
- `athena/indexing/index_note.py` — the idempotent per-note job. Re-runs
  the identical read → secret-scan → redact → parse pipeline `ingest_note`
  used (including applying the same allowlist), rather than trusting a
  cached copy.
- `athena/worker.py` — a lazy, process-lifetime `QdrantClient` singleton;
  `index_note_task` chained after `ingest_note_task`'s success via a normal
  (queued, not `call_local`) invocation; `run_index_bootstrap` for the new
  CLI command.
- `athena/vault/bootstrap.py` / `reconcile.py` — both gained an optional
  `qdrant_client` parameter; when supplied, chain directly into
  `index_note()` after each successful ingest, catching (not propagating)
  per-note indexing failures so one bad note or an unreachable Qdrant server
  never aborts the rest of a bootstrap/reconciliation run.
- `athena/worker.py`'s `run_bootstrap`/`run_reconcile` degrade gracefully
  (log a warning, proceed metadata-only) if Qdrant is unreachable;
  `run_index_bootstrap` does not, since indexing is its entire purpose —
  verified live, and the CLI wraps it in a clean error message rather than
  letting a raw traceback leak (a real UX bug caught during live
  verification and fixed before this session ended).
- CLI: `athena index bootstrap`. Doctor: `qdrant_reachable` (warn, not
  fail, when unreachable — mirrors `bwrap_available`/`docker_available`'s
  own posture for optional external dependencies).

## Quality gates

- `pytest`: 241/241 passing (30 new this session), 4 correctly
  `skip`-marked (not silently omitted) pending Docker/Qdrant access
- `mypy --strict` across all of `src/`: clean
- `ruff check`: clean across the whole repo
- Live end-to-end verification: `athena migrate` (4 migrations) →
  `athena doctor` (correctly reports `qdrant_reachable: warn`, connection
  refused, everything else `ok`) → `athena ingest bootstrap` against a
  real ChatGPT-style fixture note (gracefully degraded to metadata-only,
  logged a clear warning, confirmed via direct DB inspection:
  `index_state='stale'`, zero orphaned `chunks` rows) → `athena index
  bootstrap` (failed cleanly with a readable error once the CLI's error
  handling was fixed, rather than a raw traceback)

## What remains (see `NEXT_SESSION.md` for full detail)

- Resolve the Docker-access blocker so the 4 skipped integration tests can
  actually run against a live Qdrant server.
- `fastembed`/miniCOIL revision pinning — no mechanism exists upstream yet.
- `watchdog` supply-chain review — still outstanding since Phase 2.
- Phase 4 (Retrieval): hybrid fusion, reranking, context construction, the
  retrieval-evaluation corpus — all deliberately out of Phase 3's scope.
- This session's work has not been committed to git — awaiting explicit
  user go-ahead, per standing practice established in prior sessions.

# AI_BRAIN — Next Session

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
10. `docs/design/indexing-pipeline.md` (Phase 3 — implemented this session; read this before touching `ai_brain.indexing`)
11. `docs/sessions/2026-09-02_indexing-pipeline.md` (this session's own record)

## Objective

**Phase 0, 1, and 2 are fully closed. Phase 3 (Indexing) is now implemented and tested.** What exists as real, tested code as of this session:

- `ai_brain.indexing.chunking` — `chonkie`-based structure-aware Markdown chunking, empirically confirmed to split on AI_BRAIN's real conversational-turn headers.
- `ai_brain.indexing.embedding` — BGE-M3 dense embeddings (revision-pinned) + miniCOIL sparse embeddings (no pinning mechanism exists — documented gap).
- `ai_brain.indexing.qdrant_store` — collection/alias lifecycle, point upsert/delete, payload indexes, atomic lock-guarded alias mutation.
- `ai_brain.indexing.index_note` — the idempotent per-note indexing job, chained after `ingest_note()`.
- `ai_brain.worker`/`ai_brain.vault.bootstrap`/`ai_brain.vault.reconcile` — all wired to chain indexing, with graceful degradation to metadata-only ingestion when Qdrant is unreachable (verified live).
- Migration 0004 — `notes.index_state`/`last_index_error`, resolving Phase 2's deferred item.
- CLI: `ai-brain index bootstrap`. Doctor: `qdrant_reachable` check.

241/241 tests passing (4 correctly `skip`-marked pending Docker access), mypy --strict clean, ruff clean. **Nothing from this session has been committed to git yet** — Phases 1 and 2 were already committed and pushed in prior sessions (`a4050d3`/`d97840d`, `aa76ce7`); this session's Phase 3 work is still untracked, awaiting explicit user go-ahead.

## Real findings from this implementation session (verify-before-trust discipline)

1. **A critical CVE (CVE-2026-68770, CVSS 9.8) in `sentence-transformers` was found during research and resolved by direct source verification**, not assumed from the CVE tracker's stale "no fix" claim. Confirmed by reading the actual GitHub source: the vulnerable version (v5.5.1, matching the exploit PoC's own target) contains the literal bypass `trust_remote_code or os.path.exists(model_name_or_path)`; the fix landed in v6.0.0 (three weeks after disclosure), removing the bypass entirely. `sentence-transformers>=6.0.0` is now pinned. Do not downgrade below this without re-verifying.
2. **`chonkie`'s heading-aware chunking works for AI_BRAIN's real turn-header content** — empirically confirmed, not assumed. A hand-built `RecursiveRules` (never `from_recipe()`, which makes a live HuggingFace Hub call) correctly splits on `# you asked`/`### USER`-style headers.
3. **`fastembed` has no revision-pinning mechanism at all** for miniCOIL, confirmed by reading its installed source directly (`MiniCOIL.__init__` doesn't forward `**kwargs` to the download path). `SECURITY_MODEL.md` P1 item 15 remains genuinely open for the sparse leg — flagged, not silently worked around.
4. **Qdrant's IDF modifier is easy to miss and silently wrong if omitted** — a sparse vector collection without `modifier=Modifier.IDF` produces meaningless miniCOIL vectors, not an error.
5. **Ordering matters for crash-safety**: `index_note()` deliberately embeds and upserts to Qdrant *before* writing any SQLite `chunks` row, so a failure at any point leaves zero partial rows — verified by an actual failure-injection test, not just asserted in the design.
6. **This development environment cannot reach Docker** — the current user isn't in the `docker` group and interactive `sudo` isn't available in this session. Every test needing a live Qdrant server is written as real, correct code but marked `pytest.mark.skip` with a clear reason, not silently omitted.

## What is genuinely still missing before Phase 3/4 are "done"

1. **Resolve the Docker-access blocker** — add the current user to the `docker` group and restart the session (or have the user run the 4 skipped integration tests themselves) before Phase 3 can be called fully verified end-to-end the way Phases 1-2 were.
2. **`fastembed` revision-pinning** — either wait for upstream to add a mechanism, or build a pre-downloaded-snapshot wrapper. Not urgent but a real open security-model item.
3. **`watchdog` supply-chain review** — still flagged as required since Phase 2, still not done.
4. **Phase 4 (Retrieval)**: hybrid fusion (RRF/DBSF), filters, ranking, reranking (`bge-reranker-v2-m3` — deliberately not installed yet, that's Phase 4's job, not Phase 3's), context construction, and the retrieval-evaluation corpus (`TESTING_STRATEGY.md`'s 30-60 hand-labeled notes).
5. **Status promotion from `draft`** — still no mechanism decided (carried from Phase 2).
6. **`ai_brain.mcp_server`** — still a placeholder (Phase 6).
7. Real install/venv path decision for the deployment configs (still placeholder paths).
8. Adding `secret_findings_list`/`secret_finding_resolve` to ADR-0007's MCP tool contract table (still open from ADR-0011).

## Do not

- silently alter accepted architecture,
- downgrade `sentence-transformers` below 6.0.0 without re-verifying the CVE-2026-68770 fix is still present,
- install `bge-reranker-v2-m3` or any reranking dependency as part of "finishing" Phase 3 — that's explicitly Phase 4's scope, not an oversight here,
- rely on the filesystem watcher or `fastembed`'s sparse embeddings in a real deployment before `watchdog` and the miniCOIL pinning gap get the scrutiny/resolution they still need,
- assume the 4 skipped Qdrant integration tests pass just because the rest of the suite does — they haven't been run at all in this environment,
- commit the current Phase 3 work without checking with the user first (nothing has been committed yet by design).

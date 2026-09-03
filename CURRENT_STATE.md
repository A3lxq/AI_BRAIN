# ATHENA AI-BRAIN — Current State

## Project

ATHENA AI-BRAIN

## Development Mode

Ground-up development with Claude Code

## Current Phase

Phase 4 — Retrieval (implemented and tested this session; Phases 1-3 are complete and committed, Phase 3 committed already)

## Current Status

**Full code/package/repository rename completed 2026-09-03** (this session, alongside Phase 4): the project's technical identity now matches its ATHENA AI-BRAIN branding end-to-end, not just prose. Changed: the Python package (`src/ai_brain` → `src/athena`, `import athena.*` everywhere), the installable/CLI name (`pyproject.toml`'s `name = "athena"`, `[project.scripts] athena = "athena.cli:main"`), the CLI command itself (`ai-brain doctor` → `athena doctor`, etc., across every subcommand), all environment variables (`AI_BRAIN_*` → `ATHENA_*`: `ATHENA_VAULT_DIR`, `ATHENA_DATA_DIR`, `ATHENA_HUEY_SECRET`, `ATHENA_QDRANT_URL`, `ATHENA_LOG_LEVEL`, `ATHENA_SECRET_SCANNER_BLOCK_HIGH`), the `AIBrainConfig` dataclass (→ `AthenaConfig`), the Qdrant collection alias (`ai_brain_chunks`/`ai_brain_chunks_bge_m3_v1` → `athena_chunks`/`athena_chunks_bge_m3_v1` — no live collection existed to migrate, Docker still blocked), the deployment configs (`ai-brain-huey-worker.service` → `athena-huey-worker.service`, `ai-brain-mcp-launch.sh` → `athena-mcp-launch.sh`), and the GitHub repository itself (`github.com/A3lxq/AI_BRAIN` → `github.com/A3lxq/ATHENA_AI_BRAIN`, via `gh repo rename`; GitHub's automatic redirect keeps the old clone URL working). All 296 tests pass unchanged, mypy --strict and ruff are clean, and a live CLI smoke test (`athena doctor`/`athena migrate` against fresh `ATHENA_*` env vars) confirmed the renamed entry point and env vars work end-to-end. See `docs/sessions/2026-09-03_project-rename.md` for the full record, including the one thing this session could *not* do itself (updating the local `origin` remote URL — blocked by this environment's own git-config-change restriction; the old remote URL still works via GitHub's redirect, but see "Do not" below for the one-line fix you can run yourself).

**Phase 0 is fully closed** (all eleven ADRs accepted, all exit criteria satisfied). **Phase 1 (Foundation)**, **Phase 2 (Vault Engine)**, and **Phase 3 (Indexing)** are all complete, tested, and committed/pushed to `github.com/A3lxq/ATHENA_AI_BRAIN` `main`:
- Phase 1 (`a4050d3`, merged as `d97840d`): package scaffolding, config/logging/CLI/doctor, all four P0 security modules, OS-sandboxing deployment configs.
- Phase 2 (`aa76ce7`): SQLite migration runner, repository layer, filesystem watcher, the idempotent `ingest_note` job, bootstrap/reconcile, `athena.worker`.
- Phase 3 (`561f8d4`): chunking, embedding, Qdrant store, the `index_note` job, `index_state` schema.

**Phase 3 (Indexing) recap**, per `docs/design/indexing-pipeline.md` (accepted 2026-09-02, implemented 2026-09-02):

- `athena.indexing.chunking` — structure-aware Markdown chunking via `chonkie` 1.7.0, hand-built heading-aware `RecursiveRules` (never `from_recipe()`, which makes a live HuggingFace Hub network call). **Empirically confirmed**: chunk boundaries fall exactly on ATHENA AI-BRAIN's real conversational-turn headers (`# you asked`, `### USER`) — no custom pre-splitter needed.
- `athena.indexing.embedding` — dense embeddings (`BAAI/bge-m3`, 1024-dim, revision-pinned to a resolved commit hash) and sparse embeddings (`Qdrant/minicoil-v1` via `fastembed`, no revision-pinning mechanism exists for this one — a real, documented gap, not silently faked).
- `athena.indexing.qdrant_store` — collection/alias lifecycle (atomic alias updates, lock-guarded), point upsert/delete, payload indexes.
- `athena.indexing.index_note` — the idempotent per-note indexing job, chained after `ingest_note()`'s success. Embedding and the Qdrant upsert both happen *before* any SQLite `chunks` row is written, guaranteeing zero partial rows on failure.
- `athena.worker`/`athena.vault.bootstrap`/`athena.vault.reconcile` all wired to chain indexing after ingestion, with graceful degradation to metadata-only ingestion when Qdrant is unreachable (verified live).
- Migration 0004: `notes.index_state`/`last_index_error` — resolves Phase 2's deliberately deferred item.
- CLI: `athena index bootstrap`; doctor gained a `qdrant_reachable` check (warn, not fail, when unreachable).

**A critical CVE (CVE-2026-68770, CVSS 9.8) in `sentence-transformers` was found during research and resolved by direct source verification** — confirmed fixed in v6.0.0 (three weeks after disclosure) by reading the actual GitHub source at the exact pre-fix and post-fix versions; `sentence-transformers>=6.0.0` is now pinned.

241/241 tests passing (4 correctly `skip`-marked, not silently omitted, pending Qdrant/Docker access), mypy --strict clean, ruff clean. Committed as `561f8d4`.

**Phase 4 (Retrieval) is now implemented and tested**, per `docs/design/retrieval-pipeline.md` (accepted 2026-09-03, implemented 2026-09-03):

- `athena.retrieval.keyword_search` — SQLite FTS5 search over `chunks_fts`/`notes_fts`, with a verified-safe `sanitize_fts5_query` closing `SECURITY_MODEL.md` P1 item 10 (TB-7, FTS5 query-syntax injection) for the first time. **A documentation-derived assumption about FTS5 phrase concatenation was tested and found wrong** before it reached the implementation — corrected via direct empirical testing, not left as an unverified belief.
- `athena.retrieval.vector_search` — Qdrant hybrid dense+sparse (`FusionQuery(fusion=Fusion.RRF)`) query construction. **A real Qdrant embedded-mode bug was found empirically**: a filter set only on the outer `query_filter` was silently ignored; the same filter set on every `Prefetch` worked. Mitigated defensively (filter on every prefetch) regardless of whether a real server has the same bug — unconfirmed there, Docker-blocked (§0/§8 of the design doc).
- `athena.retrieval.fusion` — hand-written Reciprocal Rank Fusion (`k=60`, ADR-0003's own "hand-rollable" call), three-way over vector hits, chunk-keyword hits, and note-title hits (mapped to each note's first chunk as a representative proxy).
- `athena.retrieval.reranking` — cross-encoder reranking via `BAAI/bge-reranker-v2-m3`, revision-pinned (`953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`, resolved via `HfApi` and cross-checked against the raw HF HTTP API). **A real API-drift finding**: `CrossEncoder`'s activation-function parameter is `activation_fn` in the current API, not `default_activation_function` as older docs describe.
- `athena.retrieval.context` — token-budgeted greedy context assembly with per-chunk citations, never truncating a chunk mid-text.
- `athena.retrieval.evaluation` — Recall@K/Precision@K (K=3,5,10), MRR, nDCG@10, latency p50/p95, plus a distinct `unanswerable_top1_false_positive_rate` metric for deliberately-unanswerable questions (excluded from the other averages, tracked separately). A 10-note/17-question starter corpus ships in `tests/retrieval/fixtures/eval_corpus/` (below `TESTING_STRATEGY.md`'s 30-60 target, explicitly flagged, not silently under-delivered).
- `athena.retrieval.search` — the orchestrator (`search()`/`search_ranked_note_paths()`), degrading to keyword-only fusion (not propagating the exception) when Qdrant is unreachable at query time.
- CLI: `athena retrieval evaluate [--corpus PATH]` — prints the report, always exits 0 (no regression-gating threshold in this pass, explicitly flagged, not an oversight).

296/296 tests passing (5 correctly `skip`-marked pending Qdrant/Docker access), mypy --strict clean, ruff clean. **Live end-to-end verification performed** against the real eval corpus with Qdrant unreachable throughout (`athena migrate` → `athena ingest bootstrap`, degrades gracefully → `athena index bootstrap`, fails cleanly as expected since indexing requires Qdrant → `athena retrieval evaluate`). **This confirmed a real, previously-only-analytical architectural finding, now documented in the design doc's §8**: because `index_note()` never writes any `chunks` rows without a successful Qdrant upsert, and `fusion.fuse()` correctly drops any note-title hit whose note has zero chunks, a fully-Qdrant-down environment's "keyword-only degradation" path currently returns **zero** results end-to-end (not just reduced-quality ones) — verified live: all 17 eval questions produced `recall@k`/`precision@k`/`mrr`/`ndcg@10`/`unanswerable_top1_false_positive_rate` of `0.000`. Each contributing mechanism is individually correct and already tested; the gap is in their composition, not a bug in either one — flagged as an open item for a future phase/addendum ADR, not silently patched into this accepted design. **This session's Phase 4 work has not yet been committed** — awaiting explicit user go-ahead, per standing practice.

**Known environment blocker, not a code gap**: this development environment's user account is not in the `docker` group and interactive `sudo` is unavailable, so a live Qdrant server cannot be started here. Every test needing one is written as real, correct code but marked `skip` with a clear reason — see `docs/design/indexing-pipeline.md` §0/§8 and `docs/design/retrieval-pipeline.md` §0/§8.

## Accepted Architecture

- Runtime language: **Python** — ADR-0001 accepted 2026-08-22 (see `docs/adr/0001-runtime-language-selection.md`)
- Job/queue library: **Huey with SQLite backend** (`SqliteHuey`), default serializer swapped off pickle, **own SQLite file separate from ATHENA AI-BRAIN's metadata database** (confirmed by ADR-0004) — ADR-0002 accepted 2026-08-22 (see `docs/adr/0002-job-queue-architecture.md`); validate the async bridge (`aget_result()`) against one real job type early in Phase 1, with the hand-rolled asyncio+SQLite queue as documented fallback if that proves awkward
- Event audit/replay log: **narrow append-only `events` table** in ATHENA AI-BRAIN's metadata SQLite database (not Huey's), recording domain-meaningful transitions across filesystem/Git/job/dedup/reconciliation domains per a shared envelope schema — ADR-0010 accepted 2026-08-27 (see `docs/adr/0010-event-audit-log.md`); retention/pruning policy deferred until table growth is measured
- Secret-scan schema: **`notes.secret_scan_status` column** (orthogonal to `status`/`index_state`) + **`note_secret_findings`** + **`secret_scan_allowlist`** tables in `athena.db`, fingerprint-keyed allowlisting with mandatory `reason` — ADR-0011 accepted 2026-08-27 (see `docs/adr/0011-secret-scan-schema.md`); formalizes the schema `docs/design/pre-ingestion-secret-scanning.md` specifies; two new MCP tools (`secret_findings_list`, `secret_finding_resolve`) still need adding to ADR-0007's tool table
- RAG orchestration: **hand-rolled composable primitives** — `qdrant-client` + `chonkie` (chunking) + SQLite FTS5 (keyword search) + hand-written cross-store fusion + `sentence-transformers` (embeddings/reranking) + a small `Protocol`-based multi-provider LLM adapter — no LangChain/LlamaIndex adoption. ADR-0003 accepted 2026-08-22 (see `docs/adr/0003-rag-orchestration-approach.md`); verify `chonkie`'s frontmatter handling early in Phase 1
- SQLite access layer: **hand-rolled thin repository layer over `aiosqlite`** — typed functions per query, parameterized SQL, a minimal `PRAGMA user_version`-driven migration runner, FTS5 external-content tables with hand-written trigger sync; Peewee documented as fallback. ADR-0004 accepted 2026-08-22 (see `docs/adr/0004-sqlite-access-layer.md`); verify connection-management pattern (single connection vs. small pool) during Phase 1 prototyping
- Git automation: **purpose-built module wrapping the real `git` CLI via `asyncio.create_subprocess_exec`** — argument lists only, `--`/`--end-of-options` insertion, explicit branch-name allow-listing, hand-built failure taxonomy, `gitleaks` via `pre-commit` for secret scanning; no GitPython; Dulwich (`--pure`) optional and read-only-only. ADR-0005 accepted 2026-08-24 (see `docs/adr/0005-git-automation-library.md`); verify Kali's git version supports `--end-of-options` per-subcommand before Phase 1
- Qdrant deployment: **Docker server, bound to `127.0.0.1` only**, `--restart unless-stopped`, pinned image tag, snapshot-before-upgrade runbook required. ADR-0006 accepted 2026-08-24 (see `docs/adr/0006-qdrant-deployment-mode.md`); `QdrantClient(":memory:")` permitted only for non-fusion-critical unit tests
- MCP tool contract: full tool table (search/read/create/update/move/delete/related/duplicate-detection/merge/research/summarize/link/reindex/status/history/provenance/Git-ops/diagnostics) — destructive ops (`note_delete`, `note_merge`) require MRTR elicitation confirmation; genuinely irreversible Git operations excluded from the MCP surface entirely; interim `job_status`/`job_cancel` shim pending official SDK tasks-extension support. ADR-0007 accepted 2026-08-24 (see `docs/adr/0007-mcp-tool-contract.md`); **no protocol-level defense exists against retrieved-content/instruction conflation** — mitigation is application-level defense-in-depth, must be named explicitly in the security threat model
- Embeddings/sparse/reranker: **BGE-M3** (dense) + **bge-reranker-v2-m3** (reranker) + Qdrant **miniCOIL** via `fastembed` (sparse); Qwen3-Embedding-0.6B + Qwen3-Reranker-0.6B documented as a close fallback; BM25 fallback for the sparse leg if vault language composition is meaningfully non-English. ADR-0008 accepted 2026-08-24 (see `docs/adr/0008-embeddings-model-choice.md`); provisional-but-documented, re-evaluate in 6–12 months via a new ADR; collection access must use an alias, never a hardcoded name
- Filesystem event architecture: light, non-semantic debouncing (quiet-window per-path) + idempotent Huey jobs as the real safety net + a periodic/startup reconciliation (full-scan) job as backstop; `.git`/`.obsidian`/plugin-cache excluded from watch scope; `fs.inotify.max_user_watches` sysctl raise documented as a deployment prerequisite. ADR-0009 accepted 2026-08-24 (see `docs/adr/0009-filesystem-event-architecture.md`)
- Obsidian vault as source of truth
- ATHENA AI-BRAIN separate from the vault
- One unified MCP server
- Event-driven architecture
- Hybrid retrieval
- Semantic/structure-aware chunking
- Embeddings + vector retrieval
- Keyword/metadata retrieval
- Reranking
- SQLite for application metadata/state
- Qdrant or another evaluated vector store, subject to Phase 0 research
- Git/GitHub for version control and backup
- Provenance and knowledge lineage
- Duplicate detection
- Knowledge lifecycle/status
- Multi-LLM/provider abstraction
- Local-first options where practical

## Completed

- Project vision established
- Core architecture direction established
- Development constitution established
- Claude Code operating rules established
- Continuity strategy established
- Ground-up development pack created
- Runtime/language research (Python, TypeScript/Node.js, Go, Rust) completed against all 15 evaluation criteria, using current primary documentation (research date 2026-08-22)
- Comparison matrix and recommendation written
- ADR-0001 (runtime selection: Python) accepted 2026-08-22
- Job/queue architecture research (Celery, Dramatiq, Taskiq, RQ, arq, Huey, hand-rolled asyncio+SQLite) completed, using current primary documentation (research date 2026-08-22)
- ADR-0002 (job/queue: Huey with SQLite backend) accepted 2026-08-22
- RAG orchestration research (LangChain+LangGraph, LlamaIndex, hand-rolled, middle-ground) completed, using current primary documentation (research date 2026-08-22)
- ADR-0003 (RAG orchestration: hand-rolled composable primitives) accepted 2026-08-22
- SQLite access layer research (raw sqlite3/aiosqlite, SQLAlchemy 2.x async, SQLModel, Peewee, hand-rolled) completed, using current primary documentation (research date 2026-08-22)
- ADR-0004 (SQLite access layer: hand-rolled repository layer; Huey uses a separate DB file) accepted 2026-08-22
- Git automation library research (raw subprocess, GitPython, pygit2, Dulwich, hybrid) completed, using current primary documentation (research date 2026-08-24)
- ADR-0005 (Git automation: purpose-built subprocess wrapper around real `git`) accepted 2026-08-24
- Qdrant deployment research completed; ADR-0006 (Docker server, localhost-bound) accepted 2026-08-24
- MCP tool contract research completed; ADR-0007 (full tool table, MRTR confirmation, no destructive Git ops) accepted 2026-08-24
- Embeddings/sparse/reranker model research completed; ADR-0008 (BGE-M3 + bge-reranker-v2-m3 + miniCOIL) accepted 2026-08-24
- Filesystem event architecture research completed; ADR-0009 (light debouncing + idempotent jobs + reconciliation backstop) accepted 2026-08-24
- All nine Phase 0 technology-selection ADRs now accepted
- `docs/LONGEVITY_NOTES.md` written (long-term viability reasoning across all nine ADRs)
- `docs/ARCHITECTURE.md` written (consolidated architecture: layers, component inventory, data flow narratives, trust boundaries, consolidated open questions)
- Real user vault sample inspected (structure only, via a private GitHub repo the user pointed to) — confirmed the master spec's AI-origin-folder example is the user's actual vault, and revealed the existing content has no YAML frontmatter (positional/inline metadata instead) — this finding is incorporated into `docs/DATA_MODEL.md` and `docs/EVENT_MODEL.md`
- `docs/DATA_MODEL.md` written (full SQLite DDL, Qdrant payload schema, validated against the real vault sample)
- `docs/EVENT_MODEL.md` written (event taxonomy, envelope schema, primary pipeline walkthrough, failure/recovery handling, MCP mapping) — recommends new ADR-0010 for an `events` audit table
- Security threat model drafted (STRIDE + OWASP LLM Top 10 2026), then adversarially red-team reviewed against primary sources (CVEs, incidents, framework existence all fact-checked); `docs/SECURITY_MODEL.md` updated with the reviewed, corrected version, including several genuinely new findings (no OS-level sandboxing considered anywhere; Huey async-bridge DoS surface; FTS5 query-grammar injection; Qdrant alias-race; `chonkie` never got the supply-chain scrutiny other libraries did)
- `docs/TESTING_STRATEGY.md` and `docs/GIT_WORKFLOW.md` elaborated with concrete per-subsystem test cases and operational runbooks (Qdrant upgrade, gitleaks setup, auto-commit/push policy, branching policy)
- All Phase 0 exit-criteria deliverables per `docs/00_MASTER_PROJECT_SPECIFICATION.md` §18 are now complete
- ADR-0010 (events audit/replay table) drafted and accepted 2026-08-27 — see `docs/adr/0010-event-audit-log.md`
- Four Article-2 design documents written for all six P0 security checklist items, each with purpose/responsibilities/interfaces/dependencies/failure-modes/security-considerations/test-strategy:
  - `docs/design/vault-safety-boundary.md` — path-traversal/symlink mechanism (P0 #1) + `python-frontmatter` YAML-safety verification (P0 #3, confirmed safe by default against upstream source)
  - `docs/design/os-level-process-sandboxing.md` — systemd hardening for the Huey worker + bubblewrap sandboxing for the stdio MCP server (P0 #2); corrects two specifics in the threat model's own literal wording (`DynamicUser=` doesn't fit ATHENA AI-BRAIN's vault-ownership model; `ProtectHome=yes` needed, not `read-only`)
  - `docs/design/storage-runtime-hardening.md` — Huey serializer startup assertion (P0 #4, hard-fail) + SQLite/Qdrant file-permission hardening (P0 #5, two-tier fail policy); found Qdrant's Docker root-by-default behavior means host-side chmod alone doesn't protect the data directory without also pinning a non-root image variant
  - `docs/design/pre-ingestion-secret-scanning.md` — `detect-secrets` in-process scanning before chunking/embedding (P0 #6); recommends redact-and-flag by default (not hard-block) specifically because the real vault contains legitimate OWASP security-training content that would otherwise become permanently unsearchable

- ADR-0011 (secret-scan schema) drafted and accepted 2026-08-27 — see `docs/adr/0011-secret-scan-schema.md`
- Phase 1 foundational scaffolding implemented 2026-08-27: `src/athena` package, `pyproject.toml` (hatchling, mypy --strict, ruff), `config.py`, `logging_setup.py`, `cli.py`, `diagnostics.py` (`athena doctor`/`athena version`)
- All four P0 security design docs implemented as working, tested code 2026-08-27: `athena.safety.{paths,content}`, `athena.hardening.{serializer,permissions}`, `athena.security.secrets`, plus `deployment/systemd/` and `deployment/bubblewrap/` configs (verified against this environment's real systemd 259 / bubblewrap 0.11.1)
- 87/87 tests passing, mypy --strict clean, ruff clean, live `athena doctor` CLI run verified end-to-end 2026-08-27
- Phase 1 work committed and pushed to `github.com/A3lxq/ATHENA_AI_BRAIN` `main` 2026-08-27 (merged with the repo's pre-existing history, no history discarded)
- `docs/design/migration-runner-and-vault-ingestion.md` drafted and accepted 2026-08-28 — migration runner, minimal repository layer, vault ingestion pipeline (watcher/ingest/bootstrap/reconcile), `athena.worker`
- Migration runner + full `DATA_MODEL.md`/`EVENT_MODEL.md`/ADR-0011 schema (3 numbered migrations) implemented and applied for the first time 2026-08-31, with atomic rollback and checksum-drift detection verified empirically (a real `executescript()` non-atomicity gotcha was found and worked around)
- Repository layer (`athena.db.repository.{notes,tags,provenance,lifecycle,events,research_jobs,secret_findings}`), provenance inference, filesystem watcher (real `watchdog` 6.0.0 behavior verified, one genuine discrepancy found: spurious `DirModifiedEvent` synthesis), vault lifecycle service, the idempotent `ingest_note` job (secret-scan persistence into ADR-0011's schema now wired to a real caller for the first time), bootstrap, reconciliation, and `athena.worker` all implemented 2026-08-31
- CLI gained `athena migrate`/`athena ingest bootstrap`/`athena ingest reconcile`; doctor gained a `schema_version` check
- 211/211 tests passing, mypy --strict clean, ruff clean; live end-to-end CLI verification against a 3-note fixture vault (all three real content shapes) confirmed correct database state 2026-08-31
- Phase 2 work committed and pushed to `github.com/A3lxq/ATHENA_AI_BRAIN` `main` 2026-08-31 (`aa76ce7`)
- `docs/design/indexing-pipeline.md` drafted and accepted 2026-09-02 — chunking, embedding, Qdrant store, the `index_note` job, `index_state` schema resolution; research included a critical `sentence-transformers` CVE found and resolved by direct source verification
- `athena.indexing.{chunking,embedding,qdrant_store,index_note}` implemented and tested 2026-09-02; `athena.worker`/`athena.vault.bootstrap`/`athena.vault.reconcile` wired to chain indexing after ingestion with graceful Qdrant-unreachable degradation; migration 0004 (`index_state`/`last_index_error`)
- CLI gained `athena index bootstrap`; doctor gained a `qdrant_reachable` check
- 241/241 tests passing (4 correctly skipped pending Docker/Qdrant access), mypy --strict clean, ruff clean; live end-to-end CLI verification (migrate/doctor/ingest bootstrap/index bootstrap) confirmed correct graceful-degradation behavior 2026-09-02
- Phase 3 work committed and pushed to `github.com/A3lxq/ATHENA_AI_BRAIN` `main` (`561f8d4`)
- `docs/design/retrieval-pipeline.md` drafted and accepted 2026-09-03 — keyword search, vector search, RRF fusion, cross-encoder reranking, context construction, evaluation harness; research found a real Qdrant embedded-mode filter bug and corrected a documentation-derived FTS5 phrase-concatenation misunderstanding before implementation
- `athena.retrieval.{keyword_search,vector_search,fusion,reranking,context,search,evaluation}` implemented and tested 2026-09-03; a real `CrossEncoder` API-drift finding (`activation_fn`, not `default_activation_function`) caught during implementation
- CLI gained `athena retrieval evaluate [--corpus PATH]`
- 296/296 tests passing (55 new this session, 5 correctly skipped pending Docker/Qdrant access), mypy --strict clean, ruff clean; live end-to-end CLI verification against the real 10-note eval corpus (Qdrant unreachable throughout) confirmed and documented a real architectural finding: keyword-only degradation currently returns zero results, not degraded ones, when no note has ever been successfully indexed — see `docs/design/retrieval-pipeline.md` §8

## Not Yet Completed

- Adding `secret_findings_list`/`secret_finding_resolve` to ADR-0007's tool contract table (a lightweight cross-reference now, or a full entry at implementation time — open question in ADR-0011)
- Status promotion from `draft` to `active` — no mechanism decided yet for when freshly-ingested legacy content leaves `draft` (open item, carried since Phase 2's design doc §8)
- `watchdog`'s CVE/maintainer-trust supply-chain review — still flagged as required, not yet actually done
- `fastembed`/miniCOIL has no revision-pinning mechanism — a real, documented gap in `SECURITY_MODEL.md` P1 item 15's coverage; open until fastembed adds one or ATHENA AI-BRAIN builds a pre-downloaded-snapshot wrapper
- **Live Qdrant integration testing is blocked in this development environment** — the current user isn't in the `docker` group and interactive `sudo` isn't available; 4 real, correct integration tests are written and `skip`-marked pending this
- `athena.mcp_server` (stdio MCP server entry point) — still only a placeholder in the bubblewrap script; Phase 6
- Deciding the real install location/venv path referenced by the systemd unit and bwrap script (currently placeholder paths under `%h`/`$HOME`)
- **Confirmed live gap (§8 of the retrieval design doc)**: keyword-only degradation returns zero results, not degraded ones, when no note has any indexed chunks (i.e. Qdrant has never been reachable) — a real emergent property of two individually-correct, already-tested mechanisms composing badly; a design decision for a future phase/addendum ADR, not fixed in this pass
- The retrieval-evaluation corpus ships at 10 notes/17 questions, not `TESTING_STRATEGY.md`'s 30-60 note target (explicitly flagged, §2.6/§8 of the design doc)
- A structural/grep-based CI check enforcing "only `sanitize_fts5_query`-passed strings ever reach a `MATCH` expression" — recommended, not built
- No regression-gating threshold on `athena retrieval evaluate` — it always exits 0, reporting numbers for a human/future CI step to compare, not gating the build itself
- Committing this Phase 4 work to git (nothing has been committed yet this session — awaiting explicit user go-ahead)

## Immediate Next Step

Decide with the user whether to commit the current Phase 4 retrieval work now, and whether to resolve the Docker-access blocker (add the current user to the `docker` group, restart the session) so the skipped integration tests (Phase 3 and Phase 4 combined: 5) can actually run against a live Qdrant server, and so the "keyword-only degradation returns zero results" finding can be re-verified against a real end-to-end indexed vault rather than only the Qdrant-down scenario. After that, the next substantive work is **Phase 5 — Knowledge Intelligence** per `docs/ROADMAP.md`: duplicate detection, the merge engine, provenance, and lineage.

## Important Constraint

Do not start production implementation until Phase 0 exit criteria are satisfied. (Satisfied — Phases 1 through 4 are all implemented and tested.)

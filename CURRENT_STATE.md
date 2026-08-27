# AI_BRAIN — Current State

## Project

AI_BRAIN

## Development Mode

Ground-up development with Claude Code

## Current Phase

Phase 1 — Foundation (in progress)

## Current Status

**Phase 0 is fully closed** (all eleven ADRs accepted, all exit criteria satisfied). **Phase 1 foundational scaffolding is now implemented, tested, and verified**: a real `src/`-layout Python package (`ai_brain`) exists with `pyproject.toml` (hatchling, mypy --strict, ruff), config loading, structured JSON logging, a `doctor` diagnostics command, and a minimal CLI (`ai-brain doctor` / `ai-brain version`). **All four P0 security design docs are now implemented as working code**, not just designs:

- `ai_brain.safety.paths` / `ai_brain.safety.content` — vault safety boundary (`SafeVaultPath`, `resolve_vault_path()`, `parse_note_safely()`)
- `ai_brain.hardening.serializer` / `ai_brain.hardening.permissions` — storage/runtime hardening (`assert_safe_job_serializer()`, `ensure_private_file()`/`ensure_private_dir()`)
- `ai_brain.security.secrets` — pre-ingestion secret scanning (`scan_note_for_secrets()`, `redact_high_confidence_spans()`)
- `deployment/systemd/ai-brain-huey-worker.service` + `deployment/bubblewrap/ai-brain-mcp-launch.sh` — OS-level process sandboxing, verified against this environment's actual systemd 259 and bubblewrap 0.11.1 (both configs are explicitly marked as placeholders for the worker/MCP-server entry points and install paths, which don't exist yet — see "Not Yet Completed")

87/87 tests passing, mypy --strict clean, ruff clean, and a live end-to-end `ai-brain doctor` run confirmed working. **No git commit has been made yet** — `git init -b main` only; all Phase 1 files are untracked pending an explicit user go-ahead to commit.

## Accepted Architecture

- Runtime language: **Python** — ADR-0001 accepted 2026-08-22 (see `docs/adr/0001-runtime-language-selection.md`)
- Job/queue library: **Huey with SQLite backend** (`SqliteHuey`), default serializer swapped off pickle, **own SQLite file separate from AI_BRAIN's metadata database** (confirmed by ADR-0004) — ADR-0002 accepted 2026-08-22 (see `docs/adr/0002-job-queue-architecture.md`); validate the async bridge (`aget_result()`) against one real job type early in Phase 1, with the hand-rolled asyncio+SQLite queue as documented fallback if that proves awkward
- Event audit/replay log: **narrow append-only `events` table** in AI_BRAIN's metadata SQLite database (not Huey's), recording domain-meaningful transitions across filesystem/Git/job/dedup/reconciliation domains per a shared envelope schema — ADR-0010 accepted 2026-08-27 (see `docs/adr/0010-event-audit-log.md`); retention/pruning policy deferred until table growth is measured
- Secret-scan schema: **`notes.secret_scan_status` column** (orthogonal to `status`/`index_state`) + **`note_secret_findings`** + **`secret_scan_allowlist`** tables in `ai_brain.db`, fingerprint-keyed allowlisting with mandatory `reason` — ADR-0011 accepted 2026-08-27 (see `docs/adr/0011-secret-scan-schema.md`); formalizes the schema `docs/design/pre-ingestion-secret-scanning.md` specifies; two new MCP tools (`secret_findings_list`, `secret_finding_resolve`) still need adding to ADR-0007's tool table
- RAG orchestration: **hand-rolled composable primitives** — `qdrant-client` + `chonkie` (chunking) + SQLite FTS5 (keyword search) + hand-written cross-store fusion + `sentence-transformers` (embeddings/reranking) + a small `Protocol`-based multi-provider LLM adapter — no LangChain/LlamaIndex adoption. ADR-0003 accepted 2026-08-22 (see `docs/adr/0003-rag-orchestration-approach.md`); verify `chonkie`'s frontmatter handling early in Phase 1
- SQLite access layer: **hand-rolled thin repository layer over `aiosqlite`** — typed functions per query, parameterized SQL, a minimal `PRAGMA user_version`-driven migration runner, FTS5 external-content tables with hand-written trigger sync; Peewee documented as fallback. ADR-0004 accepted 2026-08-22 (see `docs/adr/0004-sqlite-access-layer.md`); verify connection-management pattern (single connection vs. small pool) during Phase 1 prototyping
- Git automation: **purpose-built module wrapping the real `git` CLI via `asyncio.create_subprocess_exec`** — argument lists only, `--`/`--end-of-options` insertion, explicit branch-name allow-listing, hand-built failure taxonomy, `gitleaks` via `pre-commit` for secret scanning; no GitPython; Dulwich (`--pure`) optional and read-only-only. ADR-0005 accepted 2026-08-24 (see `docs/adr/0005-git-automation-library.md`); verify Kali's git version supports `--end-of-options` per-subcommand before Phase 1
- Qdrant deployment: **Docker server, bound to `127.0.0.1` only**, `--restart unless-stopped`, pinned image tag, snapshot-before-upgrade runbook required. ADR-0006 accepted 2026-08-24 (see `docs/adr/0006-qdrant-deployment-mode.md`); `QdrantClient(":memory:")` permitted only for non-fusion-critical unit tests
- MCP tool contract: full tool table (search/read/create/update/move/delete/related/duplicate-detection/merge/research/summarize/link/reindex/status/history/provenance/Git-ops/diagnostics) — destructive ops (`note_delete`, `note_merge`) require MRTR elicitation confirmation; genuinely irreversible Git operations excluded from the MCP surface entirely; interim `job_status`/`job_cancel` shim pending official SDK tasks-extension support. ADR-0007 accepted 2026-08-24 (see `docs/adr/0007-mcp-tool-contract.md`); **no protocol-level defense exists against retrieved-content/instruction conflation** — mitigation is application-level defense-in-depth, must be named explicitly in the security threat model
- Embeddings/sparse/reranker: **BGE-M3** (dense) + **bge-reranker-v2-m3** (reranker) + Qdrant **miniCOIL** via `fastembed` (sparse); Qwen3-Embedding-0.6B + Qwen3-Reranker-0.6B documented as a close fallback; BM25 fallback for the sparse leg if vault language composition is meaningfully non-English. ADR-0008 accepted 2026-08-24 (see `docs/adr/0008-embeddings-model-choice.md`); provisional-but-documented, re-evaluate in 6–12 months via a new ADR; collection access must use an alias, never a hardcoded name
- Filesystem event architecture: light, non-semantic debouncing (quiet-window per-path) + idempotent Huey jobs as the real safety net + a periodic/startup reconciliation (full-scan) job as backstop; `.git`/`.obsidian`/plugin-cache excluded from watch scope; `fs.inotify.max_user_watches` sysctl raise documented as a deployment prerequisite. ADR-0009 accepted 2026-08-24 (see `docs/adr/0009-filesystem-event-architecture.md`)
- Obsidian vault as source of truth
- AI_BRAIN separate from the vault
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
  - `docs/design/os-level-process-sandboxing.md` — systemd hardening for the Huey worker + bubblewrap sandboxing for the stdio MCP server (P0 #2); corrects two specifics in the threat model's own literal wording (`DynamicUser=` doesn't fit AI_BRAIN's vault-ownership model; `ProtectHome=yes` needed, not `read-only`)
  - `docs/design/storage-runtime-hardening.md` — Huey serializer startup assertion (P0 #4, hard-fail) + SQLite/Qdrant file-permission hardening (P0 #5, two-tier fail policy); found Qdrant's Docker root-by-default behavior means host-side chmod alone doesn't protect the data directory without also pinning a non-root image variant
  - `docs/design/pre-ingestion-secret-scanning.md` — `detect-secrets` in-process scanning before chunking/embedding (P0 #6); recommends redact-and-flag by default (not hard-block) specifically because the real vault contains legitimate OWASP security-training content that would otherwise become permanently unsearchable

- ADR-0011 (secret-scan schema) drafted and accepted 2026-08-27 — see `docs/adr/0011-secret-scan-schema.md`
- Phase 1 foundational scaffolding implemented 2026-08-27: `src/ai_brain` package, `pyproject.toml` (hatchling, mypy --strict, ruff), `config.py`, `logging_setup.py`, `cli.py`, `diagnostics.py` (`ai-brain doctor`/`ai-brain version`)
- All four P0 security design docs implemented as working, tested code 2026-08-27: `ai_brain.safety.{paths,content}`, `ai_brain.hardening.{serializer,permissions}`, `ai_brain.security.secrets`, plus `deployment/systemd/` and `deployment/bubblewrap/` configs (verified against this environment's real systemd 259 / bubblewrap 0.11.1)
- 87/87 tests passing, mypy --strict clean, ruff clean, live `ai-brain doctor` CLI run verified end-to-end 2026-08-27

## Not Yet Completed

- Adding `secret_findings_list`/`secret_finding_resolve` to ADR-0007's tool contract table (a lightweight cross-reference now, or a full entry at implementation time — open question in ADR-0011)
- Real vault ingestion pipeline (filesystem watcher, debouncing, reconciliation job per ADR-0009)
- SQLite migration runner + repository layer per ADR-0004/ADR-0011 (the secret-scan schema is designed and accepted but no migration has been written or applied yet — `ai_brain.db` does not yet have any tables)
- `ai_brain.worker` (Huey worker entry point) and `ai_brain.mcp_server` (stdio MCP server entry point) — both are referenced only as placeholders in the deployment configs; neither module exists yet
- Wiring `scan_note_for_secrets()` into an actual ingestion job (the module is implemented and tested standalone, but nothing calls it as part of a pipeline yet)
- Deciding the real install location/venv path referenced by the systemd unit and bwrap script (currently placeholder paths under `%h`/`$HOME`)
- Committing this Phase 1 work to git (nothing has been committed yet — awaiting explicit user go-ahead)

## Immediate Next Step

Decide with the user whether to commit the current Phase 1 foundational scaffolding now. After that, the next substantive Phase 1 work is the SQLite migration runner + repository layer (ADR-0004/ADR-0011) and the real vault ingestion pipeline (ADR-0009), since the four P0 security modules now need a real caller to be wired into.

## Important Constraint

Do not start production implementation until Phase 0 exit criteria are satisfied. (Satisfied — Phase 1 implementation is now underway.)

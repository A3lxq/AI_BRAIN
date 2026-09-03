# ATHENA AI-BRAIN — Consolidated Architecture Document

- **Date:** 2026-08-24
- **Author:** Claude Code (ATHENA AI-BRAIN Phase 0)
- **Status:** Phase 0 exit-criteria deliverable — architecture is documented
- **Scope:** Synthesizes `00_MASTER_PROJECT_SPECIFICATION.md`, `DEVELOPMENT_CONSTITUTION.md`, ADR-0001 through ADR-0009, and `LONGEVITY_NOTES.md` into one architecture-level reference.
- **Companion document:** `docs/SECURITY_MODEL.md` (threat model, produced in parallel). Section 5 below establishes *where* trust boundaries sit; it does not enumerate *what can go wrong* at each one — that belongs in the threat model.
- **Non-goal:** This document invents no new technology decisions. Every claim below traces to an accepted ADR or to the master specification/constitution. Where the source material is silent, ambiguous, or internally in tension, that is flagged explicitly rather than resolved here.

## 1. System Overview

ATHENA AI-BRAIN is a vendor-agnostic, event-driven AI Knowledge Operating System built around an Obsidian vault. Its governing philosophy, stated in the master specification, is that **"knowledge should outlive AI models"**: the vault's Markdown notes are the durable asset, and every AI provider, embedding model, or orchestration library in this design is treated as a replaceable component, never as the thing the system is built around.

This produces a hard separation of concerns. The **Obsidian vault** is the sole source of truth for knowledge — it is authoritative, human-owned, and human-readable independent of ATHENA AI-BRAIN's existence. **ATHENA AI-BRAIN** is infrastructure that sits beside the vault, never inside it: it observes the vault's filesystem, parses and indexes content, retrieves relevant knowledge on request, performs controlled writes back into the vault, records provenance, and maintains Git-backed version history. ATHENA AI-BRAIN must never become a second canonical copy of the user's knowledge, and its own repository (code, config, tests, derived state/databases) must remain physically and conceptually separate from the vault repository. This separation is why nearly every accepted ADR independently converges on the same meta-pattern (documented in `LONGEVITY_NOTES.md`): favor thin, replaceable wrappers over durable standards (SQL, git, the Qdrant wire protocol) rather than frameworks that would "own" ATHENA AI-BRAIN's architecture — LangChain, LlamaIndex, GitPython, and Celery were all rejected substantially on this basis.

## 2. Layered Architecture

The system decomposes into seven layers. The load-bearing architectural rule — stated in the master specification (§4) and enforced by construction in ADR-0007 — is that **the MCP layer is an interface, not the business-logic layer**: every MCP tool is a thin wrapper over an internal function that is independently callable and independently testable without MCP present at all.

```
┌──────────────────────────────────────────────────────────────────────┐
│  EXTERNAL CONSUMERS                                                    │
│  MCP clients (Claude Code, other MCP hosts) · human operator (git CLI) │
└───────────────────────────────┬────────────────────────────────────────┘
                                 │  MCP protocol (2026-07-28 spec, stateless, MRTR)
┌───────────────────────────────▼────────────────────────────────────────┐
│  1. MCP TRANSPORT LAYER                            (ADR-0007)          │
│     modelcontextprotocol/python-sdk · Resources (vault://) · Tools      │
│     Thin wrappers only — no business logic lives here.                 │
│     Elicitation/MRTR confirmation for destructive tools.               │
│     Interim job_status/job_cancel shim (pending official tasks ext.)   │
└───────────────────────────────┬────────────────────────────────────────┘
                                 │  direct function calls (no MCP dependency)
┌───────────────────────────────▼────────────────────────────────────────┐
│  2. INTERNAL BUSINESS-LOGIC LAYER          (Master Spec §4, ADR-0007)   │
│     Search · Read · Create/Update/Move/Delete · Related · Dedup/Merge  │
│     · Research · Summarize · Provenance · Status/Diagnostics           │
│     Each function independently testable; MCP-agnostic; provider-      │
│     agnostic. Calls down into layers 3–6.                               │
└───────┬───────────────┬───────────────┬───────────────┬───────────────┘
        │               │               │               │
┌───────▼──────┐┌───────▼───────────┐┌──▼─────────────┐┌▼─────────────────┐
│3. JOB/EVENT  ││4. STORAGE LAYER   ││5. RAG/RETRIEVAL ││6. GIT AUTOMATION  │
│   LAYER      ││   (ADR-0004,      ││   LAYER         ││   LAYER (ADR-0005)│
│ (ADR-0002,   ││    ADR-0006)      ││ (ADR-0003,      ││ subprocess/       │
│  ADR-0009)   ││                   ││    ADR-0008)    ││ asyncio.create_   │
│ watchdog     ││ SQLite metadata   ││ chunking        ││ subprocess_exec   │
│ Observer →   ││ store (aiosqlite, ││ (chonkie) →     ││ over real `git`   │
│ light debounce││ hand-rolled repo  ││ embedding       ││ CLI, argv-only,   │
│ → Huey enqueue││ layer, FTS5)      ││ (BGE-M3) →      ││ never shell=True. │
│ Huey/SqliteHuey││ separate .db     ││ dense+sparse    ││ pre-commit +      │
│ (own .db file)││ file from Huey's ││ (miniCOIL) →    ││ gitleaks secret   │
│ periodic + one-││ job store        ││ Qdrant upsert   ││ scanning. Dulwich │
│ off jobs      ││                  ││ hybrid fusion   ││ (--pure) optional,│
│ idempotent,   ││ Qdrant vector    ││ (RRF/DBSF) →    ││ read-only only.   │
│ lock_task     ││ store (Docker,   ││ reranking       ││ Destructive Git   │
│ reconciliation││ 127.0.0.1-only)  ││ (bge-reranker)  ││ ops never exposed │
│ full-scan job ││                  ││ → context build ││ via MCP.          │
└──────────────┘└───────────────────┘└─────────────────┘└───────────────────┘
                                 │
┌───────────────────────────────▼────────────────────────────────────────┐
│  7. EXTERNAL INTEGRATIONS                                               │
│     Multi-provider LLM adapter — Protocol-based, hand-rolled or narrow  │
│     pinned litellm (ADR-0003) — over OpenAI/Anthropic/Google/Ollama SDKs│
│     No provider-specific code leaks into layers 2–6 (Master Spec §13). │
└──────────────────────────────────────────────────────────────────────┘
```

Layers 3–6 are peers, not a strict stack: the business-logic layer (2) orchestrates across all four depending on the operation (e.g., a create-note flow touches the storage layer, the RAG layer, and eventually the Git layer, but never goes through the job layer unless it's dispatched as background work). Layer 7 is reached only through layer 2 or the RAG layer's LLM adapter — never called directly from the MCP layer or the job layer.

## 3. Component Inventory

| Component | Responsibility | Deciding ADR(s) | Key dependencies / interfaces |
|---|---|---|---|
| **Vault Watcher** | Single `watchdog` `Observer` on the vault root (`recursive=True`); excludes `.git`/`.obsidian`/plugin-cache subtrees | ADR-0009 (also ADR-0001 for `watchdog` choice) | Feeds raw events to the Event Debouncer; requires `fs.inotify.max_user_watches` sysctl raise on Kali/Debian |
| **Event Debouncer** | Normalizes every raw event to "path P changed" (a move = two path-changed signals); per-path last-seen-timestamp map with a ~1–2s fixed quiet window | ADR-0009 | Calls Job Queue's enqueue function directly and synchronously from the debounce thread (no asyncio bridge needed) |
| **Job Queue (Huey)** | Durable job execution for indexing, research/ingestion, reindexing, dedup/merge scans, Git commit/push; retry, dead-letter, cron scheduling via `@huey.periodic_task` | ADR-0002 (also ADR-0004 for its separate SQLite file) | `SqliteHuey` backend, own `.db` file distinct from the metadata store; `SignedSerializer`/JSON in place of default pickle; `huey.lock_task` for per-path concurrency control; exposed to MCP via interim `job_status`/`job_cancel` shim |
| **Metadata Store (SQLite)** | Note metadata, provenance/lineage, knowledge-lifecycle status, duplicate-detection records, FTS5 keyword index | ADR-0004 (ADR-0003 for FTS5 usage) | Hand-rolled thin repository layer over `aiosqlite`; `PRAGMA user_version`-driven migration runner; FTS5 external-content tables with trigger-based sync; every connection must set `PRAGMA busy_timeout` explicitly (per-connection, resets to zero) |
| **Vector Store (Qdrant)** | Dense + sparse vector storage and native hybrid fusion (RRF/DBSF) | ADR-0006 (ADR-0003 for hybrid-fusion commitment, ADR-0008 for schema) | Docker server bound to `127.0.0.1` only, pinned image tag, snapshot-before-upgrade; accessed only via a collection **alias**, never a hardcoded name; `qdrant-client` (official, sync+async) |
| **Chunking Engine** | Structure-aware Markdown chunking | ADR-0003 | `chonkie`; frontmatter handling must be verified in Phase 1 (fallback: `markdown-it-py` frontmatter plugin, or strip-before-chunk) |
| **Embedding Engine** | Dense embedding + sparse-vector generation | ADR-0008 (built on `sentence-transformers`/`fastembed` per ADR-0003) | `BAAI/bge-m3` (dense, MIT, 100+ languages, 8192-token context) + `Qdrant/minicoil-v1` (sparse, via `fastembed`; English-only, documented BM25 fallback if vault is meaningfully multilingual); documented alternative: `Qwen3-Embedding-0.6B` |
| **Hybrid Retrieval/Fusion Engine** | Combines Qdrant vector search, SQLite FTS5 keyword search, metadata/tag/folder filtering; fuses results | ADR-0003 (Qdrant native fusion per ADR-0006/0008 schema) | Qdrant-native RRF/DBSF for the vector+sparse leg; a small hand-written cross-store fusion module (or `ranx`) to merge in FTS5 results; feeds the Reranker |
| **Reranker** | Cross-encoder reranking of fused candidates | ADR-0008 (mechanism per ADR-0003) | `BAAI/bge-reranker-v2-m3` via `sentence-transformers`' `CrossEncoder` |
| **LLM Provider Adapter** | Vendor-agnostic interface to external LLM providers for summarize/research operations | ADR-0003 (Master Spec §13) | Small `Protocol`-based adapter over official OpenAI/Anthropic/Google/Ollama SDKs, or narrow/pinned `litellm` (decision deferred to implementation); no provider-specific code above this layer |
| **Duplicate Detection Engine** | Multi-signal duplicate candidate detection: content hash, lexical (MinHash-LSH), semantic (embedding cosine), metadata/provenance | ADR-0003 (Master Spec §10) | `datasketch` MinHash-LSH + embedding similarity via the Embedding Engine + content hash; feeds a hand-written merge-policy layer; exposed via `note_duplicates` (single-note) and `duplicates_scan` (task-backed, vault-wide) |
| **Provenance/Lineage Tracker** | Records origin (model/provider), source URLs, transformation/merge history, human edits, superseded versions | ADR-0003 (Master Spec §9) | Custom schema designed against the W3C PROV (PROV-DM/PROV-O) entity/activity/agent/derivation model; persisted in the Metadata Store; exposed via `note_provenance` |
| **Git Automation Module** | Status detection, safe commit/push, rollback/recovery, conflict detection, dry-run, secret scanning | ADR-0005 | `subprocess`/`asyncio.create_subprocess_exec` over real `git` CLI, argv-only, never `shell=True`; `--`/`--end-of-options` pathspec guarding; `pre-commit` + `gitleaks`; optional read-only Dulwich (`--pure` mode) for status/diff/log convenience; destructive ops (force-push, hard reset, branch delete, history rewrite) never exposed via MCP |
| **MCP Server** | Single external interface exposing the accepted tool contract | ADR-0007 | `modelcontextprotocol/python-sdk`; Resources (`vault://{path}`) + Tools (sync and task-backed); every tool a thin wrapper over layer 2 |

## 4. Data Flow Narratives

### 4.1 Note created/modified in vault → indexed in Qdrant + SQLite

1. **Vault Watcher** (ADR-0009) observes a raw filesystem event (create, modify, or a move that inotify may degrade into delete+create across watch boundaries).
2. **Event Debouncer** normalizes it to "path P changed" and holds it in a per-path last-seen-timestamp map through a short quiet window (~1–2s, tuned in Phase 1) to absorb editor save patterns (temp-file+rename).
3. Once the path settles, the debounce callback **directly and synchronously** calls the Huey enqueue function (no asyncio bridge — Huey's SQLite enqueue is just a DB write) (ADR-0009 §3).
4. The **Job Queue** (Huey/SqliteHuey, ADR-0002) durably records the indexing job. `huey.lock_task` prevents two concurrent index jobs on the same path.
5. The indexing job (executed by a Huey worker) first checks **idempotency**: compares current file mtime/content-hash against the Metadata Store's recorded state, and no-ops if unchanged (ADR-0009 §4) — every triggered job treats "path X changed" as a signal to re-derive current truth from disk, never as a diff to apply.
6. If changed, the job reads the note, strips/parses YAML frontmatter, and runs it through the **Chunking Engine** (`chonkie`, ADR-0003).
7. Each chunk is embedded by the **Embedding Engine** (BGE-M3 dense + miniCOIL sparse, ADR-0008) and upserted into **Qdrant** (ADR-0006) via its collection alias, with original chunk text retained independently in the **Metadata Store** (ADR-0004, ADR-0008) so a future embedding-model swap is a backfill job, not a data-migration crisis.
8. Note-level metadata (title, provenance, status, tags, FTS5 keyword index) is written/updated in the **Metadata Store** (ADR-0004), with FTS5 external-content triggers keeping the keyword index in sync.
9. Independently of this event-driven path, a **periodic/startup reconciliation (full-scan) job** compares vault-on-disk state against index state as a backstop for events dropped during downtime, queue overflow, or inotify's cross-boundary move-degradation (ADR-0009 §5) — this is required, not optional, per the master specification's durability requirement.

### 4.2 User query via MCP → hybrid retrieval → response

1. An MCP client calls the `vault_search` tool (or reads `vault://{path}` for direct content access) (ADR-0007).
2. The **MCP Server**'s thin wrapper calls the corresponding internal search function in the **business-logic layer** — the same function is independently callable/testable without MCP present.
3. The **Hybrid Retrieval/Fusion Engine** (ADR-0003) issues: (a) a Qdrant query combining dense (BGE-M3) and sparse (miniCOIL) vectors with Qdrant-native RRF/DBSF fusion (ADR-0006, ADR-0008), and (b) a SQLite FTS5 keyword query (ADR-0004), optionally filtered by metadata/tags/folder.
4. Results from the vector leg and the FTS5 leg are merged by the small cross-store fusion module (hand-written RRF or `ranx`, deferred choice per ADR-0003).
5. The merged candidate set is reranked by the **Reranker** (`bge-reranker-v2-m3`, ADR-0008).
6. Context is constructed from the top reranked chunks (with original chunk text pulled from the Metadata Store, not re-derived from vectors) and returned to the MCP client as tool output or resource content.
7. Retrieved content is treated as **untrusted data** at this boundary (Master Spec §15, ADR-0007 §Rationale-3) — see Section 5 below.

### 4.3 Destructive operation (delete/merge) requested via MCP

1. An MCP client calls `note_delete` or `note_merge` (ADR-0007).
2. Per the accepted tool contract, these are classified **mutating, destructive**. The MCP Server does not execute immediately: it **MUST** use MRTR (Multi Round-Trip Request) elicitation, requiring the client to echo back the exact target path (for delete) or confirm the merge (for merge), before the underlying business-logic function runs.
3. This confirmation gate is enforced **server-side**, independent of any client-declared tool annotation (`destructiveHint`) — the MCP spec's own documentation states annotations are informational only and cannot be relied upon as an enforcement mechanism (ADR-0007 §Rationale-2/3).
4. `note_merge` is only reachable after a duplicate-detection step (`note_duplicates` or `duplicates_scan`) has produced a candidate pair/set; `dry_run` is supported and should be the default exploratory path.
5. Once confirmed, the internal function executes the deletion/merge against the vault filesystem, updates the Metadata Store and Qdrant accordingly (effectively re-triggering aspects of flow 4.1's indexing update for merge targets), and the operation is expected to trigger a Git commit for provenance (see 4.4).
6. Genuinely irreversible Git-level operations (force-push, hard reset, branch deletion, history rewrite) are **excluded from the MCP surface entirely** — not gated, excluded — per CLAUDE.md rules 22–23 and ADR-0007's explicit rejection of "confirmation gate as sufficient" for that class of operation, since a confirmation gate still depends on the calling model correctly relaying the request to a human.

### 4.4 Git backup/commit flow

1. Any mutating operation that changes vault content (`note_create`, `note_update`, `note_move`, `note_delete`, `note_merge`, `research_commit`) is expected to auto-invoke `git_commit` afterward for provenance trail purposes (ADR-0007's tool table: "Auto-invoked after other mutations for provenance trail; also exposed standalone").
2. `git_commit` calls into the **Git Automation Module** (ADR-0005), which builds an argv-only `git commit` invocation (never `shell=True`), with `--`/`--end-of-options` guarding any pathspec that could originate from dynamic/untrusted input.
3. Before the commit is finalized, the `pre-commit` framework runs with **gitleaks** as a secret scanner (subprocess, JSON output) — a required security control, not optional.
4. Push policy is configurable and safe (Master Spec §12); automatic pushing must be explicitly configured, never silently defaulted on.
5. Separately, periodic Git backup commits are also dispatched as **Huey periodic jobs** (`@huey.periodic_task`, ADR-0002 §Consequences) — i.e., the Git layer is reached both synchronously (post-mutation, via the MCP-triggered flow above) and asynchronously (on a schedule, via the Job Queue), and both paths converge on the same Git Automation Module.
6. Qdrant's own pre-upgrade snapshot step may also be folded into this same periodic Git-backup job (ADR-0006 §Open Questions) — **this is an explicitly unresolved placement decision**, flagged again in Section 6 below.
7. `note_history` (read-only) and `git_status`/`git_log` (read-only) surface this history back through MCP via the same Git Automation Module's read paths, without any risk of mutation.

## 5. Trust / Module Boundaries

This section identifies **where** untrusted input enters the system and which architectural layer is responsible for validating it, per the constitution's Article 8 (stable interfaces, MCP decoupled from business logic) and Article 9 (treat external web content, retrieved notes, AI output, and MCP input as potentially untrusted). It intentionally stops at "where" — `docs/SECURITY_MODEL.md` covers "what can go wrong" and specific mitigations in depth.

| Boundary | Untrusted input | Layer where it first enters | Layer(s) responsible for validation |
|---|---|---|---|
| **MCP client → MCP Server** | Tool call arguments (paths, note content, confirmation echoes) from any MCP host | MCP Transport Layer (ADR-0007) | The MCP wrapper performs schema/type validation before calling the business-logic layer; the business-logic layer itself does not trust that MCP validation was sufficient — no destructive action executes without server-side confirmation logic (see 4.3), independent of client-declared annotations |
| **Vault content → Chunking/Retrieval** | Markdown/frontmatter written by the user, by AI providers, or (via research flows) derived from the web | Job/Event Layer entry point (ADR-0009) into the RAG/Retrieval Layer (ADR-0003) | Master Spec §15: "Retrieved content must be treated as untrusted data unless verified." The business-logic layer, not the MCP layer, is where retrieved-content/instruction conflation must be mitigated (structured content envelopes, optional heuristic scanning) — ADR-0007 names this explicitly as a residual risk requiring layered, application-level defense-in-depth since no MCP protocol mechanism exists for it |
| **Web-research-ingested content → vault** | Content pulled by the (task-backed) `research_start` flow before it becomes a vault note | RAG/Retrieval Layer's research pathway → Provenance Tracker | `research_commit` is a deliberately separate, explicit step from `research_start` (defaults `dry_run=true`), giving a human-in-the-loop checkpoint between ingestion and the content becoming part of the vault; provenance metadata must distinguish source material from AI-generated synthesis (Master Spec §9) |
| **Git subprocess boundary** | Any dynamic/derived string (path, ref, branch name) that reaches an argv passed to `git` | Git Automation Module (ADR-0005) | Argument-list-only invocation (never `shell=True`), `--`/`--end-of-options` insertion before any pathspec/ref of dynamic origin, and explicit allow-listing of branch/tag names before they reach argv (git does not itself forbid leading-dash ref names) |
| **Filesystem event boundary** | Raw inotify events, which are not authenticated or validated in any way — any process writing into the watched tree produces events | Vault Watcher / Event Debouncer (ADR-0009) | This boundary is explicitly **not** a security boundary in the ADRs as written — the debounce+idempotency design assumes events may be noisy or duplicated but does not address a hostile actor writing into the vault directly; this is a candidate gap examined further in `docs/SECURITY_MODEL.md` |

**Cross-cutting note on the decoupling requirement itself:** the master specification (§4) and ADR-0007 both state that internal capabilities must be callable and testable without MCP. This is itself a trust-boundary design choice, not merely a testability convenience: it means the business-logic layer's own input validation cannot assume MCP's schema validation already ran, because the same functions are reachable from Huey jobs, the reconciliation job, and (in tests) direct calls with no MCP layer present at all.

## 6. Consolidated Open / Deferred Decisions

The following implementation-time decisions are explicitly **not resolved** by any ADR — each is carried forward from its ADR's "Consequences" or "Open Questions" section. Listed here so a reader does not need to re-read all nine ADRs to find them.

**Runtime / tooling**
- Exact CI type-checking strictness policy (`mypy` vs. `pyright`, strictness level) — deferred to a Phase 1 design doc (ADR-0001).
- Monitor OpenAI's ownership of Astral (`uv`/`ruff`/`ty`) for licensing/maintenance posture changes (ADR-0001) — not a blocker, a watch item.

**Job queue**
- Validate Huey's sync-core/`aget_result()` async bridge against one real job type (e.g., single-note indexing) early in Phase 1; fall back to a hand-rolled asyncio+SQLite queue if it proves awkward (ADR-0002).

**RAG orchestration**
- Verify `chonkie`'s frontmatter handling early in Phase 1; fallback is `markdown-it-py`'s frontmatter plugin or a strip-before-chunk preprocessing step (ADR-0003).
- Multi-provider LLM adapter: hand-rolled `Protocol` vs. narrow/pinned `litellm` — deferred to implementation, explicitly low-stakes (ADR-0003).
- Cross-store fusion implementation: hand-written RRF vs. `ranx` — deferred to implementation (ADR-0003).
- Provenance schema must be formally designed against the W3C PROV model *before* the indexing subsystem is implemented (ADR-0003) — this is a required pre-implementation design doc, not yet written per this document's source material.

**SQLite access layer**
- Should Peewee be prototyped in parallel as a fallback before fully committing to the hand-rolled repository layer, given how closely the research rated the two? (ADR-0004)
- What connection-management pattern (single long-lived connection vs. small pool) fits actual concurrency once Huey and the MCP server are both live — flagged for Phase 1 verification (ADR-0004).

**Git automation**
- Whether the subprocess wrapper module's interface should be designed now or deferred until the MCP tool contract settles exact Git operations — the source material notes this was written *before* ADR-0007 existed; ADR-0007 is now accepted, so this dependency is arguably resolved in sequence, but no ADR explicitly closes the loop (ADR-0005). **Flagged as a sequencing note for whoever writes the Git module's own design doc.**
- Verify Kali's installed git version supports `--end-of-options` for every subcommand ATHENA AI-BRAIN uses (`checkout`/`reset` only gained it in git 2.43.1) before relying on it as a mitigation (ADR-0005).

**Qdrant deployment**
- Should the snapshot-before-upgrade runbook be a standalone documented procedure, or automated as part of the Git-backup Huey job? (ADR-0006) — **Note:** Section 4.4 above shows this decision directly affects how the Git Automation Module and the Job Queue interact; it is unresolved in both ADR-0005/0006/0007 and should be settled before the Git module's design doc is finalized.

**MCP tool contract**
- Should `note_summarize` (the one read tool with `openWorldHint=true`, calling an external LLM provider) require explicit user opt-in/configuration before being exposed? (ADR-0007)
- Should optional heuristic injection-pattern scanning (for retrieved-content/instruction conflation) be built in Phase 1, or deferred to the security threat model design step? (ADR-0007) — this bears directly on Section 5's flagged residual risk.
- The interim `job_status`/`job_cancel` shim must be migrated once the Python MCP SDK implements the official `io.modelcontextprotocol/tasks` extension — no target version/date is set (ADR-0007).

**Embedding/reranker models**
- Spot-check the live MTEB leaderboard directly (not via secondary aggregation) before Phase 1 model download — the original research could not fetch its JS-rendered table (ADR-0008).
- Phase 1 should include an explicit vault-language-composition measurement step to confirm or revisit miniCOIL vs. the BM25 sparse-vector fallback (ADR-0008).
- This entire model choice is **provisional-but-documented**: must be revisited via a new ADR within 6–12 months, or upon a measured case that a newer model materially outperforms it — not changed silently (ADR-0008, Constitution Article 14).

**Filesystem events**
- Exact quiet-window duration (1–2s suggested) must be tuned empirically against real Obsidian save behavior during Phase 1 (ADR-0009).
- Reconciliation/full-scan job's exact trigger cadence (every startup, periodic, or both) is deferred to job/queue implementation design (ADR-0009).
- Whether to use `FileClosedEvent` (`IN_CLOSE_WRITE`, Linux-only) as an additional settle signal alongside the timestamp debouncer — low-stakes, decide during implementation (ADR-0009).
- The `fs.inotify.max_user_watches` sysctl raise must be documented as a Phase 1 deployment prerequisite (ADR-0009).

**Cross-ADR sequencing observation (not a contradiction, but worth naming):** ADR-0005 was proposed 2026-08-24 with an open question about whether its module design should wait for the MCP tool contract (ADR-0007, also 2026-08-24) — and ADR-0007 was indeed accepted the same day with a concrete Git-operations tool list. No document formally closes ADR-0005's open question against ADR-0007's now-settled contract; this document treats it as effectively resolved (the contract now exists) but flags that no ADR says so explicitly.

## References

- `docs/00_MASTER_PROJECT_SPECIFICATION.md`
- `docs/DEVELOPMENT_CONSTITUTION.md`
- `docs/DOCUMENTATION_STANDARDS.md`
- `docs/LONGEVITY_NOTES.md`
- `docs/adr/0001-runtime-language-selection.md` through `docs/adr/0009-filesystem-event-architecture.md`

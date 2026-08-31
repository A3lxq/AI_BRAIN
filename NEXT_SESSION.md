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
9. `docs/design/migration-runner-and-vault-ingestion.md` (implemented this session — read this before touching `ai_brain.db`/`ai_brain.vault`/`ai_brain.worker`)
10. `docs/sessions/2026-08-31_migration-runner-vault-ingestion.md` (this session's own record)

## Objective

**Phase 0 and Phase 1 are fully closed. Phase 2's foundational slice — migration runner + vault ingestion pipeline — is now implemented, tested, and verified end-to-end.** What exists as real, tested code as of this session:

- `ai_brain.db.migrate` — migration runner (3 numbered migrations already applying the full `DATA_MODEL.md`/`EVENT_MODEL.md`/ADR-0011 schema), with real atomic rollback and checksum-drift detection.
- `ai_brain.db.repository.{notes,tags,provenance,lifecycle,events,research_jobs,secret_findings}` — typed async repository functions.
- `ai_brain.vault.provenance_inference` — folder-name/shape → origin/provider inference (`DATA_MODEL.md` §0's rules).
- `ai_brain.vault.watcher` — real filesystem watcher/debounce over `watchdog` 6.0.0.
- `ai_brain.vault.lifecycle` — note lifecycle service (create/update/move/delete/transition_status).
- `ai_brain.vault.ingest` — the idempotent per-path ingestion job: metadata, provenance, lifecycle, secret-scan persistence (ADR-0011's schema has a real caller now), move/delete detection. Deliberately stops short of chunking/embedding.
- `ai_brain.vault.bootstrap` / `ai_brain.vault.reconcile` — one-time full-vault ingestion and the reconciliation backstop.
- `ai_brain.worker` — the Huey entry point (`huey_consumer.py ai_brain.worker.huey`), resolving the systemd unit's placeholder from Phase 1.
- CLI: `ai-brain migrate`, `ai-brain ingest bootstrap`, `ai-brain ingest reconcile`. Doctor gained a `schema_version` check.

211/211 tests passing, mypy --strict clean, ruff clean. **Live end-to-end verification was performed** (not just unit tests): `ai-brain migrate` → `ai-brain doctor` (all-ok, including the new schema check) → `ai-brain ingest bootstrap` against a 3-note fixture vault covering all three real content shapes (ChatGPT-style, Qwen-style, OWASP-style) → correct `notes`/`provenance`/`note_lifecycle_history`/`events`/`research_jobs` rows confirmed by direct database inspection → `ai-brain ingest reconcile` confirmed a true no-op on an already-current vault. **Nothing from this session has been committed to git yet** — Phase 1 was already committed and pushed in a prior session; this session's Phase 2 work is still untracked, awaiting explicit user go-ahead.

## Real findings from this implementation (verify-before-trust discipline, not assumed)

1. **`executescript()` doesn't respect an enclosing explicit transaction** — verified empirically against the installed `aiosqlite`/stdlib `sqlite3`: each statement auto-commits regardless of a surrounding `BEGIN`. The migration runner instead splits files into individual statements (respecting `CREATE TRIGGER ... BEGIN ... END;` bodies) and executes them one at a time inside an explicit transaction.
2. **`SqliteHuey(filename=":memory:")` does not work for `lock_task`** — Huey's storage opens a fresh connection per call, and `:memory:` gives each connection an empty, table-less database. Every test/fixture uses a real temp file for Huey.
3. **`watchdog` 6.0.0 synthesizes a spurious `DirModifiedEvent`** for a directory whenever a child inside it changes — not documented in ADR-0009's own research. The watcher filters out all `is_directory=True` events; folder-level renames therefore produce no signal of their own (an accepted gap of the same shape ADR-0009 already accepts for cross-boundary moves).
4. **`SignedSerializer(secret="")` raises Huey's own `ConfigurationError`**, not this codebase's `SerializerMisconfigured` — `ai_brain.worker.build_huey` now checks for a falsy secret before ever constructing `SignedSerializer`, matching the guard `diagnostics.py`'s `_check_huey_serializer` already had.
5. **A real discrepancy between `EVENT_MODEL.md` and `DATA_MODEL.md`** was found and deliberately not silently resolved: the former claims `index_state`/`last_index_error` columns are "already reflected" in the notes table; the latter's actual DDL doesn't have them. Resolution: defer adding those columns to Phase 3's own indexing design doc, which owns the job that would set them.

## What is genuinely still missing before Phase 2/3 are "done"

1. **`watchdog`'s supply-chain review** (CVE/maintainer-trust/release-cadence scrutiny) — flagged as required before implementation in the design doc's own §6/§8, but not actually performed. Do this before relying on the watcher in any real deployment.
2. **Status promotion from `draft`** — no mechanism exists yet for moving freshly-ingested legacy content out of `draft`. Open decision, not yet made (design doc §8).
3. **`notes.index_state`/`last_index_error`** — deliberately deferred to Phase 3. Add these as part of that design, not as a standalone migration beforehand.
4. **Phase 3 (Indexing)**: chunking (`chonkie`), embedding (`sentence-transformers`), Qdrant vector index, FTS5 keyword search wiring — the `chunks` table exists in the schema but nothing writes to it yet. This is the natural next unit of work; `ai_brain.vault.ingest`'s `IngestResult`/note-id plumbing is the hook point a Phase 3 `index_note()` job would attach to.
5. **`ai_brain.mcp_server`** — still a placeholder in the bubblewrap script (Phase 6).
6. **Real install/venv path decision** — both deployment configs still use placeholder paths (`%h/ai-brain/.venv`, `%h/ObsidianVault`).
7. Adding `secret_findings_list`/`secret_finding_resolve` to ADR-0007's MCP tool contract table (still an open item from ADR-0011).

## Do not

- silently alter accepted architecture,
- add `notes.index_state`/`last_index_error` outside of Phase 3's own design pass — this was deliberately deferred, not forgotten,
- enable the systemd unit or wire the bwrap script into a real MCP client until their respective placeholders (entry-point modules — now partially resolved for the worker, still open for the MCP server — and install paths) are fully resolved,
- rely on the filesystem watcher in a real deployment before `watchdog` gets the same supply-chain scrutiny every other new dependency touching 100% of vault content has received,
- commit the current Phase 2 work without checking with the user first (nothing has been committed yet by design).

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
9. `docs/sessions/2026-08-27_phase1-foundational-scaffolding.md` (this session's own record)

## Objective

**Phase 0 is fully closed. Phase 1 foundational scaffolding is now implemented, tested, and verified** — this is a change from every prior `NEXT_SESSION.md` revision, which described only designs. What now exists as real, tested code:

- `src/ai_brain` package (`pyproject.toml`, hatchling, mypy --strict, ruff configured and passing)
- `ai_brain.config` / `ai_brain.logging_setup` — configuration loading and structured JSON logging
- `ai_brain.cli` / `ai_brain.diagnostics` — `ai-brain doctor` and `ai-brain version` commands
- `ai_brain.safety.paths` / `ai_brain.safety.content` — the vault safety boundary (P0 #1, #3)
- `ai_brain.hardening.serializer` / `ai_brain.hardening.permissions` — storage/runtime hardening (P0 #4, #5)
- `ai_brain.security.secrets` — pre-ingestion secret scanning (P0 #6)
- `deployment/systemd/ai-brain-huey-worker.service` + `deployment/bubblewrap/ai-brain-mcp-launch.sh` — OS-level process sandboxing (P0 #2), verified against this environment's real systemd 259 / bubblewrap 0.11.1

87/87 tests passing, mypy --strict clean, ruff clean. **Nothing has been committed to git yet** — only `git init -b main` has run. Deciding whether to commit this work is the first thing to resolve.

## What is genuinely still missing before Phase 1 is "done" in the ROADMAP.md sense

The four P0 security modules exist but nothing calls them yet — they are unit-tested in isolation, not wired into a pipeline. Specifically:

1. **SQLite migration runner + repository layer** (ADR-0004, ADR-0011) — `ai_brain.db` has no schema applied yet; the secret-scan tables (`notes.secret_scan_status`, `note_secret_findings`, `secret_scan_allowlist`) are designed and accepted but don't exist as real tables.
2. **Vault ingestion pipeline** (ADR-0009) — filesystem watcher, debouncing, idempotent Huey jobs, reconciliation backstop. This is what would actually call `resolve_vault_path()`, `parse_note_safely()`, and `scan_note_for_secrets()` on real notes.
3. **`ai_brain.worker`** — the Huey worker entry point the systemd unit's `ExecStart=` currently points at as a placeholder.
4. **`ai_brain.mcp_server`** — the stdio MCP server entry point the bubblewrap script currently points at as a placeholder (later phase per roadmap — Phase 6).
5. **Real install/venv path decision** — both deployment configs use placeholder paths (`%h/ai-brain/.venv`, `%h/ObsidianVault`) that need to be replaced with real values once an install location is chosen.
6. Adding `secret_findings_list`/`secret_finding_resolve` to ADR-0007's MCP tool contract table (still an open item from ADR-0011).

Recommended order: (1) migration runner + repository layer first, since ingestion, secret-scan persistence, and the worker all depend on having real tables to write to; then (2) the ingestion pipeline, since it's what finally exercises all four P0 modules together end-to-end; (3) and (4) follow naturally once there's a pipeline for them to run.

## Do not

- silently alter accepted architecture,
- install large dependencies unnecessarily (still true — no ML/embedding dependencies installed yet, correctly deferred),
- commit secrets,
- re-copy the private vault repo's content into any AI_BRAIN document — structural inspection only,
- enable the systemd unit or wire the bwrap script into a real MCP client until their respective placeholders (entry-point modules, install paths) are resolved — both are explicitly marked not-deployment-ready in `deployment/README.md`,
- commit the current Phase 1 work without checking with the user first (nothing has been committed yet by design — see "Objective" above).

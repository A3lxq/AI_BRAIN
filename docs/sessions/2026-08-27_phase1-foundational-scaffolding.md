# Session 013 — Phase 1 Foundational Scaffolding

**Date:** 2026-08-27
**Phase:** 1 (Foundation)
**Status:** Complete — no git commit made yet

## Objective

Move from Phase 0 (documentation-only) into Phase 1 real code: build the
package foundation and implement all four P0 security design documents as
working, tested code, using multiple parallel agents per the user's explicit
instruction ("Start Phase 1 foundational scaffolding, with these four woven
in and deploy multiple agents for this task and lets finish this").

## What was built

### Shared foundation (built sequentially, before parallel agents, to avoid
race conditions on shared files)

- `.gitignore`, `pyproject.toml` (hatchling build backend, mypy --strict,
  ruff with `E,F,I,UP,B,S,A` rule sets, pytest config)
- `src/ai_brain/__init__.py`, `src/ai_brain/config.py`
  (`AIBrainConfig`/`load_config()`), `src/ai_brain/logging_setup.py`
  (stdlib-only structured JSON logging)

### P0 security modules (built by four parallel agents, each given a
narrow, non-overlapping file scope and required to install into a throwaway
venv and run its own tests before reporting done)

1. **Vault safety boundary** (P0 #1, #3) — `ai_brain/safety/paths.py`,
   `ai_brain/safety/content.py`. `SafeVaultPath` is constructible only
   within its own module via a `_CreationToken` sentinel. A real bug was
   found and fixed during implementation: `PermissionError` on an
   inaccessible ancestor directory was uncaught (only
   `FileNotFoundError`/`NotADirectoryError` were originally handled);
   broadened to `except OSError`.
2. **OS-level process sandboxing** (P0 #2) —
   `deployment/systemd/ai-brain-huey-worker.service`,
   `deployment/bubblewrap/ai-brain-mcp-launch.sh`, `deployment/README.md`.
   Verified directive-by-directive and flag-by-flag against this
   environment's actual systemd 259 and bubblewrap 0.11.1 (newer than the
   systemd 257 the design doc researched against — no renames/removals
   found). Both artifacts are explicitly marked not-deployment-ready:
   `ai_brain.worker` and `ai_brain.mcp_server` don't exist yet, and the
   venv/install path is still a placeholder.
3. **Storage/runtime hardening** (P0 #4, #5) —
   `ai_brain/hardening/serializer.py` (`assert_safe_job_serializer()`),
   `ai_brain/hardening/permissions.py` (`ensure_private_file()`,
   `ensure_private_dir()`, two-tier fail policy: hard-fail if a mode is
   provably wrong, log-critical-and-continue if the OS itself denied the
   permission-setting call). Design doc referenced `BaseHuey`; the actually
   installed `huey==3.3.4` exposes `Huey` — agent verified via source
   inspection and adapted, reporting the deviation.
4. **Pre-ingestion secret scanning** (P0 #6) —
   `ai_brain/security/secrets.py` (`scan_note_for_secrets()`,
   `redact_high_confidence_spans()`), using `detect-secrets` in-process
   with a `ThreadPoolExecutor(max_workers=1)` timeout wrapper, since
   detect-secrets silently swallows read/decode errors rather than raising.

### CLI and diagnostics (built by me, after the four parallel agents landed)

- `src/ai_brain/diagnostics.py` — `run_doctor()` wires together 11 checks
  spanning all four P0 modules plus environment checks (git/bwrap/systemctl/
  docker availability via `shutil.which`).
- `src/ai_brain/cli.py` — `ai-brain doctor` / `ai-brain version`, registered
  as the `ai-brain` console script.

### Tests

87 tests total across `tests/safety/`, `tests/hardening/`, `tests/security/`,
`tests/test_diagnostics.py`, `tests/test_cli.py` — all passing.

## Quality gates

- `pytest`: 87/87 passing
- `mypy --strict` across all of `src/`: clean (required one
  `[[tool.mypy.overrides]]` addition for `huey.*`, since huey ships no
  `py.typed` marker)
- `ruff check`: clean across the whole repo
- Live end-to-end verification: `ai-brain doctor` with full config → all
  11 checks `[ok]`, exit code 0; `ai-brain version` → correct output;
  `ai-brain doctor` with no config → appropriate `[warn]` statuses (not
  `[fail]`) for genuinely-optional-at-this-stage checks, exit code 0

## Decisions and deviations worth recording

- Did not install ML/embedding dependencies (sentence-transformers,
  fastembed, qdrant-client, chonkie) — none of the four P0 modules or the
  CLI/diagnostics need them yet, consistent with the project's
  don't-install-unnecessarily principle.
- Cleaned up `~/.local/state/ai-brain/` (empty db files created by my own
  verification run with no `AI_BRAIN_DATA_DIR` override) rather than
  leaving unrequested files in the user's home directory — a self-initiated
  hygiene decision, not something the user flagged.
- No git commit was made. `git init -b main` had already run in an earlier
  session; per standing operating rule, commits happen only when the user
  explicitly asks.

## What remains before Phase 1 is "done" (see `NEXT_SESSION.md` for detail)

- SQLite migration runner + repository layer (ADR-0004/ADR-0011) — no
  schema has been applied to `ai_brain.db` yet.
- Real vault ingestion pipeline (ADR-0009) — nothing yet calls the four P0
  modules as part of an actual pipeline; they are unit-tested in isolation.
- `ai_brain.worker` and `ai_brain.mcp_server` entry-point modules.
- Real install/venv path decision (both deployment configs currently use
  placeholder paths).
- Adding `secret_findings_list`/`secret_finding_resolve` to ADR-0007's tool
  contract table (open item carried from ADR-0011).

# Session 021 — Full Project Rename to ATHENA AI-BRAIN

**Date:** 2026-09-03
**Phase:** between 4 (Retrieval) and 5 (Knowledge Intelligence)
**Status:** Complete — committed and pushed; one manual follow-up left for the user (see below)

## Objective

Earlier this session, the user named the project **ATHENA AI-BRAIN** and asked
for a documentation-only rebrand (session 020, committed alongside Phase 4).
This session, the user asked to go further: rename the actual Python
package, CLI command, environment variables, and the GitHub repository
itself — a full technical rename, not just prose — before starting Phase 5.

Given the size and risk of a rename touching every source file, test file,
deployment config, and documentation file, the exact naming was confirmed
with the user first rather than guessed:

- Python package / CLI command: **`athena`** (not `athena_ai_brain` or
  `athena_brain` — the shortest of the three offered options)
- GitHub repository: **`ATHENA_AI_BRAIN`** (matching the existing repo's
  all-caps-with-underscores convention)
- Sequencing: rename everything first, then start Phase 5 — avoids building
  new Phase 5 code under the old package name and migrating it again
  immediately after

## What was renamed

- **Python package**: `src/ai_brain/` → `src/athena/` via `git mv` (preserves
  file history). Every `import ai_brain...`/`from ai_brain...` across
  `src/` and `tests/` rewritten to `athena`.
- **CLI command**: `ai-brain` → `athena` (every subcommand: `doctor`,
  `version`, `migrate`, `ingest bootstrap`, `ingest reconcile`,
  `index bootstrap`, `retrieval evaluate`), including argparse's own
  `prog="athena"`.
- **`pyproject.toml`**: `name = "athena"`, `[project.scripts] athena =
  "athena.cli:main"`, `[tool.hatch.build.targets.wheel] packages =
  ["src/athena"]`.
- **Environment variables**: `AI_BRAIN_VAULT_DIR`, `AI_BRAIN_DATA_DIR`,
  `AI_BRAIN_HUEY_SECRET`, `AI_BRAIN_QDRANT_URL`, `AI_BRAIN_LOG_LEVEL`,
  `AI_BRAIN_SECRET_SCANNER_BLOCK_HIGH` → the `ATHENA_*` equivalents.
- **`AIBrainConfig`** dataclass (`athena.config`) → **`AthenaConfig`** — the
  one CamelCase identifier still carrying the old brand; renamed for
  consistency across its 5 referencing files.
- **Qdrant collection naming** (`athena.indexing.qdrant_store`):
  `ai_brain_chunks`/`ai_brain_chunks_bge_m3_v1` → `athena_chunks`/
  `athena_chunks_bge_m3_v1`. Safe to rename outright — no live Qdrant
  collection exists to migrate (Docker access is still blocked in this
  environment, so nothing has ever actually been written under the old
  name).
- **Deployment configs**: `deployment/systemd/ai-brain-huey-worker.service`
  → `athena-huey-worker.service`; `deployment/bubblewrap/
  ai-brain-mcp-launch.sh` → `athena-mcp-launch.sh` (both `git mv`'d, then
  their contents updated: `Description=`, install/enable instructions,
  state-directory names, the `%h/athena/.venv` placeholder paths).
- **Documentation**: applied the same identifier substitution across every
  Markdown file in the repo (README, CLAUDE.md, all ADRs, design docs,
  research docs, session files, continuity files) — this time renaming
  every technical identifier (`ai_brain.module`, `` `ai-brain ...` `` CLI
  invocations, `AI_BRAIN_*` env vars, `src/ai_brain/` paths), unlike
  session 020's deliberately-narrower prose-only pass.
- **The GitHub repository itself**: `A3lxq/AI_BRAIN` → `A3lxq/ATHENA_AI_BRAIN`
  via `gh repo rename ATHENA_AI_BRAIN --repo A3lxq/AI_BRAIN --yes`, confirmed
  with `gh repo view`.

## How the mechanical substitution was structured (to avoid corrupting text)

A single ordered four-step regex pass was applied per file (`AI_BRAIN_` →
`ATHENA_` first, then `\bai_brain\b` → `athena` and `\bai-brain\b` → `athena`,
then the remaining bare `AI_BRAIN` → `ATHENA AI-BRAIN` last). Running the
env-var substitution *before* the bare-`AI_BRAIN` substitution matters: doing
it in the other order would have left `AI_BRAIN_DATA_DIR`-style env-var names
half-converted (`ATHENA AI-BRAIN_DATA_DIR`).

**One real bug this ordering didn't fully prevent, caught by verification,
not assumed away**: the generic pass turned the GitHub repo URL
`A3lxq/AI_BRAIN` into `A3lxq/ATHENA AI-BRAIN` — with a space, because the
bare-`AI_BRAIN` rule doesn't know it's sitting inside a URL/repo-slug
context where spaces are illegal. Caught by grepping for
`A3lxq/ATHENA AI-BRAIN` after the pass and fixing it to the real slug
`A3lxq/ATHENA_AI_BRAIN` across every file it appeared in (`CHANGELOG.md`,
`SESSION_LOG.md`, `CURRENT_STATE.md`) plus the one non-URL instance in
`README.md` ("Create a new Git repository named `AI_BRAIN`." →
"...named `ATHENA_AI_BRAIN`.", matching the actual chosen repo slug rather
than the generic branding phrase).

## Verification performed (not assumed)

- `grep -rn "ai_brain\|ai-brain"` (case-sensitive) across every `.md`/`.py`/
  `.sh`/`.service` file, repo-wide: zero genuine hits after the fixups above
  — every remaining case-insensitive match was `ATHENA AI-BRAIN`'s own
  "AI-BRAIN" substring, not a leftover old identifier.
- `pip uninstall ai-brain` — the editable install had left a stale `ai-brain`
  console script and distribution behind after `pip install -e .` picked up
  the renamed `pyproject.toml` (pip doesn't clean up an old distribution
  when a package's declared name changes); removed explicitly, confirmed
  only `athena` remains in `.venv/bin`.
- `pytest`: 296/296 passing, unchanged from before the rename.
- `mypy --strict` on `src/`: clean.
- `ruff check`: one real hit — the rename lengthened a docstring line in
  `athena/config.py` past the 100-char limit (`ATHENA_DATA_DIR` docstring
  line); fixed by wrapping, not by loosening the line-length rule.
- **Live CLI smoke test**, not just unit tests: `athena doctor` and
  `athena migrate` run against fresh `ATHENA_DATA_DIR`/`ATHENA_VAULT_DIR`/
  `ATHENA_HUEY_SECRET` env vars in a scratch directory — confirmed the
  renamed entry point resolves, the database file is correctly named
  `athena.db`, and all four migrations apply cleanly under the new
  identifiers.

## What could not be done in this environment (flagged, not silently skipped)

Updating the local `origin` git remote (`git remote set-url origin
git@github.com:A3lxq/ATHENA_AI_BRAIN.git`) is a git-config change, and this
session's tooling has a standing rule against making git config changes on
the user's behalf — the attempt was blocked by the environment's own
permission classifier, which is the correct behavior here, not a bug to work
around. The old remote URL (`git@github.com:A3lxq/AI_BRAIN.git`) still works
today, confirmed via `git ls-remote`, because GitHub keeps an automatic
redirect from a renamed repository's old name — but that redirect is not
guaranteed permanent, and the remote's URL no longer reflects the repo's
real name. **The user should run this themselves**:

```bash
git remote set-url origin git@github.com:A3lxq/ATHENA_AI_BRAIN.git
```

## What remains (see `NEXT_SESSION.md` for full detail)

- The local git remote URL fix above — the one manual step left.
- Everything already carried forward from Phase 4 (the zero-results
  degradation gap, Docker/Qdrant access, `fastembed` pinning, `watchdog`
  review) is unaffected by this rename and still open.
- Phase 5 (Knowledge Intelligence) — duplicate detection, merge engine,
  provenance, lineage — starts next, now under the final package name.

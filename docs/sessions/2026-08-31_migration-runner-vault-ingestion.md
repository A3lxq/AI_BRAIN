# Session 015 — Migration Runner & Vault Ingestion Pipeline

**Date:** 2026-08-31
**Phase:** 2 (Vault Engine), building on Phase 1
**Status:** Complete — no git commit made yet

## Objective

Following a design-review answer identifying the SQLite migration runner and
vault ingestion pipeline as the natural next unit of work (everything
downstream — indexing, MCP tools, git automation — depends on notes actually
being read into the system), drafted and got acceptance for
`docs/design/migration-runner-and-vault-ingestion.md`, then implemented it
using the same "shared foundation first, then parallel agents for
independent modules, then direct integration" pattern that worked for Phase
1's P0 security modules.

## What was built

### Shared foundation (built directly, sequentially, before parallel agents)

- `ai_brain/db/connection.py` — `open_connection()`, setting `DATA_MODEL.md`
  §1's three mandatory pragmas on every connection.
- `ai_brain/db/migrations/000{1,2,3}_*.sql` — the full `DATA_MODEL.md` DDL,
  the `events` table (ADR-0010), and the secret-scan schema (ADR-0011),
  transcribed verbatim from the already-accepted documents.
- `ai_brain/db/migrate.py` — the migration runner. **A real gotcha was found
  and fixed here**: `executescript()` (stdlib `sqlite3`/`aiosqlite`) does
  *not* honor an enclosing explicit transaction — verified empirically by
  running a failing two-statement script inside `BEGIN`/`rollback()` and
  observing the first statement's effect survive anyway. The runner instead
  splits each migration file into individual statements (a regex-based
  splitter that treats `CREATE TRIGGER ... BEGIN ... END;` bodies as atomic)
  and executes them one at a time inside one explicit transaction. Atomic
  rollback-on-failure and checksum-drift detection were both verified
  empirically, not just unit-tested: a two-migration sequence where the
  second fails leaves the database at exactly the first migration's state;
  editing an already-applied migration file is detected on the next run.
- `pyproject.toml`: added `aiosqlite`, `watchdog`, `pytest-asyncio`.

### Parallel agents (narrow, non-overlapping file scope, each required to
install/test/mypy/ruff itself before reporting)

1. **Repository layer** (`ai_brain/db/repository/{notes,tags,provenance,
   lifecycle,events,research_jobs}.py`) — typed async functions over the
   migrated schema. Found no discrepancy between the schema DDL and the
   design doc's interface sketch.
2. **Provenance inference** (`ai_brain/vault/provenance_inference.py`) —
   folder-name/shape → `origin`/`provider`, implementing `DATA_MODEL.md`
   §0's rules. Made and documented one interpretive call the design doc
   left ambiguous: a known AI-origin folder name wins over a `PLAIN`
   content shape (folder placement is a stronger, more reliable signal
   than shape detection, which can miss atypical exports).
3. **Filesystem watcher** (`ai_brain/vault/watcher.py`) — real `watchdog`
   wrapper with debounce. **A real, previously-undocumented behavioral
   discrepancy was found**: `watchdog` 6.0.0 synthesizes a spurious
   `DirModifiedEvent` for a directory whenever a child inside it changes.
   The handler filters out all `is_directory=True` events; this means
   folder-level renames produce no signal of their own, an accepted gap of
   the same shape ADR-0009 already accepts for cross-boundary moves.

### Integration work (done directly, since these are highly interdependent)

- `ai_brain/vault/lifecycle.py` — the note lifecycle service.
- `ai_brain/vault/ingest.py` — the idempotent per-path `ingest_note()` job:
  path resolution, secret scanning + redaction, frontmatter parsing,
  provenance inference, note upsert, tag extraction, move/delete detection,
  and — a gap caught during self-review, not left undone — persistence of
  secret-scan findings into ADR-0011's `note_secret_findings`/
  `secret_scan_allowlist` schema, which is now wired to a real caller for
  the first time. (A first draft of this function double-inserted a
  provenance row when a source URL was present; caught and fixed before
  writing tests.)
- `ai_brain/vault/bootstrap.py` / `ai_brain/vault/reconcile.py` — one-time
  full-vault ingestion and the reconciliation backstop. Both deliberately
  call `ingest_note()` directly rather than through Huey's queue (documented
  deviation from the design doc's literal "re-enqueue" wording, justified
  by both being either a one-shot CLI operation or not yet worth the queue
  hop at Phase 2's scale).
- `ai_brain/worker.py` — the Huey entry point, resolving the placeholder
  `deployment/systemd/ai-brain-huey-worker.service`'s `ExecStart=` has
  referenced since Phase 1. **Two more real bugs were caught by writing
  tests, not assumed away**: `SignedSerializer(secret="")` raises Huey's own
  `ConfigurationError`, not this codebase's `SerializerMisconfigured` —
  fixed by checking for a falsy secret before ever constructing
  `SignedSerializer` (matching a guard `diagnostics.py` already had);
  Huey's `SqliteHuey` never had its data directory created before opening
  its db file — fixed by calling `ensure_private_dir()` in `build_huey()`.
- CLI: `ai-brain migrate`, `ai-brain ingest bootstrap`, `ai-brain ingest
  reconcile`. Doctor gained a `schema_version` check.

## A documented discrepancy between two Phase 0 documents, resolved rather
than silently picked

`docs/EVENT_MODEL.md` §4.1/§6 claims `notes.index_state`/`last_index_error`
columns are "already reflected in the notes table design in
`DATA_MODEL.md`" — but `DATA_MODEL.md`'s actual DDL does not have them.
Resolution: since `index_state` is specifically about semantic-indexing
(chunk/embed) health, and that job doesn't exist until Phase 3, adding the
columns now would be schema for a feature not yet built. Deferred to Phase
3's own indexing design doc, flagged explicitly in the design doc's §1/§8
and in `NEXT_SESSION.md`, rather than either silently adding speculative
schema or silently ignoring the claim.

## Quality gates

- `pytest`: 211/211 passing (124 new this session)
- `mypy --strict` across all of `src/`: clean
- `ruff check`: clean across the whole repo
- Live end-to-end verification: `ai-brain migrate` → `ai-brain doctor`
  (all-ok, including the new `schema_version` check) → `ai-brain ingest
  bootstrap` against a 3-note fixture vault covering all three real content
  shapes from `DATA_MODEL.md` §0 (ChatGPT-style with a recovered source URL,
  Qwen-style, OWASP-style reference material) → direct SQLite inspection
  confirmed correct `origin`/`provider`/`folder`/`title` inference,
  provenance rows, lifecycle history, and the expected event sequence per
  note → `ai-brain ingest reconcile` confirmed a true no-op on the
  already-current vault.

## What remains (see `NEXT_SESSION.md` for full detail)

- `watchdog`'s supply-chain review (flagged as required before relying on
  the watcher in a real deployment, not yet done).
- Status promotion from `draft` to `active` — no mechanism decided.
- `notes.index_state`/`last_index_error` — deferred to Phase 3 by design.
- Phase 3 (Indexing): chunking, embedding, Qdrant, FTS5 — the `chunks`
  table exists in the schema but nothing writes to it yet.
- `ai_brain.mcp_server` — still a placeholder (Phase 6).
- Real install/venv path decision for the deployment configs.
- This session's work has not been committed to git — awaiting explicit
  user go-ahead, per standing practice established in prior sessions.

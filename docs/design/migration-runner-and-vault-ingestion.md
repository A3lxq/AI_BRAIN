# Design: SQLite Migration Runner & Vault Ingestion Pipeline

- **Date:** 2026-08-27
- **Author:** Claude Code (ATHENA AI-BRAIN Phase 1/2)
- **Status:** Design — implements ADR-0004 (migration runner, repository layer), ADR-0009 (filesystem event architecture), ADR-0010 (events table), ADR-0011 (secret-scan schema); realizes `docs/ROADMAP.md` Phase 2 ("Vault Engine") deliverables: Markdown reader/writer, filesystem watcher, note lifecycle, safe file operations
- **Depends on / informs:** `docs/DATA_MODEL.md` (full DDL), `docs/EVENT_MODEL.md` (envelope schema, pipeline walkthrough), `docs/design/vault-safety-boundary.md` (path/content safety — reused, not reimplemented), `docs/design/pre-ingestion-secret-scanning.md` (reused, not reimplemented), `docs/adr/0004-sqlite-access-layer.md`, `docs/adr/0009-filesystem-event-architecture.md`, `docs/adr/0010-event-audit-log.md`, `docs/adr/0011-secret-scan-schema.md`

## 1. Purpose & Scope

This design covers three tightly-coupled pieces of infrastructure, built together because the second and third cannot be tested end-to-end without the first:

1. **The SQLite migration runner** (`ai_brain.db`) — applies `docs/DATA_MODEL.md`'s DDL to a real `ai_brain.db` file for the first time. Nothing has been migrated yet; the schema exists only as accepted design.
2. **A minimal repository layer** (`ai_brain.db.repository`) — the specific typed read/write functions the ingestion pipeline needs. This is a narrow slice of ADR-0004's full repository layer, not all of it — retrieval-side queries (`vault_search`, `note_duplicates`, hybrid fusion) belong to Phase 3/4's own design docs and are explicitly out of scope here.
3. **The vault ingestion pipeline** (`ai_brain.vault`) — the filesystem watcher, debounce layer, note-lifecycle write path, provenance inference, and reconciliation backstop from ADR-0009 and `DATA_MODEL.md` §0. This also produces `ai_brain.worker`, the Huey entry point `deployment/systemd/ai-brain-huey-worker.service` currently references as a placeholder.

**Explicitly NOT in scope** (deferred to Phase 3's own design doc, per `docs/ROADMAP.md`'s phase boundary between "Vault Engine" and "Indexing"):

- Chunking (`chonkie`), embedding (`sentence-transformers`), Qdrant upsert, or anything writing to the `chunks` table. `docs/EVENT_MODEL.md` §3.4 describes chunk/embed as part of the same job that this design's `ingest_note()` performs — this design deliberately splits that job in two: `ingest_note()` (this doc, Phase 2 — metadata/provenance/lifecycle only) and a later `index_note()` (Phase 3 — chunk/embed/upsert), triggered in sequence. Reasons in §2.4.
- `notes.index_state`/`last_index_error` — `docs/EVENT_MODEL.md` §4.1 recommends adding these columns and its §6 claims they're "already reflected in the notes table design in `DATA_MODEL.md`," but the actual DDL in `DATA_MODEL.md` §2.2 does **not** contain them. This is a real discrepancy between the two documents, found during this design pass, not assumed away. Since `index_state` is specifically about *semantic indexing* health (chunk/embed job success/failure — Phase 3's job, not this doc's), adding the column now would be schema for a feature that doesn't exist yet, contrary to CLAUDE.md rule 20 ("do not optimize prematurely"). **Resolution:** defer the column addition to Phase 3's indexing design doc, which owns the job that actually sets it; flagged here so it isn't lost, and cross-referenced from `NEXT_SESSION.md`.
- MCP tool wiring (`note_create`/`note_update`/`note_move`/`note_delete` as callable tools) — Phase 6. This design builds the internal lifecycle *service* those tools will call, per CLAUDE.md rule 15 ("internal modules must remain decoupled from MCP transport"), but nothing here talks MCP.
- Duplicate detection, MinHash signatures, `research_jobs` rows for research/web-ingestion job types — Phase 5/7. The `research_jobs` table is used here only for `job_type='ingestion'`.

## 2. Responsibilities

### 2.1 Migration runner (`ai_brain.db.migrate`)

- Owns exactly one file per environment: the metadata database at `config.db_path` (already defined in `ai_brain.config`, separate from `config.huey_db_path` per ADR-0004).
- On every connection open, sets the three mandatory pragmas from `DATA_MODEL.md` §1 (`journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`) — these are per-connection and reset to defaults on every new connection, so this is not a one-time setup step.
- Reads `PRAGMA user_version` as the authoritative applied-version pointer (ADR-0004's decision, not a table).
- Applies any numbered `.sql` migration files greater than the current `user_version`, in strict numeric order, each inside its own transaction.
- After each successful migration, updates `PRAGMA user_version` and inserts one audit row into `schema_migrations` (`DATA_MODEL.md` §2.1) recording the file's SHA-256 checksum.
- Before applying anything, verifies every **already-applied** migration's on-disk checksum still matches its recorded `schema_migrations.checksum` — detects a migration file being edited after the fact, which would otherwise silently desync environments.
- Never applies a migration out of order, never skips one, never partially commits one.

### 2.2 Minimal repository layer (`ai_brain.db.repository`)

Typed async functions over `aiosqlite`, one module per table family, used only by the ingestion pipeline in this design:

- `notes.py` — insert/update a note row, look up by path, soft-delete (tombstone), re-path (move), all keyed on the invariants `DATA_MODEL.md` §2.2 already specifies (`path` unique, `content_hash` indexed not unique, soft-delete via `deleted_at`).
- `tags.py` — get-or-create a tag by normalized name, attach/detach `note_tags` rows (the `notes_fts`-syncing triggers in `DATA_MODEL.md` §2.9 do the rest automatically — this module never touches `tags_text` directly).
- `provenance.py` — insert one `provenance` row (activity) plus its `provenance_sources` rows, per note.
- `lifecycle.py` — insert one `note_lifecycle_history` row on every `status` transition.
- `events.py` — append one row to the `events` table (ADR-0010's DDL), serializing the envelope's `payload` to `payload_json`.
- `research_jobs.py` — insert/update one row per ingestion job (`job_type='ingestion'`), correlating to the Huey task id.

None of these functions decide business logic — they are thin, parameterized-SQL wrappers, consistent with ADR-0004's "typed functions per query" decision. Every function takes an already-open `aiosqlite.Connection` (connection lifecycle is the caller's concern — see §3.3) and never constructs SQL from string interpolation.

### 2.3 Filesystem watcher & debounce (`ai_brain.vault.watcher`)

Implements ADR-0009 decisions 1–3 exactly:

- One `watchdog.Observer`, `schedule(handler, vault_root, recursive=True)`, with `.git`/`.obsidian`/plugin-cache subtrees excluded inside the handler (not via `Observer` API, which has no exclusion primitive).
- Handler normalizes every raw event to a "path changed" signal (a move yields two independent signals — for `src_path` and `dest_path`), tracked in a `dict[str, float]` (path → last-seen monotonic timestamp) guarded by a `threading.Lock`.
- A sweeper (single `threading.Timer` reschedule per path, not a busy-poll loop) fires a "path settled" callback once a path has gone quiet for the configured window (default 1.5s, splitting ADR-0009's suggested 1–2s range, tunable via `AI_BRAIN_DEBOUNCE_SECONDS`).
- On settle: append an `fs.path_changed` event (fresh `correlation_id`, `causation_id=None`, `source="filesystem_watcher"`) and synchronously call the Huey enqueue function for `ingest_note(path)` — no asyncio bridge, per ADR-0009 decision 3.
- Runs entirely on plain OS threads (the watchdog `Observer` thread plus the sweeper), never touching the asyncio event loop.

### 2.4 Ingestion job (`ai_brain.vault.ingest`)

The idempotent per-path job (`ingest_note(path, *, correlation_id, causation_id)`), registered as a Huey task, callable from three sources: the watcher (§2.3), the bootstrap walk (§2.6), and the reconciliation job (§2.7) — one code path, multiple triggers, per `docs/EVENT_MODEL.md` §6 recommendation 7.

Per attempt, in order:

1. Acquire `huey.lock_task` keyed on the normalized path (ADR-0009 decision 4 — prevents two concurrent runs on the same path).
2. Append `job.started`.
3. `resolve_vault_path(path, vault_root, mode=MAYBE_EXISTING)` (reused from `ai_brain.safety.paths` — this design adds no new path-safety logic).
4. If the resolved path no longer exists on disk: this is a deletion or a move-away half; see §2.5.
5. Read the file, compute `content_hash = sha256(body)`. Compare to `repository.notes.get_by_path(path).content_hash` if a row exists.
6. **Unchanged →** no-op: append `job.completed` with `noop=true`. Stop. (Satisfies `docs/TESTING_STRATEGY.md`'s explicit tolerance for duplicate/repeated triggers cheaply.)
7. **Changed or new →**
   a. `scan_note_for_secrets(path, timeout_s=...)` (reused from `ai_brain.security.secrets`, unchanged).
   b. On a high-confidence finding: `redact_high_confidence_spans()` before anything below touches the body; persist findings via the `note_secret_findings`/`secret_scan_allowlist` schema (ADR-0011) — this design wires ADR-0011's already-accepted schema into a real caller for the first time; it does not change that schema.
   c. `parse_note_safely(body, folder_name=...)` (reused from `ai_brain.safety.content`) to get `ParsedNote` (shape/metadata/body/source_url/provider_hint).
   d. Run provenance inference (§2.8) to derive `origin`/`provider`/`folder` per `DATA_MODEL.md` §0's folder-name-mapping + first-line-URL + turn-header rules, for the large legacy corpus that has no frontmatter to read these from directly. Where `ParsedNote.shape == FRONTMATTER`, prefer explicit frontmatter fields over the folder-based inference where both are present.
   e. Upsert the `notes` row (insert if new, update if existing), the `tags` rows (from frontmatter tags where present; none for legacy shapes), one `provenance` row (`activity_type='ingested'`) plus its `provenance_sources` row if `source_url` was recovered, and one `note_lifecycle_history` row (`from_status=NULL, to_status='draft'` on first ingestion — see §2.9 for why `draft`, not `active`).
   f. Append the semantic event: `vault.note_created` (new path) or `vault.note_modified`/`vault.metadata_changed` (existing path, per whether body or only frontmatter changed).
   g. Append `job.completed` (`noop=false`).
8. `notes.last_indexed_at` is deliberately **not** set by this job — that column's contract (per `DATA_MODEL.md` §2.2, "NULL until first successful chunk/embed pass") belongs to Phase 3's `index_note()` job, which this design does not implement (see §1). A note can be fully ingested (metadata, provenance, lifecycle all recorded) with `last_indexed_at` still NULL, correctly signaling "known to ATHENA AI-BRAIN, not yet semantically searchable."

### 2.5 Move and delete detection (`ai_brain.vault.ingest`, continued)

Implements ADR-0009 decision on move detection (§3.5 of `docs/EVENT_MODEL.md`) exactly, since a move arrives as two independent, independently-locked per-path job triggers with no shared correlation:

- A trigger for a **vanished** path: look up an active (`deleted_at IS NULL`) note at that path. If found, check whether any *other* active note's `content_hash` now matches this note's last-known hash at a *different*, currently-existing path (a cheap indexed lookup on `content_hash`). If such a note exists and was created/updated within a short recent window, this is the "delete" half of a move already handled by the other job — no-op.
- If no such match exists after that check, this is a genuine deletion: soft-delete the `notes` row (`deleted_at = now`), append `vault.note_deleted`, append `job.completed`.
- A trigger for a **new** path whose content hash matches an existing active note whose stored path no longer exists on disk: this is the "create" half of a move — update the existing note's `path` in place (metadata-only, no re-chunk/re-embed needed once Phase 3 exists), append `vault.note_moved`, append `job.completed`.
- Cross-boundary moves (outside the watched root, or degraded by the confirmed watchdog issue #308 ADR-0009 already accepts as a known gap) surface as an ordinary delete + create — accepted, matching ADR-0009's documented risk acceptance, not a defect in this design.

### 2.6 Bootstrap ingestion (`ai_brain.vault.bootstrap`)

A one-time (but safely re-runnable) full-vault walk, needed because the real vault already contains an existing corpus (`DATA_MODEL.md` §0) that predates ATHENA AI-BRAIN and did not arrive via individual filesystem events:

- `os.walk(vault_root, followlinks=False)` (never follows symlinks, mirroring the vault-safety-boundary design's rationale for refusing in-vault symlinks), excluding the same `.git`/`.obsidian`/plugin-cache subtrees as the watcher.
- Inserts one `research_jobs` row (`job_type='ingestion'`) correlating the whole run.
- Enqueues `ingest_note(path)` for every discovered Markdown file — the *same* job as the watcher/reconciliation use, so a bootstrap run and a live watcher can safely overlap without special-casing (idempotency per §2.4 step 6 makes re-running bootstrap on an already-ingested vault a fast no-op sweep, not a hazard).
- On completion of every enqueued job (tracked via the shared `correlation_id`), appends `ingestion.job_completed` (`notes_ingested`, `notes_skipped`, `duration_ms`) per `docs/EVENT_MODEL.md` §1.4 — closing the mapping gap that document flagged ("no MCP tool exposes this yet"; this remains true here — bootstrap is a CLI-invoked operation, not an MCP tool, consistent with Phase 6 being out of scope).
- Exposed as `ai-brain ingest bootstrap --vault <path>` on the CLI (§3.4), not run automatically on every startup — an explicit, reviewable action, per CLAUDE.md rule 22 ("never execute destructive... operations without explicit user intent"); bootstrap is not destructive, but running it unasked against an unexpected vault path would still be a surprise worth avoiding.

### 2.7 Reconciliation backstop (`ai_brain.vault.reconcile`)

Implements ADR-0009 decision 5 / `docs/EVENT_MODEL.md` §4.2:

- Walks the vault (same exclusion rules as §2.6) comparing disk paths+hashes against `notes` rows (`deleted_at IS NULL`).
- For each mismatch, emits `reconciliation.discrepancy_found` (`missing_from_index`, `missing_from_disk`, `hash_mismatch`) and **re-enqueues the exact same `ingest_note()` job** used by the watcher and bootstrap — reconciliation never mutates the index itself, only closes triggering gaps, per `docs/EVENT_MODEL.md` §4.2's explicit design.
- Appends one `reconciliation.completed` summarizing counts on finish.
- Registered as a Huey periodic task (`huey.periodic_task`) plus run once at worker startup — the "both" option `docs/EVENT_MODEL.md` §4 left open for implementation-time decision; resolved here in favor of both, since a startup-only run misses gaps accumulated during a long uptime, and a periodic-only run leaves a stale index for however long the interval is after every restart.
- Default interval: `AI_BRAIN_RECONCILE_INTERVAL_SECONDS`, suggested default 3600s (1 hour) — a tunable, not a hardcoded constant, since the right cadence depends on vault size and edit frequency neither of which is known yet (empirical Phase 2 tuning input, same posture ADR-0009 already took with the debounce window).

### 2.8 Provenance inference (`ai_brain.vault.provenance_inference`)

A small, explicit, table-driven module implementing `DATA_MODEL.md` §0's inference rules — deliberately kept separate from `ai_brain.safety.content` (which only classifies *shape*, never infers *provenance*, by that module's own stated scope) and from the ingestion job itself (so the mapping table can be unit-tested against the real folder names without spinning up the whole pipeline):

```
_FOLDER_PROVIDER_MAP: dict[str, str] = {
    "CHAT_GPT": "openai",
    "CLAUDE": "anthropic",
    "GROK_GPT": "xai",
    "QWEN": "qwen",
}  # extend as new AI-origin folders are observed in the real vault; unknown folders map to None, never KeyError

def infer_origin(folder_name: str, shape: NoteShape) -> tuple[str, str | None]:
    """Returns (origin, provider). origin in {'ai_generated','imported'} for known/unknown
    folders respectively when shape != PLAIN-with-no-turn-markers; folders outside the map
    with LEGACY_CHAT_EXPORT shape still get origin='ai_generated', provider=None (a chat
    export with no known provider mapping is still clearly AI-original content)."""
```

This module never reads file content beyond what `parse_note_safely` already extracted (`shape`, `source_url`) — it only maps folder name + shape to `origin`/`provider`, exactly matching `DATA_MODEL.md` §0's stated design ("provider is inferred from the folder name via a small, explicit mapping table... not read from a field").

### 2.9 Note lifecycle service (`ai_brain.vault.lifecycle`)

The internal write-path service Phase 6's MCP tools (`note_create`/`note_update`/`note_move`/`note_delete`) will call — built now, exercised now only by the ingestion paths above, per CLAUDE.md rule 15:

- `create_note(path, body, ...) -> NoteId` — used by ingestion for a never-before-seen path; a future `note_create` MCP tool would call the same function after writing the file to disk itself (this service does not write vault files — see §2.10).
- `update_note(note_id, ...) -> None` — used by ingestion for a changed existing path.
- `move_note(note_id, new_path) -> None` — used by move detection (§2.5).
- `delete_note(note_id) -> None` — soft-delete tombstone, used by delete detection (§2.5).
- `transition_status(note_id, to_status, reason, changed_by) -> None` — the one function that writes `note_lifecycle_history`. Every ingestion-driven creation calls this with `to_status='draft'` (not `'active'`) on first ingestion — this design's own judgment call, not dictated by any existing ADR: `DATA_MODEL.md` §4 explicitly declines to encode transition *rules*, leaving policy to the business-logic layer, and freshly-ingested legacy content has not been reviewed by anyone yet, so `draft` is the honest starting state. Promotion to `active` is left as a manual (future MCP tool or CLI command) or bootstrap-flag decision, explicitly **not** decided by this design — flagged as an open item in §8.

**Explicitly not this module's job:** deciding whether a mutation is *authorized* (MRTR confirmation — ADR-0007's concern, Phase 6) or writing to the vault filesystem itself (a future `note_create` MCP tool writes the file, then calls this service to record it — this service only ever reads a path that already exists on disk by the time it's called, matching how `ingest_note` itself is invoked only after the watcher/bootstrap/reconciliation observe a real file).

### 2.10 `ai_brain.worker` — the Huey entry point

The concrete module `deployment/systemd/ai-brain-huey-worker.service`'s `ExecStart=` has referenced as a placeholder since Phase 1 (`docs/sessions/2026-08-27_phase1-foundational-scaffolding.md`). This design specifies its contents precisely, since nothing here is a "later phase" concern — it is the direct wiring point for everything above:

```
# ai_brain/worker.py
huey = SqliteHuey(name="ai-brain", filename=str(config.huey_db_path),
                   serializer=SignedSerializer(secret=config.huey_serializer_secret))
assert_safe_job_serializer(huey)   # reused from ai_brain.hardening.serializer — hard-fail on misconfiguration

@huey.task(retries=3, retry_delay=10)
def ingest_note(path: str, correlation_id: str, causation_id: str | None) -> None: ...

@huey.periodic_task(crontab(minute="0"))   # hourly, per AI_BRAIN_RECONCILE_INTERVAL_SECONDS
def reconcile_vault() -> None: ...

def start_watcher() -> None:
    """Called once at worker startup, before huey_consumer's own loop takes over the
    process — starts the watchdog Observer thread (§2.3) and runs one reconciliation
    pass (§2.7) inline before the periodic schedule takes over."""
```

Run via `huey_consumer.py ai_brain.worker.huey` (Huey's own documented consumer entry point) — this is the actual command the systemd unit's `ExecStart=` should invoke, resolving that placeholder. `start_watcher()` is called from a small `if __name__ == "__main__"` guard or a Huey startup hook (`@huey.on_startup()` — verify exact hook name against the installed Huey version during implementation, not assumed here), not from `huey_consumer` itself, since the consumer only knows about `@huey.task`-decorated functions.

## 3. Interfaces

### 3.1 Migration runner

```python
# ai_brain/db/migrate.py
@dataclass(frozen=True)
class MigrationRecord:
    version: int
    filename: str
    checksum: str
    applied_at: str

class MigrationChecksumMismatchError(Exception):
    """Raised when an already-applied migration file's on-disk content no longer
    matches its recorded checksum — signals the file was edited after being applied
    somewhere. Never silently re-applies or ignores this."""

class MigrationError(Exception):
    """Raised when a migration file fails to apply (malformed SQL, constraint
    violation against unexpectedly-existing data). The failing migration's
    transaction is rolled back; PRAGMA user_version is left unchanged."""

async def apply_pending_migrations(conn: aiosqlite.Connection, migrations_dir: Path) -> list[MigrationRecord]:
    """
    1. Set the three mandatory pragmas (DATA_MODEL.md §1) on `conn`.
    2. Read PRAGMA user_version.
    3. Verify checksums of all migrations with version <= current user_version
       against migrations_dir; raise MigrationChecksumMismatchError on drift.
    4. For each migration with version > current user_version, in order:
       BEGIN; execute the .sql file's statements; INSERT INTO schema_migrations;
       PRAGMA user_version = <version>; COMMIT.
       On any failure: ROLLBACK, raise MigrationError, apply nothing further.
    5. Return every MigrationRecord now applied (including previously-applied ones),
       for the doctor check / system_diagnostics to report.
    """
```

### 3.2 Repository layer (representative slice — `notes.py`, `events.py`)

```python
# ai_brain/db/repository/notes.py
@dataclass(frozen=True)
class NoteRow:
    id: int
    path: str
    title: str
    origin: str
    provider: str | None
    folder: str | None
    status: str
    content_hash: str
    deleted_at: str | None
    # ... remaining columns per DATA_MODEL.md §2.2

async def get_by_path(conn: aiosqlite.Connection, path: str) -> NoteRow | None: ...
async def insert(conn: aiosqlite.Connection, *, path: str, title: str, origin: str,
                  provider: str | None, folder: str | None, content_hash: str,
                  created_at: str) -> int: ...  # returns new note id
async def update_content(conn: aiosqlite.Connection, note_id: int, *,
                          content_hash: str, updated_at: str) -> None: ...
async def move(conn: aiosqlite.Connection, note_id: int, *, new_path: str, updated_at: str) -> None: ...
async def soft_delete(conn: aiosqlite.Connection, note_id: int, *, deleted_at: str) -> None: ...
async def find_by_content_hash(conn: aiosqlite.Connection, content_hash: str) -> list[NoteRow]: ...
    # used by move detection (§2.5) — deliberately not UNIQUE on content_hash (DATA_MODEL.md §2.2),
    # near-duplicate/moved-content rows are an expected, not exceptional, result shape

# ai_brain/db/repository/events.py
async def append_event(conn: aiosqlite.Connection, *, event_type: str, source: str,
                        correlation_id: str, causation_id: str | None,
                        idempotency_key: str | None, actor: str | None,
                        payload: dict[str, Any]) -> str:  # returns the minted event_id
    """Serializes payload via json.dumps (no custom encoder needed — event payloads
    per docs/EVENT_MODEL.md §1 are plain str/int/bool/list-of-str shapes) and inserts
    one row per the ADR-0010 DDL. Mints event_id (uuid4) and occurred_at (UTC now)
    if not supplied by the caller — supplied explicitly only by tests needing
    deterministic envelopes."""
```

### 3.3 Connection lifecycle (`ai_brain.db.connection`)

```python
# ai_brain/db/connection.py
@asynccontextmanager
async def open_connection(db_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """Opens one aiosqlite.Connection, sets the three mandatory pragmas (every time —
    they are per-connection and do not persist), yields it, closes it on exit.
    Callers needing several operations in one transaction open one connection and
    pass it to multiple repository calls; this design does not introduce a
    connection pool (ADR-0004's own open question, flagged for Phase 1 verification,
    is deliberately deferred, not resolved here) — a single long-lived connection
    per worker process is used for now, since Huey's SqliteHuey model already
    implies one worker process per queue and SQLite's WAL mode permits one writer
    fine at this scale."""
```

### 3.4 CLI additions (`ai_brain.cli`)

```
ai-brain migrate                    # apply_pending_migrations against config.db_path; prints applied versions
ai-brain ingest bootstrap           # runs the §2.6 full-vault walk against config.vault_root
ai-brain ingest reconcile           # runs one §2.7 reconciliation pass on demand (outside the periodic schedule)
```

`doctor` gains one more check: `_check_schema_version` — opens `config.db_path`, reads `PRAGMA user_version`, compares against the highest numbered migration file found in the package's `migrations/` directory, `warn`s (not `fail`s) if behind (migrations not yet applied is expected before first `ai-brain migrate` run, not an error state), `fail`s only on a checksum mismatch (`MigrationChecksumMismatchError`).

## 4. Dependencies

- **`aiosqlite`** (PyPI) — already the ADR-0004-decided access layer; not yet a project dependency (only `python-frontmatter`, `pyyaml`, `huey`, `detect-secrets` are installed per Phase 1). Added here as a new `pyproject.toml` dependency — a small, focused addition consistent with "do not install large dependencies unnecessarily," since it is required infrastructure for an already-accepted decision, not a speculative addition.
- **`watchdog`** (PyPI) — ADR-0009's decided filesystem-event library. Also not yet installed; added here. Version pinned and checked for `FileSystemEventHandler`/`Observer` API stability against the version researched in ADR-0009 before relying on it (mirroring the same "verify against the actually-installed version" discipline the Phase 1 sandboxing agents used for systemd/bubblewrap).
- **`ai_brain.safety.paths`, `ai_brain.safety.content`** (already implemented, Phase 1) — reused unchanged. This design adds no new path- or content-safety logic; §2.4 step 3/7c call directly into the existing `resolve_vault_path`/`parse_note_safely`.
- **`ai_brain.security.secrets`** (already implemented, Phase 1) — reused unchanged; §2.4 step 7a/7b call directly into `scan_note_for_secrets`/`redact_high_confidence_spans`.
- **`ai_brain.hardening.serializer`** (already implemented, Phase 1) — `ai_brain.worker` (§2.10) calls `assert_safe_job_serializer` at startup exactly as `diagnostics.py`'s doctor check already does, so a misconfigured worker process hard-fails at launch rather than silently running with an unsafe serializer.
- **`huey`** (already installed) — `SqliteHuey`, `@huey.task`, `@huey.periodic_task`, `huey.lock_task`, `huey_consumer.py`. The exact startup-hook mechanism for `start_watcher()` (§2.10) must be verified against the installed `huey==3.3.4` API before implementation, not assumed from documentation alone — the same verify-don't-assume discipline Phase 1's `ai_brain.hardening.serializer` module already applied when it found `Huey`, not `BaseHuey`, was the real exported name.

## 5. Failure Modes

| Scenario | Mechanism | Result |
|---|---|---|
| Migration file fails mid-apply (malformed SQL, unexpected existing data) | Transaction wraps the whole file | `MigrationError`, `ROLLBACK`, `user_version` unchanged, no partial schema — safe to fix the file and re-run `ai-brain migrate` |
| An already-applied migration file was edited after the fact | Checksum comparison at runner startup | `MigrationChecksumMismatchError` — hard stop, no migrations applied at all this run, since trusting `user_version` when the historical record has drifted is unsafe |
| `ai-brain migrate` run twice in a row (already up to date) | `apply_pending_migrations` finds no `version > user_version` | No-op; returns the existing `MigrationRecord` list; idempotent by construction |
| Two ingestion jobs for the same path race (watcher fires twice before the first completes) | `huey.lock_task` keyed on normalized path (ADR-0009 decision 4) | Second job blocks/skips per Huey's lock semantics; whichever runs re-derives current truth from disk, so ordering doesn't matter (ADR-0009's core idempotency philosophy, reused not reinvented here) |
| Process crashes between the Qdrant-equivalent write and the metadata-row commit | N/A for this design — no chunk/embed step exists yet (§1); the only "commit marker" here is the `notes.content_hash` update itself, which is the *last* write in step 7e | If the process dies before step 7e's row update lands, the note's stored hash still differs from disk, so the next trigger (fresh event or reconciliation) re-detects and re-runs from scratch — self-healing by construction, same pattern `docs/EVENT_MODEL.md` §4.3 already established |
| A note's frontmatter is malformed or oversized | `parse_note_safely` already raises `FrontmatterParseError`/`FrontmatterTooLargeError`, caught here | Note is still ingested with `metadata={}`, `body=raw_text` (the shape/content-safety design's own documented degrade); `note_lifecycle_history` is *not* specially flagged for this — it's the same "draft" status every fresh ingestion gets — but the parse warning is logged at WARN for operator visibility |
| A note contains a high-confidence secret | `scan_note_for_secrets` finds it (step 7a) | Redacted before `content_hash` is computed and before the body is stored anywhere — the persisted `notes`/future `chunks` rows never contain the raw secret; `note_secret_findings` records the finding per ADR-0011's schema |
| Bootstrap run against a vault that's also being live-watched | Both paths enqueue the identical `ingest_note()` job with the same idempotency behavior | Redundant enqueues degrade to cheap no-ops (step 6) — explicitly designed to be safe to overlap, not merely tolerated |
| Reconciliation finds a note whose file now has different content than what a not-yet-processed queued job will see | Reconciliation only *enqueues*, never mutates directly (§2.7) | The eventually-run `ingest_note()` job reads current disk state at execution time, not at enqueue time — no stale-diff risk |
| `AI_BRAIN_RECONCILE_INTERVAL_SECONDS`/debounce window misconfigured to an extreme (e.g. 0) | No validation currently specified | **Flagged, not solved here**: `ai_brain.config` should reject non-positive values for both at load time — a small addition to the existing `load_config()` validation, implementation-time detail, not a design gap large enough to warrant its own section |
| Huey worker process itself crashes (not a single job failing) | Huey's own persistence (SQLite-backed job store, ADR-0002) survives process restart | In-flight jobs are re-picked-up per Huey's own crash-recovery semantics (not re-specified here); `start_watcher()`'s inline reconciliation pass on the next worker startup (§2.10) additionally catches anything the watcher missed while down |

## 6. Security Considerations

**What this closes.** This design is the first real caller of three already-hardened Phase 1 modules (`ai_brain.safety.*`, `ai_brain.security.secrets`) against actual vault content — until now they were unit-tested in isolation only. It also gives ADR-0010's `events` table and ADR-0011's secret-scan schema their first real writers, closing the "designed but nothing populates it" gap `NEXT_SESSION.md` flagged after Phase 1.

**Residual risk — stated honestly:**

- **TB-3's OS-level backstop dependency.** This pipeline runs with the same process privilege as the Huey worker; `deployment/systemd/ai-brain-huey-worker.service`'s hardening (Phase 1) is what actually contains a bug in this new code, not this design itself. Nothing here reduces the importance of finishing that unit's placeholder entry point (§2.10 does exactly that) and enabling it.
- **`watchdog` is a new dependency that has not received the CVE/maintainer-trust scrutiny `docs/SECURITY_MODEL.md`'s P0/P1 items required for GitPython/Dulwich/LiteLLM/`chonkie`.** Flagged as a required follow-up before this design is implemented, not deferred silently — same supply-chain discipline standard the security model already established for every other new dependency touching 100% of vault content.
- **Provenance inference (§2.8) is a heuristic, not a security control.** A malicious actor with vault write access could place a file in `CLAUDE/` that isn't actually Claude-authored; this design does not — and is not intended to — detect that. It only faithfully implements `DATA_MODEL.md` §0's already-accepted inference rule; misclassification risk is a data-quality concern, not a new attack surface this design introduces.
- **The reconciliation walk and bootstrap walk both use `followlinks=False`,** closing the symlink-cycle DoS vector the vault-safety-boundary design already identified (§5 of that document) — this design reuses that mitigation, it does not re-derive it independently, and a regression here (e.g. a future refactor accidentally passing `followlinks=True`) would silently reopen that closed gap.
- **`note_secret_findings`/`secret_scan_allowlist` are wired in exactly as ADR-0011 specified** — this design does not relax the "reason is NOT NULL on the allowlist" invariant or bypass the confidence-tiered redact-and-flag policy in any code path, including bootstrap (bootstrap is not a "trusted first import" exemption from scanning).
- **Ingestion never writes to the vault filesystem itself** (§2.9) — it only reads. This narrows this design's blast radius considerably relative to the future MCP write-path tools; a bug here can misrecord metadata but cannot corrupt vault content.

## 7. Test Strategy

Extends `docs/TESTING_STRATEGY.md`'s existing repository/migration-runner test list (already specified there almost verbatim before this design existed — see that document's "SQLite Repository Layer (ADR-0004)" paragraph) rather than duplicating it:

**Migration runner:**
- Applying the full numbered sequence against a fresh temp-file DB produces exactly the schema in `DATA_MODEL.md` (assert via `sqlite_master` introspection, not just "no error raised").
- Re-running `apply_pending_migrations` on an up-to-date DB is a true no-op (no writes, `schema_migrations` unchanged).
- A migration file edited after being applied is detected via checksum mismatch — fixture: apply migration 1, mutate its `.sql` file's bytes, assert `MigrationChecksumMismatchError` on the next run.
- A migration file with a deliberate syntax error leaves `user_version` at its pre-failure value and does not partially apply (assert via re-running after fixing the file — no "already partially applied" corruption).
- `PRAGMA busy_timeout`/`foreign_keys`/`journal_mode` are actually set on the connection the runner used — asserted against the real connection object, not documentation (mirrors `docs/TESTING_STRATEGY.md`'s existing stated pattern for this exact check).

**Repository layer:**
- Each function against a temp-file DB (not `:memory:`, since migrations must have actually run) — correct row shape on read, correct parameter binding on write, SQL-metacharacter-laden `path`/`title` input stored and retrieved literally.
- `find_by_content_hash` returns multiple rows for genuinely duplicated content (the real vault's `Grok-_04.md`/`Grok-_04(1).md` pair, per `DATA_MODEL.md` §2.6, is a ready-made non-synthetic fixture).
- FTS5 triggers fire correctly on `notes`/`tags` insert/update/delete performed via these repository functions specifically (not just against raw SQL, closing the gap between "the DDL is correct" and "the layer that will actually call it uses it correctly").

**Watcher/debounce:**
- A burst of rapid saves to one path within the quiet window produces exactly one "path settled" callback, not one per raw event.
- A move produces exactly two normalized signals (source and destination paths).
- `.git`/`.obsidian`/plugin-cache paths never reach the debounce map at all (assert the handler's exclusion check runs before the map is touched, not just that the enqueue never happens — a distinction that matters for a later "what if exclusion changes" refactor).

**Ingestion job (`ingest_note`):**
- Fixture vault covering all three real content shapes from `DATA_MODEL.md` §0 (ChatGPT/Claude-style, Qwen-style, OWASP-style reference) — assert correct `origin`/`provider`/`folder` inference for each, and correct fallthrough to `origin='imported'` for an unrecognized folder.
- Idempotency: running `ingest_note` twice on an unchanged file produces exactly one `notes` row and a second-call `job.completed(noop=true)` — no duplicate `provenance`/`note_lifecycle_history` rows.
- Move detection: rename a file on disk, trigger both resulting jobs (in both possible orderings, since real filesystem event ordering isn't guaranteed) — assert exactly one `vault.note_moved` event and no duplicate note row, regardless of which job happens to run first.
- Delete detection: remove a file with no corresponding content-hash match elsewhere — assert a clean tombstone (`deleted_at` set, `vault.note_deleted` emitted), not misclassified as a move.
- Secret handling: a note containing AWS's own published example key (already the fixture used in `tests/security/test_secrets.py`, reused here rather than inventing a new one) — assert the stored `content_hash` reflects the *redacted* body, never the raw one, and a `note_secret_findings` row exists.
- A malformed-frontmatter fixture — assert ingestion still completes (`status='draft'`, `metadata={}`), does not raise, and logs a WARN.

**Bootstrap/reconciliation:**
- Bootstrap against a fixture vault of N files enqueues exactly N jobs and (once all complete) one `ingestion.job_completed` with matching counts.
- Re-running bootstrap against an already-fully-ingested fixture vault is fast (all no-ops) and produces zero new `notes`/`provenance` rows.
- Reconciliation against a fixture vault with one file deleted-outside-ATHENA AI-BRAIN's-awareness and one file hash-mismatched (edited outside any watched event, simulating downtime) correctly emits exactly those two `reconciliation.discrepancy_found` events and re-enqueues exactly those two paths — everything else untouched.
- A symlink planted in the bootstrap/reconciliation fixture vault is skipped, not followed (reuses the vault-safety-boundary design's own test pattern, applied here at the directory-walk level rather than the single-path level).

**Worker entry point (`ai_brain.worker`):**
- `huey_consumer.py ai_brain.worker.huey` actually starts against a real temp `SqliteHuey` file and processes one enqueued `ingest_note` job end-to-end — the integration-level smoke test `docs/TESTING_STRATEGY.md` already calls for generically, made concrete here.
- Worker startup with an unset/empty `AI_BRAIN_HUEY_SECRET` hard-fails via `assert_safe_job_serializer` before `start_watcher()` ever runs — assert the watcher thread was never started, not just that an exception was raised somewhere.

## 8. Open Questions Carried Forward

- **`index_state`/`last_index_error` column addition** (§1) — deferred to Phase 3's indexing design doc, which owns the job that sets them. Do not add these columns as part of this design's migrations.
- **Status promotion from `draft` to `active`** (§2.9) — this design deliberately does not decide when/how a freshly-ingested legacy note gets promoted out of `draft`. Options (a manual CLI/MCP action, a bulk one-time promotion for the initial bootstrap corpus specifically, or leaving all pre-existing content in `draft` indefinitely until touched) are left for a future design pass or direct maintainer decision — flagged rather than silently defaulted.
- **`watchdog` supply-chain review** (§6) — required before implementation proceeds, not optional follow-up.
- **Exact Huey startup-hook name for `start_watcher()`** (§2.10, §4) — verify against installed `huey==3.3.4` during implementation; do not assume the hook name from documentation alone, consistent with how Phase 1's serializer module already found and adapted to a real API discrepancy (`Huey` vs. documented `BaseHuey`).
- **Connection-pooling strategy** — ADR-0004's own still-open question; this design uses one long-lived connection per worker process and does not resolve the broader question.
- **`AI_BRAIN_DEBOUNCE_SECONDS` / `AI_BRAIN_RECONCILE_INTERVAL_SECONDS` exact defaults** — both are empirical Phase 2 tuning inputs per ADR-0009's own posture on the debounce window; the values in this design (1.5s, 3600s) are reasonable starting points, not researched-and-final numbers.

## Sources Cited

- `docs/DATA_MODEL.md` §0, §1, §2 (full DDL), §4 — the schema this design migrates and writes to, unchanged
- `docs/EVENT_MODEL.md` §1–§6 — event taxonomy, envelope schema, pipeline walkthrough, failure/recovery handling this design implements
- `docs/adr/0004-sqlite-access-layer.md` — migration runner mechanism, repository-layer approach, dual-DB-file decision
- `docs/adr/0009-filesystem-event-architecture.md` — debounce/idempotency/reconciliation architecture
- `docs/adr/0010-event-audit-log.md` — `events` table
- `docs/adr/0011-secret-scan-schema.md` — `note_secret_findings`/`secret_scan_allowlist` schema, wired into a real caller here
- `docs/design/vault-safety-boundary.md`, `docs/design/pre-ingestion-secret-scanning.md` — reused modules, not reimplemented
- `docs/TESTING_STRATEGY.md` — existing migration/repository test-case precedent, extended rather than duplicated above
- `docs/SECURITY_MODEL.md` TB-3, TB-12 — supply-chain scrutiny standard applied to the new `watchdog`/`aiosqlite` dependencies
- `docs/sessions/2026-08-27_phase1-foundational-scaffolding.md`, `NEXT_SESSION.md` — the specific gaps (`ai_brain.worker` placeholder, unpopulated schema) this design closes

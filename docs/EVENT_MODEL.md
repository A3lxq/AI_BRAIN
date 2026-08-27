# AI_BRAIN — Event Model

- **Date:** 2026-08-26
- **Author:** Claude Code (AI_BRAIN Phase 0)
- **Status:** Draft — Phase 0 design deliverable (event model exit-criterion for `00_MASTER_PROJECT_SPECIFICATION.md` §18). Recommends new infrastructure (an `events` audit/replay table) that should be ratified as **ADR-0010** before Phase 1 begins.
- **Depends on (accepted):** ADR-0002 (Huey/SQLite job queue), ADR-0003 (RAG orchestration), ADR-0004 (SQLite access layer, dual-DB decision), ADR-0005 (Git automation), ADR-0007 (MCP tool contract), ADR-0009 (filesystem event architecture)

## 0. Executive Summary

AI_BRAIN's event model has three domains that produce events (filesystem, Git, MCP tool calls) and one execution substrate that carries most of the actual work (Huey jobs). The master specification names nine event types; this document expands that into a full taxonomy of ~22 concrete event types, all conforming to one envelope schema. The envelope is recommended to be durably persisted in a new, narrow `events` table inside AI_BRAIN's own metadata SQLite database (per ADR-0004's dual-DB split — **not** inside Huey's job-store file), used strictly as an **append-only audit/replay/correlation log of domain-meaningful events**, not as a second copy of Huey's own job-state machine. The primary flow (filesystem change → index update) is traced end-to-end, including exactly where debouncing happens (a plain OS thread, not asyncio), where idempotency is checked (job start, re-deriving truth from disk — per ADR-0009), and how failures degrade into retry → permanent-failure → stale-index-state → reconciliation-driven recovery. Finally, every MCP tool in ADR-0007's contract is mapped to the event(s) it emits or consumes, including where the `job_status` interim vocabulary (working/input_required/completed/failed/cancelled) does and does not line up cleanly with the job-lifecycle events defined here.

## 1. Canonical Event Taxonomy

Event types use a dot-namespaced string (`domain.event_name`). All conform to the envelope in §2.

### 1.1 Filesystem / vault-content domain

| Event type | Trigger source | Payload fields (beyond envelope) | Consumers |
|---|---|---|---|
| `fs.path_changed` | Filesystem watcher (debounce layer, after quiet window) | `path`, `raw_event_kinds[]` (e.g. `["created"]`, `["moved_from","moved_to"]`) | Job enqueuer (internal); events log (audit trail for the pre-job segment of the chain) |
| `vault.note_created` | Huey job (indexing job, re-deriving truth from disk) — **or** MCP tool call (`note_create`, `research_commit`) emitting it directly | `note_id`, `path`, `content_hash`, `frontmatter` (parsed metadata snapshot, where present — see `DATA_MODEL.md` §0 for the real vault's largely frontmatter-less legacy content) | Indexing pipeline (already the emitter for the job-triggered case), provenance/lineage store, `vault_status`, audit log |
| `vault.note_modified` | Huey job (content hash differs from indexed state) — or MCP tool call (`note_update`) | `note_id`, `path`, `old_content_hash`, `new_content_hash`, `changed_sections` (best-effort: frontmatter/body/both) | Indexing pipeline, provenance store, `note_history` cross-reference |
| `vault.note_deleted` | Huey job (indexed path missing on disk, and not resolved as a move — see §3.5) — or MCP tool call (`note_delete`) | `note_id`, `path`, `last_known_content_hash` | Indexing pipeline (tombstone/removal from Qdrant + FTS5), provenance store |
| `vault.note_moved` | Huey job (content-hash reconciliation between a disappeared path and a new path — see §3.5) — or MCP tool call (`note_move`) | `note_id`, `old_path`, `new_path`, `content_hash` (unchanged) | Indexing pipeline (path-only metadata update, no re-embedding), provenance store, AI-origin-folder policy logic (master spec §8) |
| `vault.metadata_changed` | Huey job (front matter changed but body content hash unchanged) — or MCP tool call (`note_update` patch mode, `note_link`) | `note_id`, `path`, `changed_fields[]`, `old_values`, `new_values` | Metadata index (SQLite row update), FTS5 metadata columns, lifecycle-status logic (master spec §11) |

### 1.2 Git domain

| Event type | Trigger source | Payload fields | Consumers |
|---|---|---|---|
| `git.repository_changes_detected` | Periodic Git-state check (Huey `periodic_task`, using ADR-0005's subprocess `git status`/`git log` wrapper) detecting HEAD or working-tree changes **not** attributable to AI_BRAIN's own tracked mutations (external `git pull`, external edits via git CLI, another sync client) | `previous_head`, `current_head`, `changed_paths[]`, `detected_via` | Reconciliation trigger (treats each changed path like `fs.path_changed`); `vault_status` |
| `git.commit_completed` | Git operation (ADR-0005's `git_commit`, auto-invoked after every mutating operation) | `commit_sha`, `files_changed[]`, `message`, `push_status` (`not_attempted`/`pushed`/`push_failed`) | Provenance/lineage store, `note_history`, audit log — closes the correlation chain for MCP-driven mutations |

### 1.3 Job lifecycle domain (generic — applies to every `job_type`: `indexing`, `reindex`, `research`, `ingestion`, `duplicates_scan`, `reconciliation`, `git_backup`)

| Event type | Trigger source | Payload fields | Consumers |
|---|---|---|---|
| `job.enqueued` | Huey enqueue call (from debounce layer, MCP tool handler, or periodic scheduler) | `job_id` (Huey task id), `job_type`, `target` (path/note_id/query, job-type-dependent), `idempotency_key` | Events log (bridges fs/git/mcp domain into job domain); `job_status` tool resolution |
| `job.started` | Huey job execution begins, **after** the idempotency check passes (per ADR-0009: idempotency is checked at job start) | `job_id`, `job_type`, `worker_pid` | `job_status` tool (`working` state) |
| `job.retried` | Huey retry (before exhausting configured retries) | `job_id`, `job_type`, `attempt_number`, `next_retry_at`, `error_summary` (sanitized) | Audit log, `system_diagnostics` |
| `job.completed` | Huey job reaches terminal success | `job_id`, `job_type`, `duration_ms`, `noop` (bool — true if idempotency check short-circuited the job) | Domain-completion translator (see §1.4); `job_status` tool (`completed`) |
| `job.failed` | Huey job reaches terminal failure (retries exhausted) | `job_id`, `job_type`, `retry_count`, `last_error` (sanitized, no secrets/paths-as-secrets), `target` | `vault_status`/note `index_state` update (see §4), `job_status` tool (`failed`), audit log |
| `job.cancelled` | MCP `job_cancel` tool call, invoking Huey's task revoke | `job_id`, `job_type`, `cancelled_by` (`mcp_client`) | `job_status` tool (`cancelled`), audit log |

### 1.4 Domain-level job-completion events (master-spec-named)

These are emitted by a single small "job-completion translator" keyed on `job_type`, immediately after the corresponding `job.completed` — this keeps the translation logic in one place rather than duplicating it per job type.

| Event type | Emitted when | Payload fields | Consumers |
|---|---|---|---|
| `index.update_completed` | `job.completed` with `job_type=indexing` or `job_type=reindex`, and `noop=false` | `note_id`, `path`, `content_hash`, `chunk_count`, `index_version`, `duration_ms` | `vault_status`, retrieval layer (index is now current for this note), events log |
| `index.reindex_completed` | The batch-level `job.completed` for a `reindex_start`-dispatched job finishes (summarizes N per-note `index.update_completed`s) | `notes_processed`, `notes_changed`, `notes_failed`, `duration_ms` | `reindex_start`'s `job_status` completion payload |
| `research.job_completed` | `job.completed` with `job_type=research` | `research_job_id`, `draft_handle`, `source_urls[]`, `provider`, `model` | `research_start`'s `job_status` completion payload — **note:** payload references a draft, not a `note_id`; nothing is written to the vault until `research_commit` runs (§5) |
| `ingestion.job_completed` | `job.completed` with `job_type=ingestion` (bulk import — e.g. the initial vault bootstrap scan needed for the real vault sampled in `DATA_MODEL.md` §0; no dedicated MCP tool exposes this yet per ADR-0007 — flagged as a mapping gap in §5) | `notes_ingested`, `notes_skipped`, `duration_ms` | Events log, `vault_status` |

### 1.5 Duplicate detection / fusion domain (master spec §10)

| Event type | Trigger source | Payload fields | Consumers |
|---|---|---|---|
| `dedup.duplicate_detected` | Huey job (`duplicates_scan`), emitted once per candidate pair found during the sweep | `note_a_id`, `note_b_id`, `content_hash_match` (bool), `lexical_score`, `semantic_score`, `path_similarity`, `provenance_similarity` | `note_duplicates`/`duplicates_scan` result assembly, events log (queryable by `correlation_id` for the whole scan) |
| `dedup.scan_completed` | `job.completed` with `job_type=duplicates_scan` | `candidates_found`, `notes_scanned`, `duration_ms` | `duplicates_scan`'s `job_status` completion payload |
| `dedup.merge_completed` | MCP tool call (`note_merge`, sync, post-elicitation-confirmation) | `surviving_note_id`, `superseded_note_ids[]`, `merge_policy_applied`, `dry_run` (bool) | Provenance/lineage store (superseded-version chain per master spec §9), triggers `git.commit_completed` |

### 1.6 Reconciliation domain (ADR-0009's full-scan backstop)

| Event type | Trigger source | Payload fields | Consumers |
|---|---|---|---|
| `reconciliation.discrepancy_found` | Reconciliation job (startup and/or periodic full scan), one per discrepancy | `path`, `discrepancy_type` (`missing_from_index`\|`missing_from_disk`\|`hash_mismatch`), `note_id` (if known) | Job enqueuer (re-triggers the normal indexing job for that path — see §4.2) |
| `reconciliation.completed` | Reconciliation job finishes | `scan_started_at`, `scan_finished_at`, `paths_scanned`, `discrepancies_found`, `jobs_enqueued`, `duration_ms` | `vault_status`, `system_diagnostics`, audit log |

## 2. Event Envelope Schema

Every event — regardless of domain — is a single JSON object matching this envelope. This is the wire/storage format; job-type-specific fields live inside `payload`.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AI_BRAIN Event Envelope",
  "type": "object",
  "required": [
    "event_id", "event_type", "schema_version", "occurred_at",
    "source", "correlation_id", "payload"
  ],
  "properties": {
    "event_id": {
      "type": "string",
      "format": "uuid",
      "description": "Unique identifier for this event instance. UUIDv4 is sufficient; UUIDv7 is a low-stakes Phase 1 upgrade if time-ordered IDs prove useful for the events table's primary key."
    },
    "event_type": {
      "type": "string",
      "description": "Dot-namespaced event name, e.g. 'vault.note_created'. See §1 for the closed enum.",
      "enum": [
        "fs.path_changed",
        "vault.note_created", "vault.note_modified", "vault.note_deleted",
        "vault.note_moved", "vault.metadata_changed",
        "git.repository_changes_detected", "git.commit_completed",
        "job.enqueued", "job.started", "job.retried", "job.completed",
        "job.failed", "job.cancelled",
        "index.update_completed", "index.reindex_completed",
        "research.job_completed", "ingestion.job_completed",
        "dedup.duplicate_detected", "dedup.scan_completed", "dedup.merge_completed",
        "reconciliation.discrepancy_found", "reconciliation.completed"
      ]
    },
    "schema_version": {
      "type": "integer",
      "default": 1,
      "description": "Envelope schema version, for forward-compatible evolution."
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time",
      "description": "UTC ISO-8601 timestamp of when the event was produced (not when it is later persisted/read)."
    },
    "source": {
      "type": "string",
      "enum": ["filesystem_watcher", "huey_job", "git_operation", "mcp_tool_call", "reconciliation_job"],
      "description": "Which subsystem produced this event."
    },
    "correlation_id": {
      "type": "string",
      "format": "uuid",
      "description": "Identifies the whole causal chain this event belongs to (e.g. one filesystem save → debounce → job → index update). Minted fresh at the ROOT of a chain (debounce settle, or an MCP tool call) and copied forward unchanged by every downstream event."
    },
    "causation_id": {
      "type": ["string", "null"],
      "format": "uuid",
      "description": "The event_id of the immediate parent event that directly caused this one. Null for root events. Distinct from correlation_id: correlation_id names the whole chain, causation_id names the one-hop parent — needed to reconstruct chain order, not just chain membership."
    },
    "idempotency_key": {
      "type": ["string", "null"],
      "description": "Deterministic key used by the job layer to detect duplicate/no-op work, e.g. 'index:{normalized_path}' or 'dedup_scan:{date}'. Null for events that aren't job-triggering (e.g. domain-completion events)."
    },
    "actor": {
      "type": ["string", "null"],
      "description": "For source=mcp_tool_call: which MCP tool/client invoked the action, for provenance (CLAUDE.md rule 24). Null otherwise."
    },
    "payload": {
      "type": "object",
      "description": "event_type-specific fields, per the tables in §1."
    }
  }
}
```

### 2.1 Durability recommendation: a lightweight `events` table, separate from Huey's job store

**Recommendation: yes, add a narrow, append-only `events` table to AI_BRAIN's own metadata SQLite database** (the file ADR-0004 already separated from Huey's `SqliteHuey` job-store file). Concrete DDL:

```sql
CREATE TABLE events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL UNIQUE,
    event_type      TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    occurred_at     TEXT NOT NULL,
    source          TEXT NOT NULL,
    correlation_id  TEXT NOT NULL,
    causation_id    TEXT,
    idempotency_key TEXT,
    actor           TEXT,
    payload_json    TEXT NOT NULL
);
CREATE INDEX idx_events_correlation ON events(correlation_id);
CREATE INDEX idx_events_type_time   ON events(event_type, occurred_at);
CREATE INDEX idx_events_idempotency ON events(idempotency_key);
```

**Is this redundant given Huey already tracks job state? No — and here's the specific reasoning, not just an assertion:**

1. **Different scope.** Huey's job-store tables only know about jobs Huey executed. A large fraction of this taxonomy is *not* a Huey job at all: `fs.path_changed` happens before any job exists; `vault.note_created` emitted by a sync MCP tool call (`note_create`) never touches Huey; `git.commit_completed` is a subprocess call, not a job. Huey structurally cannot be the durable record for these.
2. **Different question.** Huey's tables answer "what is the current/last state of job X?" — a state machine, not a log. The explicit design requirement here is a **correlation chain** ("filesystem event → debounce → Huey job → index update → MCP notification"), which needs a queryable, append-only, cross-domain join key (`correlation_id`). That's a log-shaped need, not a state-shaped one, and ADR-0004 already ruled out coupling AI_BRAIN's schema to Huey's opaque internal schema for unrelated reasons (write-contention, independent evolution) — the same reasoning applies here.
3. **Different lifespan/semantics.** ADR-0004 explicitly classified Huey's queue state as *disposable/re-derivable* and AI_BRAIN's metadata as *durable knowledge state*. Domain events (a note was created, a merge happened, an index was updated) are provenance-adjacent durable facts (master spec §9, CLAUDE.md rule 24) — they belong with durable state, not disposable queue state that Huey may prune.
4. **Not duplicated where it would be redundant.** The `events` table does **not** mirror Huey's raw internal retry/backoff bookkeeping wholesale — it only records the *domain-meaningful* transitions (`job.enqueued`, `job.started`, `job.retried`, `job.completed`/`failed`/`cancelled`) as a thin translation layer, not Huey's full internal representation. Huey remains the sole authority for "what is job X's current live state right now" (queried directly by `job_status`, see §5); the `events` table is the authority for "what happened, in what order, across all domains, ever" (queried for audit, tracing, and `vault_status`/`note_provenance`).

This satisfies the master specification's "events should be durable enough to recover from failures" requirement concretely: even the pre-job segment of the pipeline (a raw filesystem signal, before any Huey job exists) becomes durable and inspectable, not just in-memory/log-line ephemeral.

**Retention:** flagged as a Phase 1 sizing decision, not decided here — recommend a periodic Huey task (same pattern as Git backups) that archives/prunes `events` rows older than N days once the table's growth is measured, per CLAUDE.md rule 20 ("do not optimize prematurely; measure before optimizing").

**Process note:** because this table is genuinely new infrastructure (not just applying an already-accepted decision), it should be captured as **ADR-0010** before Phase 1 implementation, per CLAUDE.md rule 7 ("every significant technical decision gets an ADR"). This document is written to serve as that ADR's Context/Decision source material.

## 3. Primary Pipeline Walkthrough: Filesystem Event → Indexed

Concrete, thread/process-by-thread trace of the single most important flow.

### 3.1 Raw signal (OS / watchdog thread)

Obsidian saves `notes/foo.md` (typically temp-file-write + rename). Linux inotify (via `watchdog`'s `Observer`) emits raw events — `IN_CREATE`/`IN_MODIFY`/`IN_MOVED_FROM`/`IN_MOVED_TO`/`IN_CLOSE_WRITE` — on **watchdog's own dedicated OS thread** (the `Observer` thread, entirely separate from the asyncio event loop and from the Huey worker process). Per ADR-0009 decision 1, the single `Observer` is scheduled on `vault_root` with `.git`/`.obsidian`/plugin-cache subtrees excluded inside the handler.

### 3.2 Debounce (same watchdog thread + a lightweight sweeper)

The handler, still executing **on the watchdog thread**, normalizes every raw event to "path P changed" (a move yields two normalized signals: one for `src_path`, one for `dest_path` — ADR-0009 decision 2) and updates an in-process `dict[path, last_seen_timestamp]` guarded by a lock (this dict is touched from the watchdog thread on every raw event). A separate lightweight sweeper (either a single background `threading.Timer`-based reschedule per path, or one dedicated sweeper thread polling the map every ~250ms) fires a **"path settled"** callback once a path's `last_seen` is older than the quiet window (~1–2s, tuned empirically per ADR-0009) with no newer event since. This is deliberately plain-thread machinery — **no asyncio bridge** is used here (ADR-0009 decision 3); `asyncio.run_coroutine_threadsafe` is reserved for AI_BRAIN's own asyncio-native status/logging layer, not this path.

At "path settled," an `fs.path_changed` event is appended to the `events` table (`source=filesystem_watcher`, freshly minted `correlation_id` = the root of this chain, `causation_id=null`). Semantic classification (created vs. modified vs. deleted vs. moved) is **deliberately not decided here** — ADR-0009 treats "path X changed" as a trigger to re-derive truth from disk, never a diff to apply, so classification happens at job execution (§3.4), where it has access to both current disk state and current indexed state.

### 3.3 Enqueue (same debounce thread, synchronous)

The "path settled" callback calls the Huey enqueue function **directly and synchronously** — Huey's SQLite enqueue is just a parameterized DB `INSERT`, safe to call from any thread. It constructs `idempotency_key = "index:{normalized_path}"` and enqueues an `index_note(path)` job tagged with `correlation_id` (carried from `fs.path_changed`) and `causation_id` = that event's `event_id`. A `job.enqueued` event is appended (`source=huey_job`, `payload.job_id` = the Huey task id, same `correlation_id`).

### 3.4 Execution (Huey worker process)

A separate Huey worker process (per ADR-0002's model) picks up the job. It acquires `huey.lock_task` keyed on the normalized path (preventing two concurrent indexing runs on the same path — ADR-0009 decision 4). At **job start** — the explicit point ADR-0009 recommends for the idempotency check — a `job.started` event is appended, then:

1. Compute current on-disk `mtime`/content-hash for `path`.
2. Compare to the note's stored hash/`index_version` in AI_BRAIN's metadata DB.
3. **Unchanged →** no-op. `job.completed` is appended with `noop=true`. No `index.update_completed` fires (avoids event noise on every duplicate/no-op trigger — the Testing Strategy's tolerance for duplicate events is satisfied cheaply here, and the no-op is still durably recorded for test/audit purposes).
4. **Changed →** re-derive current truth: parse front matter where present (see `DATA_MODEL.md` §0 for the real vault's largely frontmatter-less legacy content, which instead derives provider/source metadata from folder name and a first-line URL), chunk (`chonkie`), embed (`sentence-transformers`), upsert to Qdrant, update FTS5 + metadata rows, update the provenance/lineage record. The **last** write performed is the metadata row's hash/`index_version` update — this is the commit marker (see §4.3 for why ordering here matters for crash recovery).
5. Classify the change against prior state to pick the semantic event: path newly indexed → `vault.note_created`; hash differs, path unchanged → `vault.note_modified` (or `vault.metadata_changed` if only front matter differs — see payload's `changed_sections`); path gone from disk → see §3.5 for move-vs-delete resolution.
6. On success: `job.completed` (`job_type=indexing`, `noop=false`) → the job-completion translator emits `index.update_completed` **and** the semantic `vault.note_*`/`vault.metadata_changed` event, both carrying `correlation_id` from step 1 and `causation_id` = the `job.completed` event's id.

### 3.5 Move detection (content-hash reconciliation)

Because a move yields two independent "path changed" signals (§3.2) processed as two independent jobs (locked per-path, not per-note, so they can run concurrently), move detection is done by re-deriving from shared state, not by pairing raw events:

- Whichever job runs first and finds "file present on disk, content-hash matches an *active* indexed note whose stored path no longer exists on disk" performs the move: updates the note's path in place (metadata-only, no re-embedding) and emits `vault.note_moved`.
- The other job (processing the now-vanished old path) re-derives current DB state at execution time, finds the note record already re-pathed by the first job, and simply no-ops — this is safe *because* both jobs re-read authoritative current state rather than applying a stale diff (ADR-0009's core idempotency philosophy), so ordering between the two jobs doesn't matter.
- **Cross-boundary moves** (across the watched root, or degraded by the confirmed watchdog issue #308) arrive as an unpaired delete + create with no shared signal to correlate reactively. Per ADR-0009, this is an accepted risk: it surfaces as `vault.note_deleted` + `vault.note_created` rather than `vault.note_moved`. The index itself stays correct (content is indexed under the new path either way); only the "this is the same note, just moved" *lineage* signal is lost in the reactive path. Retroactively correlating these via a vault-wide hash pass is a reasonable Phase 2 enhancement inside the reconciliation job (§4.2), not a Phase 1 requirement.

### 3.6 MCP visibility (polling, not push)

The current MCP spec (per ADR-0007) is stateless — MRTR, no server-initiated push. So "notify an MCP client" concretely means: **the client polls `job_status(job_id)`.** For `reindex_start`-driven jobs specifically, `job_status` resolves `job_id` against Huey's own state store for the raw working/completed/failed state, and — to enrich the response once terminal — looks up the `events` table by `correlation_id`/`job_id` to attach the resulting `index.update_completed`/`index.reindex_completed` payload (chunk counts, notes changed, etc.). There is no separate "notification" transport; the interim shim's entire purpose (ADR-0007) is making this polling model work today, structured so it swaps cleanly for the official `io.modelcontextprotocol/tasks` extension once the SDK supports it.

### Worked example (abbreviated envelopes)

```
1  fs.path_changed        {correlation_id: C1, event_id: E1, causation_id: null,
                            payload: {path: "notes/foo.md", raw_event_kinds: ["modified"]}}
2  job.enqueued            {correlation_id: C1, event_id: E2, causation_id: E1,
                            payload: {job_id: "huey-9f2a", job_type: "indexing",
                                      idempotency_key: "index:notes/foo.md"}}
3  job.started              {correlation_id: C1, event_id: E3, causation_id: E2,
                            payload: {job_id: "huey-9f2a", job_type: "indexing"}}
4  job.completed            {correlation_id: C1, event_id: E4, causation_id: E3,
                            payload: {job_id: "huey-9f2a", job_type: "indexing",
                                      duration_ms: 842, noop: false}}
5  vault.note_modified      {correlation_id: C1, event_id: E5, causation_id: E4,
                            payload: {note_id: "n-1044", path: "notes/foo.md", ...}}
6  index.update_completed   {correlation_id: C1, event_id: E6, causation_id: E4,
                            payload: {note_id: "n-1044", chunk_count: 6, index_version: 3}}
```

## 4. Failure / Recovery Event Handling

### 4.1 Permanent job failure (retries exhausted, per ADR-0002)

Huey's own retry/backoff (configured via `@huey.task(retries=N, retry_delay=...)`) governs in-flight retries; each attempt before exhaustion appends `job.retried`. On final failure:

- `job.failed` is appended (`retry_count`, sanitized `last_error`, `target`).
- **Dead-letter equivalent:** Huey has no literal dead-letter queue. The `job.failed` event *is* the dead-letter record — durable, queryable by `event_type='job.failed'` in the `events` table (this is a concrete reason the table earns its keep beyond what Huey alone provides).
- **Note lifecycle effect:** rather than overloading the master spec §11 knowledge-lifecycle `status` field (draft/active/verified/stale/superseded/archived — a *content* axis) with indexing-health meaning, add an **orthogonal** `index_state` field to the note's metadata row (`current` / `stale` / `failed`) plus `last_index_error`. A permanently-failed indexing job sets `index_state='failed'`, surfaced via `vault_status`/`note_provenance`.
- **Recovery path:** not automatic requeue-forever. Recovery happens via (a) the next reconciliation sweep re-detecting the path as a discrepancy and re-enqueuing (§4.2), or (b) an operator/MCP-driven `reindex_start` manually retriggering it. Either path re-enters the same idempotent job (§3.4) — there is no separate "retry a failed job" code path to build.

### 4.2 Reconciliation-detected discrepancy (ADR-0009 full-scan backstop)

The periodic/startup reconciliation job walks the vault, comparing disk (paths + hashes) against indexed state. For each mismatch it emits `reconciliation.discrepancy_found` (`missing_from_index` / `missing_from_disk` / `hash_mismatch`), and — critically — **re-triggers the exact same enqueue function** the debounce layer uses (§3.3), just with `source=reconciliation_job` instead of `filesystem_watcher`. Reconciliation does not special-case any index-mutation logic; it only closes gaps in *triggering*, relying on the same idempotent job to do the actual work. On completion, one `reconciliation.completed` summarizes counts (`paths_scanned`, `discrepancies_found`, `jobs_enqueued`, `duration_ms`).

### 4.3 Partial/interrupted operations (interrupted indexing, DB failure, partial writes)

Because job execution re-derives truth from disk rather than applying an incremental diff (§3.4), the concrete recovery mechanism is **ordering the job's writes so nothing is marked "done" until every downstream write has actually landed**:

1. Qdrant upsert
2. FTS5 write
3. Metadata row hash/`index_version` update ← **last**, acts as the commit marker

If the process crashes at any point before step 3, the note's stored hash still differs from the current disk hash — so the *next* trigger for that path (a fresh fs event, or the next reconciliation sweep) re-detects it as needing indexing and simply re-runs the whole job from scratch. No partial-rollback logic is needed; partial writes are self-healing by construction, which is exactly what the Testing Strategy's "duplicate events / repeated jobs" tolerance is designed to validate. A raw SQLite write failure or embedding/Qdrant network failure inside the job simply raises, which routes into Huey's retry/backoff (§4.1) — no separate handling required.

## 5. Mapping to the MCP Tool Contract (ADR-0007)

| MCP tool | Execution | Event(s) it triggers | `job_status` vocabulary notes |
|---|---|---|---|
| `note_create` | sync | Tool handler writes to disk, then **directly enqueues** the same indexing job used by the debounce layer (bypassing the inotify round-trip for precision/latency — see note below) → emits `vault.note_created` (semantically known by the tool, not derived) → `job.enqueued`/`...`/`index.update_completed` → `git.commit_completed` (auto-invoked, ADR-0005) | n/a (sync tool; only the background indexing job is job-tracked, and it's not one the calling client polls) |
| `note_update` | sync | Same pattern → `vault.note_modified` or `vault.metadata_changed` → indexing chain → `git.commit_completed` | n/a |
| `note_move` | sync | Direct emit of `vault.note_moved` (tool already knows old/new path — no reconciliation guessing needed) → indexing chain → `git.commit_completed` | n/a |
| `note_delete` | sync, MRTR-confirmed | `vault.note_deleted` → index removal → `git.commit_completed` | n/a |
| `note_merge` | sync, MRTR-confirmed | `dedup.merge_completed` → `git.commit_completed` | n/a |
| `note_duplicates` | sync | None (pure read; ephemeral result, not persisted as an event) | n/a |
| `duplicates_scan` | **task-backed** | Per-candidate `dedup.duplicate_detected`, then `job.completed` → `dedup.scan_completed` | `working` ← `job.started`/`job.retried`; `completed` ← `job.completed` (response enriched with `dedup.scan_completed` payload); `failed` ← `job.failed`; `cancelled` ← `job.cancelled` via `job_cancel` |
| `research_start` | **task-backed** (draft only) | `job.completed` → `research.job_completed` (payload references a `draft_handle`, **not** a `note_id` — nothing is written to the vault yet) | Same working/completed/failed/cancelled mapping as above |
| `research_commit` | sync | Explicit vault write → `vault.note_created` → indexing chain → `git.commit_completed` (this is the step that actually turns a research draft into indexed, provenanced knowledge) | n/a |
| `reindex_start` | **task-backed** | One `index.update_completed` per note touched, plus one batch `index.reindex_completed` | `completed` response enriched with `index.reindex_completed`'s summary counts |
| `git_commit` | sync | `git.commit_completed` (same event as the auto-invoked case; standalone call just has `source=mcp_tool_call` instead of being chained off another mutation) | n/a |
| `git_status`, `git_log`, `note_history`, `note_provenance`, `vault_status`, `system_diagnostics` | sync, read-only | None emitted; several of these are natural **consumers** of the `events` table (`vault_status` surfaces `index_state`/last `reconciliation.completed`; `system_diagnostics` surfaces recent `job.failed`/`job.retried` counts) | n/a |
| `job_status` | sync, polls Huey | Reads (doesn't emit): Huey's live state for `working`/`completed`/`failed`/`cancelled`, enriched from the `events` table for the terminal-state payload | See mapping note below |
| `job_cancel` | mutating | `job.cancelled` | `cancelled` |

**On the `job_status` vocabulary mapping specifically:**

- `working` ← `job.started` / `job.retried` (job is live, no terminal event yet)
- `completed` ← `job.completed` (`noop` may be true or false; the domain-completion event in §1.4 carries the detail)
- `failed` ← `job.failed`
- `cancelled` ← `job.cancelled`
- `input_required` — **no corresponding event exists in this taxonomy today, and this is a deliberate, documented gap, not an oversight.** `input_required` is reserved for MRTR elicitation (confirming `note_delete`/`note_merge`, or a future `note_summarize` opt-in gate per ADR-0007's open question), but every elicitation in the current contract happens **synchronously before** a job is ever dispatched — none of the three task-backed tools (`duplicates_scan`, `research_start`, `reindex_start`) pause mid-flight for confirmation. So `input_required` is part of the vocabulary for forward-compatibility with the official tasks extension, but the interim shim should not expect to return it for any currently-defined tool. If a future task-backed tool needs mid-flight confirmation, it will need a new event (`job.input_required`) added to §1.3 at that time.

**Gap flagged:** `ingestion.job_completed` (master-spec-named) currently has no corresponding MCP tool in ADR-0007's contract — ingestion jobs today originate internally (initial vault bootstrap — a real, non-hypothetical need given the actual vault sampled in `DATA_MODEL.md` §0 — or bulk reconciliation-style imports), not from a client-invoked tool. If a bulk-import MCP tool is added later, it should be task-backed and follow the `duplicates_scan`/`research_start` pattern exactly.

## 6. Summary of Concrete Recommendations (for the Phase 1 implementer)

1. Implement the envelope in §2 as a shared `Event` dataclass/TypedDict used by every emitter (debounce layer, job-completion translator, git wrapper, MCP tool handlers).
2. Add an `events` table (§2.1 DDL) to AI_BRAIN's metadata SQLite DB (not Huey's job-store file) — write it up as ADR-0010 before building it.
3. Debouncing and enqueue happen on plain threads, no asyncio bridge (§3.2–3.3) — do not build one.
4. Idempotency is checked at job start by comparing disk state to indexed state, never by diffing events (§3.4).
5. Move detection is a same-run content-hash reconciliation between two independently-locked per-path jobs (§3.5); cross-boundary moves are an accepted degrade-to-delete+create risk.
6. Order job writes so the metadata-row hash/`index_version` update is always last — that's the crash-recovery commit marker (§4.3).
7. MCP mutation tools should enqueue the indexing job directly (not rely solely on the inotify round-trip) for latency/precision, while still letting the filesystem watcher's redundant trigger safely no-op via idempotency (§5) — one indexing code path, triggered from multiple sources.
8. Add an `index_state`/`last_index_error` field to the note metadata row, orthogonal to the knowledge-lifecycle `status` field (§4.1) — this is already reflected in the `notes` table design in `DATA_MODEL.md`, cross-reference before implementing.
9. Run an initial `ingestion` job against the real vault (per `DATA_MODEL.md` §0's folder-name/source-URL/turn-header inference logic) as the first concrete exercise of this event model in Phase 1 — this is not a hypothetical bootstrap scenario, it is the actual first workload AI_BRAIN will run.

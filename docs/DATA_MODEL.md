# AI_BRAIN — Data Model

- **Date:** 2026-08-26
- **Author:** Claude Code (AI_BRAIN Phase 0)
- **Status:** Phase 0 exit-criteria deliverable — data model is defined
- **Scope:** AI_BRAIN's own SQLite metadata database (`ai_brain.db`), distinct from Huey's job-store file (ADR-0002/ADR-0004), plus the Qdrant payload schema that rides alongside vectors in the separate vector store.
- **Validated against:** a real sample of the user's actual vault content (see §0 below) — this is not a purely theoretical design.

## 0. Real Vault Data Format — Validated Against a Sample

Before finalizing this schema, a sample of the user's actual private vault backup was inspected (structure only; content was not copied into this or any other AI_BRAIN document, consistent with treating vault content as the user's private data). The sample directly confirmed the master specification's §8 "AI-Origin Folders" example (`CHAT_GPT`, `CLAUDE`, `GROK_GPT`, `QWEN` are not hypothetical folder names — they are the user's real, current vault layout) and revealed a concrete fact that changes a real assumption in this schema's first draft:

**None of the sampled files have YAML frontmatter.** Three distinct content shapes exist, none of them frontmatter-based:

1. **ChatGPT/Claude-style chat exports** (`CHAT_GPT/`, `CLAUDE/`, `GROK_GPT/`): a `> From: <source_url>` blockquote as the first line, followed by `# you asked` / `# chatgpt response` (or provider-equivalent) headers demarcating conversational turns.
2. **Qwen-style chat exports** (`QWEN/`): `### USER` / `### ASSISTANT` headers demarcating turns; the `From:` line is not always present.
3. **Reference/training material** (e.g. `OWASP-A05:injection.mdfiles/`): Setext-style headers (`===`/`---` underlines), versioned-document metadata embedded in prose ("Version: 1.0 (Draft)"), no source URL line at all — this is imported reference content, not an AI conversation export.

**Implication for this schema**: metadata that a frontmatter-based design would expect to read from a YAML block must instead be **derived** for this large body of existing legacy content:
- `provider` is inferred from the **folder name** via a small, explicit mapping table (`CHAT_GPT` → `openai`, `CLAUDE` → `anthropic`, `GROK_GPT` → `xai`, `QWEN` → `qwen`), not read from a field.
- `source_url` (feeding `provenance_sources.url`, §2.4) is extracted by parsing the first `> From: <url>` line where present; absent for the Qwen and reference-material shapes.
- `origin` (§2.2) is `ai_generated` for the three chat-export folders and `imported` for reference material like the OWASP folder — this classification is a folder/pattern-based heuristic applied at ingestion time, not a stored field the source file provides.
- Chunking (ADR-0003, `chonkie`) should treat the `# you asked`/`### USER`-style turn headers as natural chunk/section boundaries for these files, which is a *better* structural signal than frontmatter would have been for this content shape, not a worse one.
- This does **not** invalidate ADR-0003's flagged Phase 1 check on `chonkie`'s frontmatter handling — future user-authored notes (new content the user writes directly in Obsidian, or notes AI_BRAIN itself creates via `note_create`/`research_commit`) may well use frontmatter going forward, and the schema must support both shapes. The finding here is specifically that the **existing corpus being migrated in** does not, and ingestion logic must not assume it does.
- The colon character in `OWASP-A05:injection.mdfiles` (a valid Linux filename character, but unusual and invalid on some other filesystems) is a concrete reminder that path-handling code (ADR-0009's watcher, this schema's `notes.path` column, ADR-0005's Git wrapper) must not assume filenames are limited to a "safe" ASCII subset — this is a real example from the real vault, not a hypothetical edge case.

An **ingestion-time origin/provenance inference step** (folder-name mapping + first-line URL parsing + turn-header detection) is therefore a required Phase 1 component, feeding the `provenance` and `provenance_sources` tables below — it is the concrete mechanism referenced generically in ADR-0003's "provenance schema" requirement.

## 1. Overview

Three durable stores exist, per already-accepted ADRs:

| Store | Owns | ADR |
|---|---|---|
| `ai_brain.db` (this document) | note metadata, provenance, lifecycle, duplicate candidates, chunk text, FTS5 keyword index | ADR-0004 |
| Huey's separate `.db` file | disposable/re-derivable job-queue state (opaque schema, not modeled here) | ADR-0002, ADR-0004 |
| Qdrant collection (Docker, alias-addressed) | dense (BGE-M3, 1024-dim) + sparse (miniCOIL) vectors per chunk | ADR-0006, ADR-0008 |

The vault (Markdown, with or without YAML frontmatter — see §0) remains the canonical knowledge store (Master Spec §2). `ai_brain.db` is derived/index state that must be rebuildable from the vault plus provenance history — it is not itself the source of truth, consistent with Master Spec §2/§3.

Every connection opener must set, per ADR-0004:

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;      -- OFF by default in SQLite; required for every FK/CASCADE below to actually enforce
PRAGMA busy_timeout = 5000;    -- per-connection, resets to 0 on every new connection — must be set every time
```

## 2. Complete DDL

### 2.1 Migration bookkeeping

ADR-0004's decided migration mechanism is `PRAGMA user_version` + numbered `.sql` files — that pragma, not a table, is the authoritative version pointer the runner checks. `schema_migrations` is a companion audit log only (human-readable history, drift/tamper detection via checksum), not the version source of truth:

```sql
CREATE TABLE schema_migrations (
    version     INTEGER PRIMARY KEY,   -- matches PRAGMA user_version after this migration applied
    filename    TEXT NOT NULL,
    checksum    TEXT NOT NULL,         -- sha256 of the .sql file, detects drift between applied and on-disk migration files
    applied_at  TEXT NOT NULL          -- ISO8601 UTC
);
```

### 2.2 `notes`

```sql
CREATE TABLE notes (
    id               INTEGER PRIMARY KEY,               -- rowid; also FTS5 content_rowid for notes_fts
    path             TEXT NOT NULL UNIQUE,               -- vault-relative path, canonical identifier
    title            TEXT NOT NULL,
    origin           TEXT NOT NULL                       -- Master Spec §7 "source/origin"; see §0 for inference logic
                         CHECK (origin IN ('human','ai_generated','web_research','imported','merged')),
    provider         TEXT,                                -- 'anthropic'|'openai'|'xai'|'qwen'|'google'|'ollama'|... ; NULL if human-authored; see §0's folder-name mapping
    model            TEXT,                                -- specific model id/version; NULL if not applicable/not derivable
    folder           TEXT,                                -- AI-origin folder classification (§8), set by app policy, never derived in SQL
    status           TEXT NOT NULL DEFAULT 'draft'
                         CHECK (status IN ('draft','active','verified','stale','superseded','archived')),  -- §11
    confidence       REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    content_hash     TEXT NOT NULL,                        -- sha256 of normalized body (frontmatter-stripped where present); exact-dup + change detection
    chunk_count      INTEGER NOT NULL DEFAULT 0,
    tags_text        TEXT NOT NULL DEFAULT '',             -- DERIVED/trigger-maintained (space-joined tag names) for notes_fts; never written directly by app
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    last_indexed_at  TEXT,                                 -- NULL until first successful chunk/embed pass
    deleted_at       TEXT                                  -- soft-delete tombstone; NULL = live in vault
);

CREATE INDEX idx_notes_status        ON notes(status);
CREATE INDEX idx_notes_content_hash  ON notes(content_hash);
CREATE INDEX idx_notes_deleted_at    ON notes(deleted_at);
CREATE INDEX idx_notes_folder        ON notes(folder);
```

### 2.3 Tags (normalized)

```sql
CREATE TABLE tags (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,      -- normalized lowercase slug, used for filtering/lookup
    display_name  TEXT NOT NULL              -- original casing, for UI/display
);

CREATE TABLE note_tags (
    note_id  INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
    PRIMARY KEY (note_id, tag_id)
);

CREATE INDEX idx_note_tags_tag ON note_tags(tag_id);
```

### 2.4 Provenance (W3C PROV-inspired: activity/agent/derivation)

```sql
-- One row per "activity" applied to a note (PROV Activity), with provider/model as the PROV Agent.
CREATE TABLE provenance (
    id                     INTEGER PRIMARY KEY,
    note_id                INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    activity_type          TEXT NOT NULL
                               CHECK (activity_type IN (
                                   'ingested','web_research','ai_synthesis','summarization',
                                   'human_edit','merge','split','reindex_only','migration'
                               )),
    provider               TEXT,        -- agent: 'anthropic'|'openai'|'xai'|'qwen'|'google'|'ollama'|'human'
    model                  TEXT,        -- agent: specific model id/version
    human_edited           INTEGER NOT NULL DEFAULT 0 CHECK (human_edited IN (0,1)),
    research_job_id        INTEGER REFERENCES research_jobs(id) ON DELETE SET NULL,  -- see §2.7
    supersedes_note_id     INTEGER REFERENCES notes(id) ON DELETE SET NULL,          -- this activity's output supersedes another note
    superseded_by_note_id  INTEGER REFERENCES notes(id) ON DELETE SET NULL,          -- backfilled once a later note supersedes this one
    transformation_notes   TEXT,        -- free-text description of what happened
    occurred_at            TEXT NOT NULL,   -- when the activity happened
    recorded_at            TEXT NOT NULL    -- when this row was written (may lag occurred_at for backfilled records, e.g. legacy ingestion per §0)
);

CREATE INDEX idx_provenance_note        ON provenance(note_id);
CREATE INDEX idx_provenance_supersedes  ON provenance(supersedes_note_id);
CREATE INDEX idx_provenance_superseded  ON provenance(superseded_by_note_id);

-- PROV "used" relation: external source entities an activity consumed (web pages, chat-export source URLs per §0).
CREATE TABLE provenance_sources (
    id             INTEGER PRIMARY KEY,
    provenance_id  INTEGER NOT NULL REFERENCES provenance(id) ON DELETE CASCADE,
    url            TEXT NOT NULL,       -- for legacy chat exports (§0): the parsed "> From: <url>" line; NULL/absent where the source shape has none
    title          TEXT,
    accessed_at    TEXT
);

CREATE INDEX idx_provenance_sources_provenance ON provenance_sources(provenance_id);
CREATE INDEX idx_provenance_sources_url        ON provenance_sources(url);

-- PROV "wasDerivedFrom" relation for multi-source activities (merges), beyond the single-predecessor
-- supersedes_note_id column above.
CREATE TABLE provenance_derivations (
    provenance_id   INTEGER NOT NULL REFERENCES provenance(id) ON DELETE CASCADE,
    source_note_id  INTEGER NOT NULL REFERENCES notes(id)      ON DELETE CASCADE,
    PRIMARY KEY (provenance_id, source_note_id)
);
```

### 2.5 Lifecycle status history (Master Spec §11)

```sql
CREATE TABLE note_lifecycle_history (
    id           INTEGER PRIMARY KEY,
    note_id      INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    from_status  TEXT,       -- NULL on initial creation
    to_status    TEXT NOT NULL
                     CHECK (to_status IN ('draft','active','verified','stale','superseded','archived')),
    reason       TEXT,
    changed_by   TEXT,       -- 'system' | 'user' | 'mcp:<tool_name>' | 'job:<research_jobs.id>'
    changed_at   TEXT NOT NULL
);

CREATE INDEX idx_lifecycle_history_note ON note_lifecycle_history(note_id, changed_at);
```

### 2.6 Duplicate detection (Master Spec §10, ADR-0003)

```sql
CREATE TABLE duplicate_candidates (
    id                   INTEGER PRIMARY KEY,
    note_a_id            INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    note_b_id            INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    detection_method     TEXT NOT NULL
                             CHECK (detection_method IN (
                                 'content_hash','minhash_lsh','cosine_similarity','metadata_match','combined'
                             )),
    lexical_score        REAL,   -- MinHash-LSH Jaccard estimate, 0..1
    semantic_score       REAL,   -- embedding cosine similarity, 0..1
    metadata_match_score REAL,   -- normalized path/title similarity, 0..1
    combined_score       REAL,   -- fused score used for thresholding/ranking
    status               TEXT NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending','confirmed','rejected','merged')),
    detected_at          TEXT NOT NULL,
    resolved_at          TEXT,
    resolved_by          TEXT,
    resolution_note      TEXT,
    CHECK (note_a_id < note_b_id),          -- canonical ordering: app must always insert min(id) as note_a_id
    UNIQUE (note_a_id, note_b_id)           -- combined with the CHECK, prevents (a,b)/(b,a) duplicate rows
);

CREATE INDEX idx_dup_candidates_status ON duplicate_candidates(status);
CREATE INDEX idx_dup_candidates_note_a ON duplicate_candidates(note_a_id);
CREATE INDEX idx_dup_candidates_note_b ON duplicate_candidates(note_b_id);

-- Persisted MinHash signatures so the LSH index can be rebuilt on process restart
-- without re-hashing every note in the vault (datasketch's MinHashLSH itself is in-process/ephemeral).
CREATE TABLE note_minhash_signatures (
    note_id      INTEGER PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    num_perm     INTEGER NOT NULL,     -- permutation count used to build the signature
    signature    BLOB NOT NULL,        -- serialized MinHash digest
    computed_at  TEXT NOT NULL
);
```

**Note on the real vault sample (§0):** the `GROK_GPT` folder alone contains multiple filename pairs like `Grok-_04.md` / `Grok-_04(1).md` and `Grok-_45.md` / `Grok-_45(1).md` — a strong, concrete real-world signal that the duplicate-detection subsystem will have immediate, non-hypothetical work to do against this exact vault once Phase 1 ships, not merely a theoretical feature.

### 2.7 Research job correlation

```sql
-- Durable, AI_BRAIN-owned correlation record. Huey's own job-store file (separate, opaque schema
-- per ADR-0004) tracks live queue/retry mechanics; this table is the domain-durable mirror AI_BRAIN
-- needs for provenance linkage and history, independent of Huey's disposable/re-derivable state.
CREATE TABLE research_jobs (
    id              INTEGER PRIMARY KEY,
    huey_task_id    TEXT NOT NULL UNIQUE,   -- correlation key into Huey's separate store; opaque string, no cross-db FK
    job_type        TEXT NOT NULL
                        CHECK (job_type IN ('research_start','reindex_start','duplicates_scan','git_backup','stale_sweep','ingestion')),
    query           TEXT,                    -- research prompt/topic; NULL for non-research job types
    status          TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
    result_note_id  INTEGER REFERENCES notes(id) ON DELETE SET NULL,   -- populated by research_commit
    error_message   TEXT,
    requested_by    TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT
);

CREATE INDEX idx_research_jobs_status ON research_jobs(status);
```

### 2.8 `chunks` (ADR-0008: original text retained independent of vectors)

```sql
CREATE TABLE chunks (
    id                       INTEGER PRIMARY KEY,        -- rowid; also FTS5 content_rowid for chunks_fts
    note_id                  INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    chunk_index              INTEGER NOT NULL,            -- 0-based position within the note
    chunk_text               TEXT NOT NULL,                -- retained independent of Qdrant per ADR-0008
    content_hash             TEXT NOT NULL,                -- sha256 of chunk_text; incremental re-embed detection
    qdrant_point_id          TEXT NOT NULL UNIQUE,          -- UUID4 string == the Qdrant point's id
    embedding_model_version  TEXT NOT NULL,                -- e.g. 'bge-m3@1'; per-chunk to allow incremental re-embed rollout
    token_count              INTEGER,                       -- optional, for context-construction budgeting
    created_at               TEXT NOT NULL,
    UNIQUE (note_id, chunk_index)
);

CREATE INDEX idx_chunks_note ON chunks(note_id);
CREATE INDEX idx_chunks_embedding_version ON chunks(embedding_model_version);
```

**Note on note deletion:** because `notes` uses a soft-delete tombstone (`deleted_at`), `ON DELETE CASCADE` from `notes` to `chunks` only fires on a genuine hard purge, not on the normal `note_delete` MCP flow. The `note_delete` workflow must explicitly `DELETE FROM chunks WHERE note_id = ?` (and delete the corresponding Qdrant points, looked up via `qdrant_point_id`, first) as an application-level step — this is a required implementation detail, flagged here so it isn't missed, mirroring ADR-0004's flagged `busy_timeout` pattern.

**Note on chunk boundaries for legacy content (§0):** for the three real content shapes found, chunking should prefer splitting on the conversational-turn headers (`# you asked`/`# {provider} response`, `### USER`/`### ASSISTANT`) or the reference-document's Setext section headers over `chonkie`'s generic recursive splitter defaults, where these structural signals are present — this is a concrete Phase 1 tuning input the sample directly provides, not a speculative optimization.

### 2.9 FTS5 keyword search — external-content tables and sync triggers

Per ADR-0004's decided pattern: external-content FTS5 tables (`content=`/`content_rowid=`, no duplicated storage) with `AFTER INSERT/UPDATE/DELETE` triggers using the documented `'delete'` special-command pattern.

```sql
-- ---- Notes (title + denormalized tags) ----
CREATE VIRTUAL TABLE notes_fts USING fts5(
    title,
    tags_text,
    content = 'notes',
    content_rowid = 'id',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, title, tags_text) VALUES (new.id, new.title, new.tags_text);
END;

CREATE TRIGGER notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, tags_text) VALUES('delete', old.id, old.title, old.tags_text);
END;

CREATE TRIGGER notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, tags_text) VALUES('delete', old.id, old.title, old.tags_text);
    INSERT INTO notes_fts(rowid, title, tags_text) VALUES (new.id, new.title, new.tags_text);
END;

-- Keep notes.tags_text (the derived column notes_fts indexes) in sync with note_tags.
-- These UPDATEs cascade into the notes_au trigger above automatically — cross-table trigger
-- chaining is always enabled in SQLite regardless of PRAGMA recursive_triggers, which only
-- gates a trigger re-firing itself on the same table.
CREATE TRIGGER note_tags_ai AFTER INSERT ON note_tags BEGIN
    UPDATE notes
    SET tags_text = (
        SELECT COALESCE(group_concat(t.name, ' '), '')
        FROM note_tags nt JOIN tags t ON t.id = nt.tag_id
        WHERE nt.note_id = new.note_id
    )
    WHERE id = new.note_id;
END;

CREATE TRIGGER note_tags_ad AFTER DELETE ON note_tags BEGIN
    UPDATE notes
    SET tags_text = (
        SELECT COALESCE(group_concat(t.name, ' '), '')
        FROM note_tags nt JOIN tags t ON t.id = nt.tag_id
        WHERE nt.note_id = old.note_id
    )
    WHERE id = old.note_id;
END;

-- ---- Chunk body text ----
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    chunk_text,
    content = 'chunks',
    content_rowid = 'id',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, chunk_text) VALUES (new.id, new.chunk_text);
END;

CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, chunk_text) VALUES('delete', old.id, old.chunk_text);
END;

CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, chunk_text) VALUES('delete', old.id, old.chunk_text);
    INSERT INTO chunks_fts(rowid, chunk_text) VALUES (new.id, new.chunk_text);
END;
```

Query pattern for ranking: `SELECT note_id, bm25(chunks_fts) AS score FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?` — `bm25()` returns *lower-is-better*, join back through `chunks.id = chunks_fts.rowid` to reach `note_id`/`qdrant_point_id`. Tombstoned notes (`deleted_at IS NOT NULL`) are filtered at the query layer (`JOIN notes ... WHERE notes.deleted_at IS NULL`), not by removing them from the FTS index — this keeps `note_history`/`note_provenance` able to resurface a deleted note's content record without a reindex.

## 3. Qdrant payload schema

Per ADR-0008: dense vectors are `BAAI/bge-m3` (1024-dim, cosine distance), sparse vectors are miniCOIL (`Qdrant/minicoil-v1`, with a documented BM25-sparse fallback), both stored as named vectors on the same point. Per ADR-0006: the collection is addressed through an **alias** (e.g. `ai_brain_chunks` → `ai_brain_chunks_bge_m3_v1`), never a hardcoded name.

**Point identity:** `qdrant point id == chunks.qdrant_point_id` (a UUID4 string) — this is the single link between the two stores, indexed and `UNIQUE` on the SQLite side.

**Payload** (attached to every point; deliberately excludes the chunk body — original text lives only in `chunks.chunk_text` per ADR-0008, keeping the vector store lean and re-embeddable without a data-migration crisis):

```json
{
  "chunk_id": 4821,
  "note_id": 512,
  "note_path": "CLAUDE/Creating project deliverables.md",
  "chunk_index": 3,
  "tags": ["rag", "embeddings", "qdrant"],
  "folder": "CLAUDE",
  "status": "active",
  "origin": "ai_generated",
  "provider": "anthropic",
  "embedding_model_version": "bge-m3@1",
  "content_hash": "b7e2...9f"
}
```

- `chunk_id`/`note_id` are redundant with the SQL lookup via `qdrant_point_id` but avoid a round-trip when only filtering/displaying search hits.
- `tags`, `folder`, `status` are denormalized from `notes`/`note_tags` specifically so Qdrant can filter on them natively (ADR-0006's "payload indexes for tags/folders/status" guidance) without a join back to SQLite mid-query.
- `embedding_model_version` lets a rolling re-embed run two model generations side by side, filtering old-vs-new during migration cutover (ties to `chunks.embedding_model_version`).
- `content_hash` allows a cheap staleness check (`payload.content_hash != chunks.content_hash` ⇒ needs re-embed) without a join.

**Payload indexes** (`create_payload_index`): `tags` (keyword), `folder` (keyword), `status` (keyword), `note_id` (integer — fast "delete all points for this note" during `note_delete`/`reindex_start`), `embedding_model_version` (keyword).

## 4. Design rationale

**Normalized tags over a JSON column.** A `note_tags` join table (rather than a JSON array column on `notes`) was chosen because: (a) `vault_search`/filtering needs indexed `WHERE tag = ?` lookups, which SQLite's JSON functions don't support without a separate generated-column index — plain relational tables get this for free; (b) tag rename/merge becomes a single `UPDATE tags SET name = ?` instead of rewriting every note's JSON blob; (c) `note_tags` cleanly drives the `tags_text` materialized column that `notes_fts` indexes. The cost — two extra tables and a join — is small and consistent with the constitution's "small composable modules" preference for plain relational design over ad hoc JSON.

**Soft-delete tombstone (`notes.deleted_at`) instead of hard delete.** Git already gives content-level recovery for a deleted vault file. The metadata store's job is to keep `note_provenance`/`note_history`/lifecycle audit queryable for a path that no longer exists in the working tree — directly serving CLAUDE.md rule 24 ("preserve provenance"). `chunks` (which have no meaning once the source text is gone from the vault) are explicitly hard-deleted by the application on `note_delete`, not cascaded automatically, since the parent `notes` row is only tombstoned, not removed.

**`status` CHECK constraint without transition-rule enforcement.** The six lifecycle states match Master Spec §11 exactly, but the master spec explicitly defers "exact states and transition rules" to later design. The DB therefore only validates state *membership*, not transition legality (no trigger blocking e.g. `archived → draft`) — encoding an unfinished policy into schema constraints would violate "do not silently redesign accepted architecture" the other direction (silently *inventing* policy that wasn't decided). `note_lifecycle_history` records whatever the business-logic layer decided; policy enforcement belongs there, not in DDL.

**Provenance modeled relationally (PROV entity/activity/agent/derivation), not as JSON.** `provenance` rows are PROV *Activities* (`activity_type`, `provider`/`model` as the *Agent*), `provenance_sources` rows are PROV *used* relations to external source entities (concretely populated for the real vault via the folder-name/first-line-URL inference in §0), `provenance_derivations` rows are PROV *wasDerivedFrom* for multi-source merges, and `supersedes_note_id`/`superseded_by_note_id` on `provenance` cover the common single-predecessor case without a join. This satisfies ADR-0003's explicit requirement to design against W3C PROV, keeps the same query-ability advantage as the tags decision, and lets `note_provenance` answer "what sources fed this note" and "what did this note supersede" with indexed joins rather than JSON parsing.

**`content_hash` on both `notes` and `chunks`.** Used two ways: (1) Master Spec §10's first duplicate signal (exact-content detection via hash lookup — indexed, intentionally *not* unique, since collisions are the point, and §0's `Grok-_04.md`/`Grok-_04(1).md`-style near-duplicates are exactly the real-world case this exists to catch); (2) a cheap "did this actually change" gate before re-chunking/re-embedding, avoiding wasted embedding-model calls on an unchanged file — serving the constitution's "do not optimize prematurely; measure before optimizing" by making the *skip* case cheap rather than adding speculative caching elsewhere.

**Canonical ordering on `duplicate_candidates` (`note_a_id < note_b_id` + `UNIQUE`).** Prevents the detector from recording both `(A,B)` and `(B,A)` as separate pending candidates, which would otherwise double work for `note_duplicates`/`duplicates_scan` and complicate merge-status tracking. The application must always insert with the smaller `id` as `note_a_id`.

**`research_jobs` as an added table (justification).** Not requested as a required table, added because Huey's own job-store schema is explicitly opaque and out of AI_BRAIN's migration-tracking business (ADR-0004's stated reason for the separate-file decision). Without a durable, AI_BRAIN-owned correlation record, `provenance.research_job_id`, `vault_status`, and the interim `job_status`/`job_cancel` MCP tools (ADR-0007) would have nothing stable to reference once a Huey task's own state is pruned or Huey's internal schema changes across a library upgrade. `research_jobs.huey_task_id` is an opaque correlation string (no cross-database FK — SQLite can't enforce one across separate files anyway), keeping the two databases' schemas fully independent per ADR-0004. `job_type` includes `'ingestion'` specifically to cover the initial bulk-import of the real vault sampled in §0.

**`note_minhash_signatures` as an added table (justification).** `datasketch`'s `MinHashLSH` index (ADR-0003's lexical-duplicate signal) is an in-process structure; without persisting the underlying signatures, every process restart would require re-hashing the entire vault before duplicate detection is usable again. Persisting the serialized signature is a small, clearly-scoped addition that makes `duplicates_scan` cheap to resume.

**Secrets (Master Spec §7 "do not store secrets in note metadata").** No column in this schema is credential-shaped. The only large free-text fields are `notes.title` and `chunks.chunk_text` — and because `chunks_fts` indexes chunk text for keyword search, anything accidentally ingested there becomes searchable, which raises (not lowers) the stakes for the pre-ingestion secret scanner flagged in `docs/SECURITY_MODEL.md`. Provider/model identifiers (`provenance.provider`, `.model`) are non-sensitive labels, not credentials — actual API keys used to call providers live in runtime config/environment, never in this database. The real vault sample (§0) contains personal/professional content (resumes, project details) rather than credential-shaped strings in what was inspected, but this is exactly the kind of personal content that makes the file-permission and embedding-inversion hardening items in `docs/SECURITY_MODEL.md` non-hypothetical for this specific user's data.

## 5. MCP tool contract support (ADR-0007)

| MCP tool | Backing tables |
|---|---|
| `note_provenance` | `provenance`, `provenance_sources`, `provenance_derivations`, `notes` (status/confidence), `note_lifecycle_history` (status context) |
| `note_history` | Primarily Git log/show (ADR-0005) — this DB is *not* the source. `note_lifecycle_history` supplements it with status-field transitions a git diff won't cleanly surface (e.g., a background stale-sweep job moving `active → stale`). |
| `note_duplicates` / `duplicates_scan` | `duplicate_candidates`, `note_minhash_signatures` (lexical signal), `notes`; semantic score computed via Qdrant cosine query, persisted back into `duplicate_candidates.semantic_score` |
| `note_merge` | `duplicate_candidates.status = 'merged'`, new `provenance` row (`activity_type = 'merge'`) with `provenance_derivations` rows for each source note, `note_lifecycle_history` (`superseded` transition on the losing note(s)) |
| `vault_search` | `notes_fts` + `chunks_fts` (keyword leg), fused with the Qdrant vector leg via `chunks.qdrant_point_id` |
| `note_related` | Qdrant semantic-neighbor query joined back through `chunks`/`notes`, plus `note_tags` overlap for a graph-ish signal |
| `vault_status` | `notes` (counts by status, `last_indexed_at`), `research_jobs` (durable job-history snapshot — live queue depth still comes from Huey directly), `chunks` (index freshness/coverage), `schema_migrations` |
| `system_diagnostics` | `schema_migrations` + `PRAGMA user_version`, plus consistency checks (e.g., `chunks` rows with no corresponding Qdrant point) |
| `note_create` / `note_update` / `note_move` | `notes`, `note_tags` (triggers keep `notes_fts` in sync automatically) |
| `note_delete` | `notes.deleted_at` set (tombstone) + `note_lifecycle_history` row; application explicitly deletes `chunks` rows and their Qdrant points (not automatic — see §2.8 note) |
| `research_start` / `research_commit` | `research_jobs` (job tracking), `provenance` (`activity_type IN ('web_research','ai_synthesis')`, `research_job_id` FK) |
| `reindex_start` | `chunks`, `notes.last_indexed_at`, `research_jobs` (`job_type = 'reindex_start'`) |
| `job_status` / `job_cancel` (interim shim) | `research_jobs`, correlated to Huey's live state via `huey_task_id` |

## 6. Deliberately out of scope here

- Huey's internal job-store schema (owned by the library, lives in a separate file, per ADR-0004).
- Lifecycle *transition rules* (which state changes are legal) — Master Spec §11 explicitly defers this; only state membership is enforced here.
- Exact chunking boundaries/sizes (`chonkie` configuration, ADR-0003) — orthogonal to storage shape, though §0/§2.8 give concrete Phase 1 tuning input from the real vault sample.
- Qdrant collection creation parameters beyond payload/vector shape (HNSW tuning, sharding) — ADR-0006's deployment concern, not the data model.
- The full folder-name → provider mapping table and turn-header parsing rules from §0 — these belong in the ingestion module's own design/implementation, not in schema DDL; they are referenced here only to explain why certain columns (`provider`, `provenance_sources.url`) are nullable and derived rather than required inputs.

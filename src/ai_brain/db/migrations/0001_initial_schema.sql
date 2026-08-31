-- Initial schema. Verbatim from docs/DATA_MODEL.md §2 (accepted, ADR-0004).
-- Applying this migration is the first real population of that design.

CREATE TABLE schema_migrations (
    version     INTEGER PRIMARY KEY,
    filename    TEXT NOT NULL,
    checksum    TEXT NOT NULL,
    applied_at  TEXT NOT NULL
);

CREATE TABLE notes (
    id               INTEGER PRIMARY KEY,
    path             TEXT NOT NULL UNIQUE,
    title            TEXT NOT NULL,
    origin           TEXT NOT NULL
                         CHECK (origin IN ('human','ai_generated','web_research','imported','merged')),
    provider         TEXT,
    model            TEXT,
    folder           TEXT,
    status           TEXT NOT NULL DEFAULT 'draft'
                         CHECK (status IN ('draft','active','verified','stale','superseded','archived')),
    confidence       REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    content_hash     TEXT NOT NULL,
    chunk_count      INTEGER NOT NULL DEFAULT 0,
    tags_text        TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    last_indexed_at  TEXT,
    deleted_at       TEXT
);

CREATE INDEX idx_notes_status        ON notes(status);
CREATE INDEX idx_notes_content_hash  ON notes(content_hash);
CREATE INDEX idx_notes_deleted_at    ON notes(deleted_at);
CREATE INDEX idx_notes_folder        ON notes(folder);

CREATE TABLE tags (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL
);

CREATE TABLE note_tags (
    note_id  INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
    PRIMARY KEY (note_id, tag_id)
);

CREATE INDEX idx_note_tags_tag ON note_tags(tag_id);

CREATE TABLE research_jobs (
    id              INTEGER PRIMARY KEY,
    huey_task_id    TEXT NOT NULL UNIQUE,
    job_type        TEXT NOT NULL
                        CHECK (job_type IN ('research_start','reindex_start','duplicates_scan','git_backup','stale_sweep','ingestion')),
    query           TEXT,
    status          TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
    result_note_id  INTEGER REFERENCES notes(id) ON DELETE SET NULL,
    error_message   TEXT,
    requested_by    TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT
);

CREATE INDEX idx_research_jobs_status ON research_jobs(status);

CREATE TABLE provenance (
    id                     INTEGER PRIMARY KEY,
    note_id                INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    activity_type          TEXT NOT NULL
                               CHECK (activity_type IN (
                                   'ingested','web_research','ai_synthesis','summarization',
                                   'human_edit','merge','split','reindex_only','migration'
                               )),
    provider               TEXT,
    model                  TEXT,
    human_edited           INTEGER NOT NULL DEFAULT 0 CHECK (human_edited IN (0,1)),
    research_job_id        INTEGER REFERENCES research_jobs(id) ON DELETE SET NULL,
    supersedes_note_id     INTEGER REFERENCES notes(id) ON DELETE SET NULL,
    superseded_by_note_id  INTEGER REFERENCES notes(id) ON DELETE SET NULL,
    transformation_notes   TEXT,
    occurred_at            TEXT NOT NULL,
    recorded_at            TEXT NOT NULL
);

CREATE INDEX idx_provenance_note        ON provenance(note_id);
CREATE INDEX idx_provenance_supersedes  ON provenance(supersedes_note_id);
CREATE INDEX idx_provenance_superseded  ON provenance(superseded_by_note_id);

CREATE TABLE provenance_sources (
    id             INTEGER PRIMARY KEY,
    provenance_id  INTEGER NOT NULL REFERENCES provenance(id) ON DELETE CASCADE,
    url            TEXT NOT NULL,
    title          TEXT,
    accessed_at    TEXT
);

CREATE INDEX idx_provenance_sources_provenance ON provenance_sources(provenance_id);
CREATE INDEX idx_provenance_sources_url        ON provenance_sources(url);

CREATE TABLE provenance_derivations (
    provenance_id   INTEGER NOT NULL REFERENCES provenance(id) ON DELETE CASCADE,
    source_note_id  INTEGER NOT NULL REFERENCES notes(id)      ON DELETE CASCADE,
    PRIMARY KEY (provenance_id, source_note_id)
);

CREATE TABLE note_lifecycle_history (
    id           INTEGER PRIMARY KEY,
    note_id      INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    from_status  TEXT,
    to_status    TEXT NOT NULL
                     CHECK (to_status IN ('draft','active','verified','stale','superseded','archived')),
    reason       TEXT,
    changed_by   TEXT,
    changed_at   TEXT NOT NULL
);

CREATE INDEX idx_lifecycle_history_note ON note_lifecycle_history(note_id, changed_at);

CREATE TABLE duplicate_candidates (
    id                   INTEGER PRIMARY KEY,
    note_a_id            INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    note_b_id            INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    detection_method     TEXT NOT NULL
                             CHECK (detection_method IN (
                                 'content_hash','minhash_lsh','cosine_similarity','metadata_match','combined'
                             )),
    lexical_score        REAL,
    semantic_score       REAL,
    metadata_match_score REAL,
    combined_score       REAL,
    status               TEXT NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending','confirmed','rejected','merged')),
    detected_at          TEXT NOT NULL,
    resolved_at          TEXT,
    resolved_by          TEXT,
    resolution_note      TEXT,
    CHECK (note_a_id < note_b_id),
    UNIQUE (note_a_id, note_b_id)
);

CREATE INDEX idx_dup_candidates_status ON duplicate_candidates(status);
CREATE INDEX idx_dup_candidates_note_a ON duplicate_candidates(note_a_id);
CREATE INDEX idx_dup_candidates_note_b ON duplicate_candidates(note_b_id);

CREATE TABLE note_minhash_signatures (
    note_id      INTEGER PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    num_perm     INTEGER NOT NULL,
    signature    BLOB NOT NULL,
    computed_at  TEXT NOT NULL
);

CREATE TABLE chunks (
    id                       INTEGER PRIMARY KEY,
    note_id                  INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    chunk_index              INTEGER NOT NULL,
    chunk_text               TEXT NOT NULL,
    content_hash             TEXT NOT NULL,
    qdrant_point_id          TEXT NOT NULL UNIQUE,
    embedding_model_version  TEXT NOT NULL,
    token_count              INTEGER,
    created_at               TEXT NOT NULL,
    UNIQUE (note_id, chunk_index)
);

CREATE INDEX idx_chunks_note ON chunks(note_id);
CREATE INDEX idx_chunks_embedding_version ON chunks(embedding_model_version);

-- ---- FTS5 external-content tables and sync triggers (DATA_MODEL.md §2.9) ----

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

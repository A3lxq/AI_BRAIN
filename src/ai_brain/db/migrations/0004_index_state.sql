-- notes.index_state / last_index_error. Per docs/EVENT_MODEL.md §4.1 and
-- docs/design/indexing-pipeline.md §2.1 (Phase 2's deliberately deferred item).

ALTER TABLE notes ADD COLUMN index_state TEXT NOT NULL DEFAULT 'stale'
    CHECK (index_state IN ('stale', 'current', 'failed'));

ALTER TABLE notes ADD COLUMN last_index_error TEXT;

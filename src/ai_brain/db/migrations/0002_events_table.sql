-- events table. Verbatim from docs/EVENT_MODEL.md §2.1 (accepted, ADR-0010).

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

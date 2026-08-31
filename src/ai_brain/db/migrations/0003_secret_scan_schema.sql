-- Secret-scan schema. Verbatim from docs/adr/0011-secret-scan-schema.md (accepted).

ALTER TABLE notes ADD COLUMN secret_scan_status TEXT NOT NULL DEFAULT 'clean'
    CHECK (secret_scan_status IN ('clean', 'flagged', 'scan_error'));

CREATE TABLE note_secret_findings (
    id              INTEGER PRIMARY KEY,
    note_id         INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    plugin_type     TEXT NOT NULL,
    line_number     INTEGER NOT NULL,
    confidence      TEXT NOT NULL CHECK (confidence IN ('high', 'low')),
    secret_hash     TEXT NOT NULL,
    redacted        INTEGER NOT NULL DEFAULT 0 CHECK (redacted IN (0,1)),
    detected_at     TEXT NOT NULL
);

CREATE INDEX idx_secret_findings_note ON note_secret_findings(note_id);
CREATE INDEX idx_secret_findings_hash ON note_secret_findings(secret_hash);

CREATE TABLE secret_scan_allowlist (
    id                  INTEGER PRIMARY KEY,
    finding_fingerprint TEXT NOT NULL UNIQUE,
    note_path           TEXT NOT NULL,
    plugin_type         TEXT NOT NULL,
    reason              TEXT NOT NULL,
    allowlisted_by      TEXT NOT NULL,
    allowlisted_at      TEXT NOT NULL
);

# ADR-0011: Secret-Scan Schema (Pre-Ingestion Secret Scanning Data Model)

- **ID:** ADR-0011
- **Title:** Secret-Scan Schema (Pre-Ingestion Secret Scanning Data Model)
- **Status:** Accepted
- **Date proposed:** 2026-08-27
- **Date accepted:** 2026-08-27
- **Depends on:** ADR-0003 (RAG orchestration/indexing pipeline), ADR-0004 (SQLite access layer), ADR-0005 (Git automation — pre-commit gitleaks, a distinct, separate control), `docs/DATA_MODEL.md` (existing `notes` schema and `index_state` precedent), `docs/design/pre-ingestion-secret-scanning.md` (full design rationale — this ADR formalizes its §6/§9 schema and mechanism decisions)

## Context

`docs/SECURITY_MODEL.md`'s P0 remediation item #6 required designing a pre-ingestion secret scanner for vault content — distinct from ADR-0005's pre-commit `gitleaks` hook, which only ever sees ATHENA AI-BRAIN's own software repository, never vault content. `docs/design/pre-ingestion-secret-scanning.md` produced a complete design: scan every note (whole-file, once, before chunking) using `detect-secrets` invoked in-process; on a high-confidence finding, redact the matched span before it is chunked/embedded/stored and flag the note for review; on a low-confidence (entropy-based) finding, index normally and record the finding without redaction; allow a human to allowlist a specific finding by fingerprint after review.

This decision introduces genuinely new persistent state that no existing ADR's schema covers — `docs/DATA_MODEL.md`'s accepted `notes`/`chunks`/`provenance` tables have no concept of a secret-scan result. Per CLAUDE.md rule 7 ("every significant technical decision gets an ADR") and following the precedent ADR-0010 already set for the `events` table (new infrastructure discovered during a design pass gets its own ADR rather than being silently folded into an existing one), this schema addition needs its own decision record before implementation.

## Decision

**Accepted:** Add one new column to the existing `notes` table and two new tables to ATHENA AI-BRAIN's metadata SQLite database (`ai_brain.db`, per ADR-0004 — not Huey's separate job-store file), as designed in `docs/design/pre-ingestion-secret-scanning.md` §6:

```sql
-- Added to the existing notes table (docs/DATA_MODEL.md §2.2), orthogonal to
-- notes.status (content lifecycle) and notes.index_state (indexing health,
-- per docs/EVENT_MODEL.md §4.1's precedent for adding an orthogonal field
-- rather than overloading an existing one):
ALTER TABLE notes ADD COLUMN secret_scan_status TEXT NOT NULL DEFAULT 'clean'
    CHECK (secret_scan_status IN ('clean', 'flagged', 'scan_error'));

CREATE TABLE note_secret_findings (
    id              INTEGER PRIMARY KEY,
    note_id         INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    plugin_type     TEXT NOT NULL,        -- e.g. "AWSKeyDetector", "Base64HighEntropyString"
    line_number     INTEGER NOT NULL,     -- 1-based, against the raw on-disk file at scan time
    confidence      TEXT NOT NULL CHECK (confidence IN ('high', 'low')),
    secret_hash     TEXT NOT NULL,        -- detect-secrets' own hashed_secret; NEVER the raw value
    redacted        INTEGER NOT NULL DEFAULT 0 CHECK (redacted IN (0,1)),
    detected_at     TEXT NOT NULL
);

CREATE INDEX idx_secret_findings_note ON note_secret_findings(note_id);
CREATE INDEX idx_secret_findings_hash ON note_secret_findings(secret_hash);

CREATE TABLE secret_scan_allowlist (
    id                  INTEGER PRIMARY KEY,
    finding_fingerprint TEXT NOT NULL UNIQUE,  -- matches note_secret_findings.secret_hash
    note_path           TEXT NOT NULL,          -- vault-relative path, for human-readable audit
    plugin_type         TEXT NOT NULL,
    reason              TEXT NOT NULL,          -- required, never optional — see Rationale
    allowlisted_by      TEXT NOT NULL,
    allowlisted_at      TEXT NOT NULL
);
```

Two small MCP tools are added to expose this state per the design's §6: `secret_findings_list(status="flagged")` (read-only) and `secret_finding_resolve(finding_id, resolution, reason)` (mutating, touches only this schema, not vault content). `vault_status`/`note_provenance` (ADR-0007) are extended to surface `secret_scan_status` per-note, matching how they already surface `index_state`.

The maintainer reviewed the design and accepted this ADR as proposed on 2026-08-27.

## Alternatives Considered

| Option | Verdict |
|---|---|
| Overload `notes.status` (the content-lifecycle field) to also carry secret-scan state | Rejected — `docs/EVENT_MODEL.md` §4.1 already established the precedent of adding an orthogonal field (`index_state`) rather than conflating two independent concerns into one enum; a note can legitimately be `active` and `flagged` simultaneously, which a single shared field can't represent cleanly. |
| Store findings as a JSON blob on the `notes` row instead of a separate table | Rejected — the design's allowlist mechanism (§5 of the design doc) requires per-finding fingerprint lookups and independent lifecycle (allowlisted vs. not), which needs indexed, queryable rows, not an opaque blob; this mirrors `docs/DATA_MODEL.md`'s own rationale for using `note_tags` over a JSON tags column. |
| Fold the allowlist into ADR-0005's existing `.gitleaks.toml` allowlist mechanism | Rejected — the design doc is explicit that these must stay disjoint: one governs "safe to commit to ATHENA AI-BRAIN's own code repo," the other governs "safe to index as vault content." Conflating them would let a decision about ATHENA AI-BRAIN's source code silently bless vault content, or vice versa. |
| Store the raw secret value (not just a hash) for easier human review | Rejected — `detect-secrets`' own `hashed_secret` is used specifically so this schema never persists a second copy of the actual secret; a reviewer works from `plugin_type`/`line_number`/`note_path` to go look at the (already redacted, in the high-confidence case) note directly, not from a stored plaintext value. |

## Rationale

1. **Orthogonality matches the project's own established pattern.** `index_state` (ADR-0010's companion design in `docs/EVENT_MODEL.md`) and `secret_scan_status` are both "operational health" facts about a note, independent of its content-lifecycle `status` — treating them as separate, indexed fields rather than overloading `status` keeps each concern independently queryable and testable.
2. **A separate findings table is required by the design's own on-detection behavior** (`docs/design/pre-ingestion-secret-scanning.md` §4.2's confidence-tiered redact-and-flag policy) — a note can have multiple findings at different confidence levels, each independently resolvable, which a single-column status cannot represent.
3. **`reason` is `NOT NULL` on the allowlist table by deliberate design choice**, not an oversight — per the design doc's §5, "every allowlist decision is a recorded, auditable judgment call (CLAUDE.md rule 24), not a silent toggle." Making the column nullable would silently permit exactly the un-auditable bypass the design was written to prevent.
4. **Fingerprint-scoping (`secret_hash`/`finding_fingerprint` as the join key, not `note_id`+`line_number`)** is what gives the allowlist its "can't hide a new secret behind an old allowlisted one" property (design doc §5) — a schema keyed on position instead of content would break this guarantee silently if a file were edited.
5. **This lives in `ai_brain.db`, not Huey's job-store file**, for the same reason ADR-0004 and ADR-0010 already separated those two databases: this is durable, provenance-adjacent knowledge-store state (CLAUDE.md rule 24), not disposable job-queue state.

## Consequences

- The migration runner (ADR-0004's `PRAGMA user_version` + numbered `.sql` files) gains one new migration adding the column and two tables — this is now a concrete Phase 1 implementation item, sequenced after the base `notes`/`chunks` schema migrations.
- `note_secret_findings` rows are hard-deleted via `ON DELETE CASCADE` when their parent `notes` row is hard-deleted (consistent with `docs/DATA_MODEL.md`'s existing `chunks` cascade behavior) — but note that `notes` itself uses a soft-delete tombstone (`deleted_at`) per `docs/DATA_MODEL.md` §4, so findings for a merely-tombstoned note remain queryable, matching that same rationale (Git already gives content-level recovery; the metadata store's job is to keep history queryable).
- `secret_scan_allowlist` is intentionally **not** foreign-keyed to `notes.id` — it's keyed purely on the fingerprint, so an allowlist entry survives a note being moved, renamed, or even deleted and recreated, as long as the exact secret value recurs; `note_path` is stored only for human-readable audit display, not as a join key.
- The two new MCP tools (`secret_findings_list`, `secret_finding_resolve`) must be added to ADR-0007's tool contract table in a follow-up amendment or cross-reference — this ADR does not itself amend ADR-0007, consistent with keeping each ADR's own decision scope narrow.
- Implementing `docs/design/pre-ingestion-secret-scanning.md` is blocked on this ADR's acceptance, since the design's interfaces (§6 of that document) assume this schema exists.

## References

See `docs/design/pre-ingestion-secret-scanning.md` (full design, §6 interfaces, §9 security considerations) and its own References section for the complete primary-source citation list (Yelp/detect-secrets, gitleaks, trufflehog documentation, all checked 2026-08-27).

## Open Questions

Resolved: the maintainer accepted this ADR as proposed on 2026-08-27, with no modifications requested.

Remaining open item, carried forward as an implementation-time decision: should `secret_findings_list`/`secret_finding_resolve` be added to ADR-0007's tool table now, or in a separate, later amendment once Phase 1 implementation reaches this feature? Recommend a lightweight cross-reference note in ADR-0007 now, full tool-table entry when implemented.

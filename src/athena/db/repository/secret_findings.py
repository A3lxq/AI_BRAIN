"""Repository functions for `note_secret_findings`/`secret_scan_allowlist`
(docs/adr/0011-secret-scan-schema.md).

Added during the vault-ingestion integration task (not part of the original
narrow repository-layer slice, docs/design/migration-runner-and-vault-
ingestion.md §2.2) because `athena.vault.ingest` is the first real caller
that needs to persist scan findings against a real note id.
"""

from __future__ import annotations

import aiosqlite

__all__ = ["insert_finding", "get_allowlisted_hashes", "delete_findings_for_note"]


async def delete_findings_for_note(conn: aiosqlite.Connection, note_id: int) -> None:
    """Clear a note's prior findings before recording a fresh scan's results --
    `note_secret_findings` reflects the *current* scan, not finding history
    (mirrors `notes.secret_scan_status` itself being overwritten, not
    appended, on each re-ingestion)."""
    await conn.execute("DELETE FROM note_secret_findings WHERE note_id = ?", (note_id,))
    await conn.commit()


async def insert_finding(
    conn: aiosqlite.Connection,
    *,
    note_id: int,
    plugin_type: str,
    line_number: int,
    confidence: str,
    secret_hash: str,
    redacted: bool,
    detected_at: str,
) -> int:
    cursor = await conn.execute(
        "INSERT INTO note_secret_findings "
        "(note_id, plugin_type, line_number, confidence, secret_hash, redacted, detected_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            note_id,
            plugin_type,
            line_number,
            confidence,
            secret_hash,
            1 if redacted else 0,
            detected_at,
        ),
    )
    await conn.commit()
    finding_id = cursor.lastrowid
    if finding_id is None:
        raise RuntimeError("INSERT INTO note_secret_findings did not yield a rowid")
    return finding_id


async def get_allowlisted_hashes(conn: aiosqlite.Connection) -> frozenset[str]:
    """Every `finding_fingerprint` currently allowlisted (ADR-0011 §5) --
    fed into `scan_note_for_secrets(..., allowlist=...)` so a previously
    reviewed finding is never re-flagged/re-redacted."""
    cursor = await conn.execute("SELECT finding_fingerprint FROM secret_scan_allowlist")
    rows = await cursor.fetchall()
    return frozenset(row[0] for row in rows)

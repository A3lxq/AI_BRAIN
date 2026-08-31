"""Repository functions for `note_lifecycle_history` (docs/DATA_MODEL.md §2.5)."""

from __future__ import annotations

import aiosqlite


async def record_transition(
    conn: aiosqlite.Connection,
    *,
    note_id: int,
    from_status: str | None,
    to_status: str,
    reason: str | None,
    changed_by: str | None,
    changed_at: str,
) -> None:
    await conn.execute(
        "INSERT INTO note_lifecycle_history "
        "(note_id, from_status, to_status, reason, changed_by, changed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (note_id, from_status, to_status, reason, changed_by, changed_at),
    )
    await conn.commit()

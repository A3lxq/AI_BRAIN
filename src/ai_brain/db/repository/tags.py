"""Repository functions for `tags`/`note_tags` (docs/DATA_MODEL.md §2.3).

Never writes `notes.tags_text` directly -- the FTS-sync triggers already in
migration 0001 (`note_tags_ai`/`note_tags_ad`) maintain that derived column
automatically whenever a `note_tags` row is inserted or deleted.
"""

from __future__ import annotations

import aiosqlite


async def get_or_create(conn: aiosqlite.Connection, name: str, display_name: str) -> int:
    cursor = await conn.execute("SELECT id FROM tags WHERE name = ?", (name,))
    row = await cursor.fetchone()
    if row is not None:
        return int(row[0])

    cursor = await conn.execute(
        "INSERT INTO tags (name, display_name) VALUES (?, ?)", (name, display_name)
    )
    await conn.commit()
    tag_id = cursor.lastrowid
    if tag_id is None:
        raise RuntimeError("INSERT INTO tags did not yield a rowid")
    return tag_id


async def attach(conn: aiosqlite.Connection, note_id: int, tag_id: int) -> None:
    await conn.execute(
        "INSERT OR IGNORE INTO note_tags (note_id, tag_id) VALUES (?, ?)", (note_id, tag_id)
    )
    await conn.commit()


async def detach(conn: aiosqlite.Connection, note_id: int, tag_id: int) -> None:
    await conn.execute(
        "DELETE FROM note_tags WHERE note_id = ? AND tag_id = ?", (note_id, tag_id)
    )
    await conn.commit()

"""Repository functions for the `chunks` table (docs/DATA_MODEL.md §2.8).

Out of the original Phase 2 repository-layer scope (that task's own file
excluded this table, since it belongs to the indexing pipeline, Phase 3).
"""

from __future__ import annotations

import aiosqlite

__all__ = ["insert", "delete_for_note"]


async def insert(
    conn: aiosqlite.Connection,
    *,
    note_id: int,
    chunk_index: int,
    chunk_text: str,
    content_hash: str,
    qdrant_point_id: str,
    embedding_model_version: str,
    token_count: int | None,
    created_at: str,
) -> int:
    cursor = await conn.execute(
        "INSERT INTO chunks (note_id, chunk_index, chunk_text, content_hash, "
        "qdrant_point_id, embedding_model_version, token_count, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            note_id,
            chunk_index,
            chunk_text,
            content_hash,
            qdrant_point_id,
            embedding_model_version,
            token_count,
            created_at,
        ),
    )
    await conn.commit()
    chunk_id = cursor.lastrowid
    if chunk_id is None:
        raise RuntimeError("INSERT INTO chunks did not yield a rowid")
    return chunk_id


async def delete_for_note(conn: aiosqlite.Connection, note_id: int) -> None:
    """Hard-delete every chunk row for `note_id` -- chunks have no meaning
    once the source text they were derived from is being re-indexed, per
    DATA_MODEL.md §2.8's note on note_delete's equivalent application-level
    (not cascade) chunk deletion. The chunks_fts trigger (migration 0001)
    keeps the FTS index in sync automatically."""
    await conn.execute("DELETE FROM chunks WHERE note_id = ?", (note_id,))
    await conn.commit()

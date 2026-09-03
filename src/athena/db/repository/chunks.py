"""Repository functions for the `chunks` table (docs/DATA_MODEL.md §2.8).

Out of the original Phase 2 repository-layer scope (that task's own file
excluded this table, since it belongs to the indexing pipeline, Phase 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiosqlite

__all__ = [
    "insert",
    "delete_for_note",
    "ChunkRow",
    "get_by_ids",
    "get_first_chunk_id_for_note",
]

_COLUMNS = (
    "id, note_id, chunk_index, chunk_text, content_hash, qdrant_point_id, "
    "embedding_model_version, token_count, created_at"
)


@dataclass(frozen=True)
class ChunkRow:
    id: int
    note_id: int
    chunk_index: int
    chunk_text: str
    content_hash: str
    qdrant_point_id: str
    embedding_model_version: str
    token_count: int | None
    created_at: str


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


def _row_to_chunk(row: Any) -> ChunkRow:
    return ChunkRow(
        id=row[0],
        note_id=row[1],
        chunk_index=row[2],
        chunk_text=row[3],
        content_hash=row[4],
        qdrant_point_id=row[5],
        embedding_model_version=row[6],
        token_count=row[7],
        created_at=row[8],
    )


async def get_by_ids(conn: aiosqlite.Connection, chunk_ids: list[int]) -> list[ChunkRow]:
    """Used by reranking (design doc §2.4) and the context builder (§2.5) to
    load the retained original chunk text for the fusion's top-N candidates
    -- retrieving text independent of vectors is ADR-0008's own stated
    rationale for the `chunks` table existing at all."""
    if not chunk_ids:
        return []
    placeholders = ",".join("?" for _ in chunk_ids)
    cursor = await conn.execute(
        f"SELECT {_COLUMNS} FROM chunks WHERE id IN ({placeholders})",  # noqa: S608
        chunk_ids,
    )
    rows = await cursor.fetchall()
    return [_row_to_chunk(row) for row in rows]


async def get_first_chunk_id_for_note(conn: aiosqlite.Connection, note_id: int) -> int | None:
    """A `notes_fts` (title/tag) hit isn't itself chunk-scoped; fusion (design
    doc §2.3) maps it to this note's first chunk as a representative proxy.
    Returns None if the note has no chunks yet (not indexed, or
    index_state != 'current') -- the caller drops such a hit rather than
    fusing a nonexistent chunk_id."""
    cursor = await conn.execute(
        "SELECT id FROM chunks WHERE note_id = ? ORDER BY chunk_index LIMIT 1",
        (note_id,),
    )
    row = await cursor.fetchone()
    return None if row is None else row[0]

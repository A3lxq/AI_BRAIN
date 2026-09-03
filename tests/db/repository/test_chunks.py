from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from ai_brain.db.migrate import DEFAULT_MIGRATIONS_DIR, apply_pending_migrations
from ai_brain.db.repository import chunks as chunks_repo
from ai_brain.db.repository import notes as notes_repo


@pytest.fixture
async def conn(tmp_path: Path) -> aiosqlite.Connection:
    connection = await aiosqlite.connect(tmp_path / "test.db")
    await apply_pending_migrations(connection, DEFAULT_MIGRATIONS_DIR)
    yield connection
    await connection.close()


async def _make_note(conn: aiosqlite.Connection, path: str = "a.md") -> int:
    return await notes_repo.insert(
        conn, path=path, title="A", origin="human", provider=None,
        folder=None, content_hash="h1", created_at="t0",
    )


async def test_insert_and_get_by_ids(conn: aiosqlite.Connection) -> None:
    note_id = await _make_note(conn)
    chunk_id = await chunks_repo.insert(
        conn, note_id=note_id, chunk_index=0, chunk_text="hello world",
        content_hash="ch1", qdrant_point_id="pid-1",
        embedding_model_version="bge-m3@abc", token_count=2, created_at="t0",
    )

    rows = await chunks_repo.get_by_ids(conn, [chunk_id])

    assert len(rows) == 1
    assert rows[0].id == chunk_id
    assert rows[0].chunk_text == "hello world"
    assert rows[0].note_id == note_id


async def test_get_by_ids_empty_list_returns_empty(conn: aiosqlite.Connection) -> None:
    assert await chunks_repo.get_by_ids(conn, []) == []


async def test_get_by_ids_preserves_no_particular_order_but_returns_all(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _make_note(conn)
    id1 = await chunks_repo.insert(
        conn, note_id=note_id, chunk_index=0, chunk_text="first",
        content_hash="c1", qdrant_point_id="p1",
        embedding_model_version="v1", token_count=1, created_at="t0",
    )
    id2 = await chunks_repo.insert(
        conn, note_id=note_id, chunk_index=1, chunk_text="second",
        content_hash="c2", qdrant_point_id="p2",
        embedding_model_version="v1", token_count=1, created_at="t0",
    )

    rows = await chunks_repo.get_by_ids(conn, [id1, id2])

    assert {r.id for r in rows} == {id1, id2}


async def test_get_first_chunk_id_for_note(conn: aiosqlite.Connection) -> None:
    note_id = await _make_note(conn)
    await chunks_repo.insert(
        conn, note_id=note_id, chunk_index=1, chunk_text="second",
        content_hash="c2", qdrant_point_id="p2",
        embedding_model_version="v1", token_count=1, created_at="t0",
    )
    first_id = await chunks_repo.insert(
        conn, note_id=note_id, chunk_index=0, chunk_text="first",
        content_hash="c1", qdrant_point_id="p1",
        embedding_model_version="v1", token_count=1, created_at="t0",
    )

    result = await chunks_repo.get_first_chunk_id_for_note(conn, note_id)

    assert result == first_id


async def test_get_first_chunk_id_for_note_with_no_chunks_returns_none(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _make_note(conn)
    assert await chunks_repo.get_first_chunk_id_for_note(conn, note_id) is None

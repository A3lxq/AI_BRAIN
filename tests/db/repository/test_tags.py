"""Tests for `athena.db.repository.tags` against a real migrated SQLite file."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from athena.db.migrate import DEFAULT_MIGRATIONS_DIR, apply_pending_migrations
from athena.db.repository import notes, tags


@pytest.fixture
async def conn(tmp_path: Path) -> aiosqlite.Connection:
    connection = await aiosqlite.connect(tmp_path / "test.db")
    await apply_pending_migrations(connection, DEFAULT_MIGRATIONS_DIR)
    yield connection
    await connection.close()


async def _make_note(conn: aiosqlite.Connection, path: str = "CLAUDE/a.md") -> int:
    return await notes.insert(
        conn,
        path=path,
        title="A",
        origin="human",
        provider=None,
        folder=None,
        content_hash="h",
        created_at="2026-08-28T00:00:00+00:00",
    )


async def test_get_or_create_creates_new_tag(conn: aiosqlite.Connection) -> None:
    tag_id = await tags.get_or_create(conn, "rag", "RAG")

    cursor = await conn.execute("SELECT name, display_name FROM tags WHERE id = ?", (tag_id,))
    row = await cursor.fetchone()
    assert row == ("rag", "RAG")


async def test_get_or_create_returns_existing_id_for_same_name(
    conn: aiosqlite.Connection,
) -> None:
    first_id = await tags.get_or_create(conn, "rag", "RAG")
    second_id = await tags.get_or_create(conn, "rag", "RAG (again)")

    assert first_id == second_id
    cursor = await conn.execute("SELECT COUNT(*) FROM tags")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 1


async def test_attach_syncs_notes_tags_text_via_ddl_trigger(conn: aiosqlite.Connection) -> None:
    note_id = await _make_note(conn)
    tag_id = await tags.get_or_create(conn, "embeddings", "Embeddings")

    await tags.attach(conn, note_id, tag_id)

    row = await notes.get_by_path(conn, "CLAUDE/a.md")
    assert row is not None
    assert row.tags_text == "embeddings"


async def test_attach_is_idempotent(conn: aiosqlite.Connection) -> None:
    note_id = await _make_note(conn)
    tag_id = await tags.get_or_create(conn, "embeddings", "Embeddings")

    await tags.attach(conn, note_id, tag_id)
    await tags.attach(conn, note_id, tag_id)

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM note_tags WHERE note_id = ? AND tag_id = ?", (note_id, tag_id)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 1


async def test_detach_syncs_notes_tags_text_via_ddl_trigger(conn: aiosqlite.Connection) -> None:
    note_id = await _make_note(conn)
    tag_id = await tags.get_or_create(conn, "qdrant", "Qdrant")
    await tags.attach(conn, note_id, tag_id)

    await tags.detach(conn, note_id, tag_id)

    row = await notes.get_by_path(conn, "CLAUDE/a.md")
    assert row is not None
    assert row.tags_text == ""
    cursor = await conn.execute(
        "SELECT COUNT(*) FROM note_tags WHERE note_id = ? AND tag_id = ?", (note_id, tag_id)
    )
    count_row = await cursor.fetchone()
    assert count_row is not None
    assert count_row[0] == 0


async def test_detach_of_untracked_pair_is_a_noop(conn: aiosqlite.Connection) -> None:
    note_id = await _make_note(conn)
    tag_id = await tags.get_or_create(conn, "unused", "Unused")

    await tags.detach(conn, note_id, tag_id)  # never attached -- should not raise

    cursor = await conn.execute("SELECT COUNT(*) FROM note_tags")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 0

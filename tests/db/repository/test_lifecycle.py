"""Tests for `athena.db.repository.lifecycle` against a real migrated SQLite file."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from athena.db.migrate import DEFAULT_MIGRATIONS_DIR, apply_pending_migrations
from athena.db.repository import lifecycle, notes


@pytest.fixture
async def conn(tmp_path: Path) -> aiosqlite.Connection:
    connection = await aiosqlite.connect(tmp_path / "test.db")
    await apply_pending_migrations(connection, DEFAULT_MIGRATIONS_DIR)
    yield connection
    await connection.close()


async def _make_note(conn: aiosqlite.Connection) -> int:
    return await notes.insert(
        conn,
        path="CLAUDE/a.md",
        title="A",
        origin="human",
        provider=None,
        folder=None,
        content_hash="h",
        created_at="2026-08-28T00:00:00+00:00",
    )


async def test_record_transition_on_first_creation_allows_null_from_status(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _make_note(conn)

    await lifecycle.record_transition(
        conn,
        note_id=note_id,
        from_status=None,
        to_status="draft",
        reason="initial ingestion",
        changed_by="system",
        changed_at="2026-08-28T00:00:00+00:00",
    )

    cursor = await conn.execute(
        "SELECT note_id, from_status, to_status, reason, changed_by, changed_at "
        "FROM note_lifecycle_history WHERE note_id = ?",
        (note_id,),
    )
    row = await cursor.fetchone()
    assert row == (
        note_id,
        None,
        "draft",
        "initial ingestion",
        "system",
        "2026-08-28T00:00:00+00:00",
    )


async def test_record_transition_with_non_null_from_status(conn: aiosqlite.Connection) -> None:
    note_id = await _make_note(conn)

    await lifecycle.record_transition(
        conn,
        note_id=note_id,
        from_status="draft",
        to_status="active",
        reason="manual promotion",
        changed_by="mcp:note_update",
        changed_at="2026-08-28T01:00:00+00:00",
    )

    cursor = await conn.execute(
        "SELECT from_status, to_status FROM note_lifecycle_history WHERE note_id = ?",
        (note_id,),
    )
    row = await cursor.fetchone()
    assert row == ("draft", "active")


async def test_multiple_transitions_are_all_recorded_in_order(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _make_note(conn)

    await lifecycle.record_transition(
        conn,
        note_id=note_id,
        from_status=None,
        to_status="draft",
        reason="initial ingestion",
        changed_by="system",
        changed_at="2026-08-28T00:00:00+00:00",
    )
    await lifecycle.record_transition(
        conn,
        note_id=note_id,
        from_status="draft",
        to_status="active",
        reason="reviewed",
        changed_by="user",
        changed_at="2026-08-28T01:00:00+00:00",
    )

    cursor = await conn.execute(
        "SELECT to_status FROM note_lifecycle_history WHERE note_id = ? ORDER BY id",
        (note_id,),
    )
    rows = await cursor.fetchall()
    assert [row[0] for row in rows] == ["draft", "active"]


async def test_record_transition_with_null_reason_and_changed_by(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _make_note(conn)

    await lifecycle.record_transition(
        conn,
        note_id=note_id,
        from_status=None,
        to_status="draft",
        reason=None,
        changed_by=None,
        changed_at="2026-08-28T00:00:00+00:00",
    )

    cursor = await conn.execute(
        "SELECT reason, changed_by FROM note_lifecycle_history WHERE note_id = ?",
        (note_id,),
    )
    row = await cursor.fetchone()
    assert row == (None, None)

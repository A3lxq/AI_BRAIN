from __future__ import annotations

import aiosqlite

from athena.db.repository import notes as notes_repo
from athena.vault import lifecycle


async def test_create_note_defaults_to_draft_and_records_transition(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await lifecycle.create_note(
        conn,
        path="a.md",
        title="A",
        origin="human",
        provider=None,
        folder=None,
        content_hash="h1",
        created_at="2026-01-01T00:00:00+00:00",
        changed_by="tester",
    )

    row = await notes_repo.get_by_path(conn, "a.md")
    assert row is not None
    assert row.status == "draft"

    cursor = await conn.execute(
        "SELECT from_status, to_status, reason, changed_by "
        "FROM note_lifecycle_history WHERE note_id = ?",
        (note_id,),
    )
    history_row = await cursor.fetchone()
    assert history_row == (None, "draft", "ingested", "tester")


async def test_update_note_content_does_not_change_status(conn: aiosqlite.Connection) -> None:
    note_id = await lifecycle.create_note(
        conn,
        path="a.md",
        title="A",
        origin="human",
        provider=None,
        folder=None,
        content_hash="h1",
        created_at="t0",
        changed_by="tester",
    )
    await lifecycle.update_note_content(conn, note_id, content_hash="h2", updated_at="t1")

    row = await notes_repo.get_by_path(conn, "a.md")
    assert row is not None
    assert row.content_hash == "h2"
    assert row.status == "draft"

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM note_lifecycle_history WHERE note_id = ?", (note_id,)
    )
    assert (await cursor.fetchone())[0] == 1


async def test_move_note_updates_path(conn: aiosqlite.Connection) -> None:
    note_id = await lifecycle.create_note(
        conn,
        path="old.md",
        title="A",
        origin="human",
        provider=None,
        folder=None,
        content_hash="h1",
        created_at="t0",
        changed_by="tester",
    )
    await lifecycle.move_note(conn, note_id, new_path="new.md", updated_at="t1")

    assert await notes_repo.get_by_path(conn, "old.md") is None
    row = await notes_repo.get_by_path(conn, "new.md")
    assert row is not None
    assert row.id == note_id


async def test_delete_note_tombstones_without_changing_status(conn: aiosqlite.Connection) -> None:
    note_id = await lifecycle.create_note(
        conn,
        path="a.md",
        title="A",
        origin="human",
        provider=None,
        folder=None,
        content_hash="h1",
        created_at="t0",
        changed_by="tester",
    )
    await lifecycle.delete_note(conn, note_id, deleted_at="t1")

    row = await notes_repo.get_by_path(conn, "a.md")
    assert row is not None
    assert row.deleted_at == "t1"
    assert row.status == "draft"


async def test_transition_status_updates_both_status_and_history(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await lifecycle.create_note(
        conn,
        path="a.md",
        title="A",
        origin="human",
        provider=None,
        folder=None,
        content_hash="h1",
        created_at="t0",
        changed_by="tester",
    )
    await lifecycle.transition_status(
        conn,
        note_id,
        from_status="draft",
        to_status="active",
        reason="reviewed",
        changed_by="tester",
        changed_at="t1",
    )

    row = await notes_repo.get_by_path(conn, "a.md")
    assert row is not None
    assert row.status == "active"

    cursor = await conn.execute(
        "SELECT to_status FROM note_lifecycle_history WHERE note_id = ? ORDER BY id", (note_id,)
    )
    rows = await cursor.fetchall()
    assert [r[0] for r in rows] == ["draft", "active"]

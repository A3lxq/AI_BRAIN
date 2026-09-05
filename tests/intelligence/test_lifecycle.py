from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite

from athena.db.repository import duplicates as duplicates_repo
from athena.db.repository import notes as notes_repo
from athena.intelligence import lifecycle

OLD_TIMESTAMP = (datetime.now(UTC) - timedelta(days=400)).isoformat()
RECENT_TIMESTAMP = datetime.now(UTC).isoformat()


async def _insert_note(
    conn: aiosqlite.Connection,
    *,
    path: str,
    status: str | None = None,
    created_at: str = RECENT_TIMESTAMP,
) -> int:
    return await notes_repo.insert(
        conn,
        path=path,
        title=path,
        origin="human",
        provider=None,
        folder=None,
        content_hash=f"hash-{path}",
        created_at=created_at,
        status=status,
    )


# --- promote_on_first_index -------------------------------------------------


async def test_promote_on_first_index_transitions_draft_to_active(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _insert_note(conn, path="a.md")  # defaults to 'draft'

    await lifecycle.promote_on_first_index(conn, note_id)

    row = await notes_repo.get_by_id(conn, note_id)
    assert row is not None
    assert row.status == "active"

    cursor = await conn.execute(
        "SELECT from_status, to_status, changed_by FROM note_lifecycle_history "
        "WHERE note_id = ? ORDER BY id",
        (note_id,),
    )
    history_rows = await cursor.fetchall()
    # One row from notes_repo.insert()? No -- insert() alone doesn't write
    # lifecycle history (only athena.vault.lifecycle.create_note does), so
    # the only history row here is the one promote_on_first_index wrote.
    assert history_rows == [("draft", "active", "job:index_note")]


async def test_promote_on_first_index_is_noop_on_already_active_note(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _insert_note(conn, path="a.md", status="active")

    await lifecycle.promote_on_first_index(conn, note_id)

    row = await notes_repo.get_by_id(conn, note_id)
    assert row is not None
    assert row.status == "active"

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM note_lifecycle_history WHERE note_id = ?", (note_id,)
    )
    count_row = await cursor.fetchone()
    assert count_row is not None
    # No transition_status call at all was made -- no history row written.
    assert count_row[0] == 0


async def test_promote_on_first_index_repeated_calls_do_not_double_transition(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _insert_note(conn, path="a.md")  # 'draft'

    await lifecycle.promote_on_first_index(conn, note_id)
    await lifecycle.promote_on_first_index(conn, note_id)
    await lifecycle.promote_on_first_index(conn, note_id)

    row = await notes_repo.get_by_id(conn, note_id)
    assert row is not None
    assert row.status == "active"

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM note_lifecycle_history WHERE note_id = ?", (note_id,)
    )
    count_row = await cursor.fetchone()
    assert count_row is not None
    assert count_row[0] == 1


async def test_promote_on_first_index_is_noop_on_verified_note(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _insert_note(conn, path="a.md", status="verified")

    await lifecycle.promote_on_first_index(conn, note_id)

    row = await notes_repo.get_by_id(conn, note_id)
    assert row is not None
    assert row.status == "verified"

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM note_lifecycle_history WHERE note_id = ?", (note_id,)
    )
    count_row = await cursor.fetchone()
    assert count_row is not None
    assert count_row[0] == 0


async def test_promote_on_first_index_raises_on_unknown_note(
    conn: aiosqlite.Connection,
) -> None:
    try:
        await lifecycle.promote_on_first_index(conn, 999999)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown note_id")


# --- run_stale_sweep ---------------------------------------------------------


async def test_run_stale_sweep_flags_old_active_note(conn: aiosqlite.Connection) -> None:
    note_id = await _insert_note(
        conn, path="old.md", status="active", created_at=OLD_TIMESTAMP
    )

    summary = await lifecycle.run_stale_sweep(conn, stale_after_days=180)

    assert summary.notes_flagged == 1
    assert summary.notes_skipped_duplicate_pending == 0

    row = await notes_repo.get_by_id(conn, note_id)
    assert row is not None
    assert row.status == "stale"

    cursor = await conn.execute(
        "SELECT from_status, to_status, changed_by FROM note_lifecycle_history "
        "WHERE note_id = ?",
        (note_id,),
    )
    history_row = await cursor.fetchone()
    assert history_row == ("active", "stale", "job:stale_sweep")


async def test_run_stale_sweep_flags_old_verified_note_preserving_from_status(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _insert_note(
        conn, path="old.md", status="verified", created_at=OLD_TIMESTAMP
    )

    summary = await lifecycle.run_stale_sweep(conn, stale_after_days=180)

    assert summary.notes_flagged == 1

    row = await notes_repo.get_by_id(conn, note_id)
    assert row is not None
    assert row.status == "stale"

    cursor = await conn.execute(
        "SELECT from_status FROM note_lifecycle_history WHERE note_id = ?", (note_id,)
    )
    from_status_row = await cursor.fetchone()
    assert from_status_row == ("verified",)


async def test_run_stale_sweep_does_not_flag_recently_updated_note(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _insert_note(
        conn, path="fresh.md", status="active", created_at=RECENT_TIMESTAMP
    )

    summary = await lifecycle.run_stale_sweep(conn, stale_after_days=180)

    assert summary.notes_flagged == 0
    assert summary.notes_skipped_duplicate_pending == 0

    row = await notes_repo.get_by_id(conn, note_id)
    assert row is not None
    assert row.status == "active"


async def test_run_stale_sweep_does_not_flag_archived_note(conn: aiosqlite.Connection) -> None:
    note_id = await _insert_note(
        conn, path="archived.md", status="archived", created_at=OLD_TIMESTAMP
    )

    summary = await lifecycle.run_stale_sweep(conn, stale_after_days=180)

    assert summary.notes_flagged == 0

    row = await notes_repo.get_by_id(conn, note_id)
    assert row is not None
    assert row.status == "archived"


async def test_run_stale_sweep_skips_note_with_pending_duplicate_candidate(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _insert_note(
        conn, path="dup-a.md", status="active", created_at=OLD_TIMESTAMP
    )
    other_id = await _insert_note(
        conn, path="dup-b.md", status="active", created_at=OLD_TIMESTAMP
    )
    await duplicates_repo.upsert_candidate(
        conn,
        note_a_id=note_id,
        note_b_id=other_id,
        detection_method="content_hash",
        lexical_score=None,
        semantic_score=None,
        metadata_match_score=None,
        combined_score=1.0,
        detected_at=RECENT_TIMESTAMP,
    )

    summary = await lifecycle.run_stale_sweep(conn, stale_after_days=180)

    # Both notes are named by the same pending candidate, so both are skipped.
    assert summary.notes_flagged == 0
    assert summary.notes_skipped_duplicate_pending == 2

    row = await notes_repo.get_by_id(conn, note_id)
    assert row is not None
    assert row.status == "active"

    other_row = await notes_repo.get_by_id(conn, other_id)
    assert other_row is not None
    assert other_row.status == "active"


async def test_run_stale_sweep_flags_note_with_only_resolved_duplicate_candidate(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _insert_note(
        conn, path="dup-a.md", status="active", created_at=OLD_TIMESTAMP
    )
    other_id = await _insert_note(
        conn, path="dup-b.md", status="active", created_at=OLD_TIMESTAMP
    )
    candidate_id = await duplicates_repo.upsert_candidate(
        conn,
        note_a_id=note_id,
        note_b_id=other_id,
        detection_method="content_hash",
        lexical_score=None,
        semantic_score=None,
        metadata_match_score=None,
        combined_score=1.0,
        detected_at=RECENT_TIMESTAMP,
    )
    await duplicates_repo.update_resolution(
        conn,
        candidate_id,
        status="rejected",
        resolved_at=RECENT_TIMESTAMP,
        resolved_by="tester",
    )

    summary = await lifecycle.run_stale_sweep(conn, stale_after_days=180)

    assert summary.notes_flagged == 2
    assert summary.notes_skipped_duplicate_pending == 0

    row = await notes_repo.get_by_id(conn, note_id)
    assert row is not None
    assert row.status == "stale"

    other_row = await notes_repo.get_by_id(conn, other_id)
    assert other_row is not None
    assert other_row.status == "stale"

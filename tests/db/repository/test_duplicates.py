"""Tests for `athena.db.repository.duplicates` against a real migrated SQLite file."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from athena.db.migrate import DEFAULT_MIGRATIONS_DIR, apply_pending_migrations
from athena.db.repository import duplicates, notes


@pytest.fixture
async def conn(tmp_path: Path) -> aiosqlite.Connection:
    connection = await aiosqlite.connect(tmp_path / "test.db")
    await apply_pending_migrations(connection, DEFAULT_MIGRATIONS_DIR)
    yield connection
    await connection.close()


async def _make_note(conn: aiosqlite.Connection, path: str) -> int:
    return await notes.insert(
        conn, path=path, title=path, origin="human", provider=None,
        folder=None, content_hash=f"hash-{path}", created_at="2026-09-04T00:00:00+00:00",
    )


async def test_upsert_candidate_enforces_canonical_ordering(conn: aiosqlite.Connection) -> None:
    note_a = await _make_note(conn, "a.md")
    note_b = await _make_note(conn, "b.md")
    assert note_a < note_b

    candidate_id = await duplicates.upsert_candidate(
        conn,
        note_a_id=note_b,
        note_b_id=note_a,
        detection_method="content_hash",
        lexical_score=None,
        semantic_score=None,
        metadata_match_score=None,
        combined_score=1.0,
        detected_at="2026-09-04T00:00:00+00:00",
    )

    row = await duplicates.get_by_id(conn, candidate_id)
    assert row is not None
    assert row.note_a_id == note_a
    assert row.note_b_id == note_b


async def test_upsert_candidate_rejects_self_pair(conn: aiosqlite.Connection) -> None:
    note_a = await _make_note(conn, "a.md")

    with pytest.raises(ValueError, match="cannot be a duplicate of itself"):
        await duplicates.upsert_candidate(
            conn,
            note_a_id=note_a,
            note_b_id=note_a,
            detection_method="content_hash",
            lexical_score=None,
            semantic_score=None,
            metadata_match_score=None,
            combined_score=1.0,
            detected_at="2026-09-04T00:00:00+00:00",
        )


async def test_rescan_refreshes_a_pending_candidate(conn: aiosqlite.Connection) -> None:
    note_a = await _make_note(conn, "a.md")
    note_b = await _make_note(conn, "b.md")
    candidate_id = await duplicates.upsert_candidate(
        conn, note_a_id=note_a, note_b_id=note_b, detection_method="minhash_lsh",
        lexical_score=0.6, semantic_score=None, metadata_match_score=None,
        combined_score=0.6, detected_at="2026-09-04T00:00:00+00:00",
    )

    refreshed_id = await duplicates.upsert_candidate(
        conn, note_a_id=note_a, note_b_id=note_b, detection_method="combined",
        lexical_score=0.6, semantic_score=0.9, metadata_match_score=0.5,
        combined_score=0.9, detected_at="2026-09-04T01:00:00+00:00",
    )

    assert refreshed_id == candidate_id
    row = await duplicates.get_by_id(conn, candidate_id)
    assert row is not None
    assert row.detection_method == "combined"
    assert row.combined_score == 0.9


async def test_rescan_does_not_overwrite_a_resolved_candidate(conn: aiosqlite.Connection) -> None:
    note_a = await _make_note(conn, "a.md")
    note_b = await _make_note(conn, "b.md")
    candidate_id = await duplicates.upsert_candidate(
        conn, note_a_id=note_a, note_b_id=note_b, detection_method="minhash_lsh",
        lexical_score=0.6, semantic_score=None, metadata_match_score=None,
        combined_score=0.6, detected_at="2026-09-04T00:00:00+00:00",
    )
    await duplicates.update_resolution(
        conn, candidate_id, status="rejected", resolved_at="2026-09-04T00:30:00+00:00",
        resolved_by="user",
    )

    await duplicates.upsert_candidate(
        conn, note_a_id=note_a, note_b_id=note_b, detection_method="combined",
        lexical_score=0.6, semantic_score=0.99, metadata_match_score=0.9,
        combined_score=0.99, detected_at="2026-09-04T01:00:00+00:00",
    )

    row = await duplicates.get_by_id(conn, candidate_id)
    assert row is not None
    assert row.status == "rejected"
    assert row.detection_method == "minhash_lsh"
    assert row.combined_score == 0.6


async def test_list_by_status_orders_by_combined_score_descending(
    conn: aiosqlite.Connection,
) -> None:
    note_a = await _make_note(conn, "a.md")
    note_b = await _make_note(conn, "b.md")
    note_c = await _make_note(conn, "c.md")
    await duplicates.upsert_candidate(
        conn, note_a_id=note_a, note_b_id=note_b, detection_method="minhash_lsh",
        lexical_score=0.5, semantic_score=None, metadata_match_score=None,
        combined_score=0.5, detected_at="t0",
    )
    await duplicates.upsert_candidate(
        conn, note_a_id=note_a, note_b_id=note_c, detection_method="content_hash",
        lexical_score=None, semantic_score=None, metadata_match_score=None,
        combined_score=1.0, detected_at="t0",
    )

    pending = await duplicates.list_by_status(conn, "pending")

    assert [c.combined_score for c in pending] == [1.0, 0.5]


async def test_update_resolution_stores_all_fields(conn: aiosqlite.Connection) -> None:
    note_a = await _make_note(conn, "a.md")
    note_b = await _make_note(conn, "b.md")
    candidate_id = await duplicates.upsert_candidate(
        conn, note_a_id=note_a, note_b_id=note_b, detection_method="content_hash",
        lexical_score=None, semantic_score=None, metadata_match_score=None,
        combined_score=1.0, detected_at="t0",
    )

    await duplicates.update_resolution(
        conn, candidate_id, status="confirmed", resolved_at="t1", resolved_by="user",
        resolution_note="looks like a real duplicate",
    )

    row = await duplicates.get_by_id(conn, candidate_id)
    assert row is not None
    assert row.status == "confirmed"
    assert row.resolved_at == "t1"
    assert row.resolved_by == "user"
    assert row.resolution_note == "looks like a real duplicate"


async def test_minhash_signature_upsert_and_roundtrip(conn: aiosqlite.Connection) -> None:
    note_a = await _make_note(conn, "a.md")
    await duplicates.upsert_signature(
        conn, note_id=note_a, num_perm=128, signature=b"\x01\x02\x03", computed_at="t0"
    )

    signatures = await duplicates.list_all_signatures(conn)

    assert len(signatures) == 1
    assert signatures[0].note_id == note_a
    assert signatures[0].num_perm == 128
    assert signatures[0].signature == b"\x01\x02\x03"


async def test_minhash_signature_upsert_replaces_existing(conn: aiosqlite.Connection) -> None:
    note_a = await _make_note(conn, "a.md")
    await duplicates.upsert_signature(
        conn, note_id=note_a, num_perm=128, signature=b"\x01", computed_at="t0"
    )
    await duplicates.upsert_signature(
        conn, note_id=note_a, num_perm=128, signature=b"\x02", computed_at="t1"
    )

    signatures = await duplicates.list_all_signatures(conn)

    assert len(signatures) == 1
    assert signatures[0].signature == b"\x02"

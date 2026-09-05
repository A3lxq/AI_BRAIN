from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from qdrant_client import QdrantClient

from athena.db.repository import duplicates as duplicates_repo
from athena.db.repository import notes as notes_repo
from athena.db.repository import provenance as provenance_repo
from athena.intelligence.merge import (
    MergeResult,
    list_pending_duplicates,
    merge_notes,
    resolve_duplicate,
)
from athena.safety.paths import VaultRoot


def _write(vault_dir: Path, relative: str, text: str) -> Path:
    path = vault_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


async def _make_note(conn: aiosqlite.Connection, path: str, content_hash: str = "h") -> int:
    return await notes_repo.insert(
        conn, path=path, title=path, origin="human", provider=None,
        folder=None, content_hash=content_hash, created_at="2026-09-04T00:00:00+00:00",
    )


async def _make_confirmed_candidate(
    conn: aiosqlite.Connection, note_a_id: int, note_b_id: int
) -> int:
    candidate_id = await duplicates_repo.upsert_candidate(
        conn, note_a_id=note_a_id, note_b_id=note_b_id, detection_method="content_hash",
        lexical_score=None, semantic_score=None, metadata_match_score=None,
        combined_score=1.0, detected_at="t0",
    )
    await resolve_duplicate(conn, candidate_id, resolution="confirmed", resolved_by="user")
    return candidate_id


async def test_list_pending_duplicates_excludes_deleted_notes(conn: aiosqlite.Connection) -> None:
    note_a = await _make_note(conn, "a.md")
    note_b = await _make_note(conn, "b.md")
    await duplicates_repo.upsert_candidate(
        conn, note_a_id=note_a, note_b_id=note_b, detection_method="content_hash",
        lexical_score=None, semantic_score=None, metadata_match_score=None,
        combined_score=1.0, detected_at="t0",
    )

    pending = await list_pending_duplicates(conn)
    assert len(pending) == 1

    await notes_repo.soft_delete(conn, note_b, deleted_at="t1")

    pending_after_delete = await list_pending_duplicates(conn)
    assert pending_after_delete == []


async def test_resolve_duplicate_rejects_invalid_resolution(conn: aiosqlite.Connection) -> None:
    note_a = await _make_note(conn, "a.md")
    note_b = await _make_note(conn, "b.md")
    candidate_id = await duplicates_repo.upsert_candidate(
        conn, note_a_id=note_a, note_b_id=note_b, detection_method="content_hash",
        lexical_score=None, semantic_score=None, metadata_match_score=None,
        combined_score=1.0, detected_at="t0",
    )

    with pytest.raises(ValueError, match="resolution must be"):
        await resolve_duplicate(conn, candidate_id, resolution="merged", resolved_by="user")


async def test_merge_notes_rejects_a_pending_candidate(
    conn: aiosqlite.Connection, vault_root: VaultRoot, vault_dir: Path, qdrant_client: QdrantClient
) -> None:
    _write(vault_dir, "a.md", "content a")
    _write(vault_dir, "b.md", "content b")
    note_a = await _make_note(conn, "a.md")
    note_b = await _make_note(conn, "b.md")
    await duplicates_repo.upsert_candidate(
        conn, note_a_id=note_a, note_b_id=note_b, detection_method="content_hash",
        lexical_score=None, semantic_score=None, metadata_match_score=None,
        combined_score=1.0, detected_at="t0",
    )

    with pytest.raises(ValueError, match="no 'confirmed'"):
        await merge_notes(
            conn, qdrant_client, vault_root,
            keep_note_id=note_a, absorb_note_id=note_b, merged_by="user",
        )


async def test_merge_notes_combines_content_and_updates_state(
    conn: aiosqlite.Connection, vault_root: VaultRoot, vault_dir: Path, qdrant_client: QdrantClient
) -> None:
    _write(vault_dir, "keep.md", "This is the kept note's original content.")
    _write(vault_dir, "absorb.md", "This is the absorbed note's original content.")
    keep_id = await _make_note(conn, "keep.md", content_hash="hash-keep")
    absorb_id = await _make_note(conn, "absorb.md", content_hash="hash-absorb")
    candidate_id = await _make_confirmed_candidate(conn, keep_id, absorb_id)

    result = await merge_notes(
        conn, qdrant_client, vault_root,
        keep_note_id=keep_id, absorb_note_id=absorb_id, merged_by="user",
    )

    assert isinstance(result, MergeResult)
    assert result.kept_note_id == keep_id
    assert result.absorbed_note_id == absorb_id

    merged_text = (vault_dir / "keep.md").read_text(encoding="utf-8")
    assert "kept note's original content" in merged_text
    assert "absorbed note's original content" in merged_text
    assert "## Merged from absorb.md" in merged_text

    keep_row = await notes_repo.get_by_id(conn, keep_id)
    assert keep_row is not None
    assert keep_row.content_hash != "hash-keep"

    absorb_row = await notes_repo.get_by_id(conn, absorb_id)
    assert absorb_row is not None
    assert absorb_row.deleted_at is not None
    assert absorb_row.status == "superseded"

    candidate = await duplicates_repo.get_by_id(conn, candidate_id)
    assert candidate is not None
    assert candidate.status == "merged"

    lineage = await provenance_repo.get_lineage(conn, keep_id)
    assert [edge.note_id for edge in lineage.ancestors] == [absorb_id]


async def test_merge_notes_survives_reindex_failure(
    conn: aiosqlite.Connection, vault_root: VaultRoot, vault_dir: Path
) -> None:
    """Qdrant unreachable during the post-merge re-index must not abort the
    merge itself -- the content merge and lifecycle/provenance bookkeeping
    still complete (docs/design/knowledge-intelligence.md's merge-ordering
    discussion)."""
    _write(vault_dir, "keep.md", "keep content")
    _write(vault_dir, "absorb.md", "absorb content")
    keep_id = await _make_note(conn, "keep.md")
    absorb_id = await _make_note(conn, "absorb.md")
    await _make_confirmed_candidate(conn, keep_id, absorb_id)
    unreachable_qdrant = QdrantClient(url="http://127.0.0.1:1")

    result = await merge_notes(
        conn, unreachable_qdrant, vault_root,
        keep_note_id=keep_id, absorb_note_id=absorb_id, merged_by="user",
    )

    assert result.kept_note_id == keep_id
    merged_text = (vault_dir / "keep.md").read_text(encoding="utf-8")
    assert "absorb content" in merged_text
    absorb_row = await notes_repo.get_by_id(conn, absorb_id)
    assert absorb_row is not None
    assert absorb_row.deleted_at is not None


async def test_merge_notes_rejects_unknown_note_ids(
    conn: aiosqlite.Connection, vault_root: VaultRoot, vault_dir: Path, qdrant_client: QdrantClient
) -> None:
    _write(vault_dir, "a.md", "content a")
    note_a = await _make_note(conn, "a.md")

    with pytest.raises(ValueError, match="no 'confirmed'"):
        await merge_notes(
            conn, qdrant_client, vault_root,
            keep_note_id=note_a, absorb_note_id=999_999, merged_by="user",
        )

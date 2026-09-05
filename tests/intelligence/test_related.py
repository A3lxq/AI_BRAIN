from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest
from huey import SqliteHuey
from qdrant_client import QdrantClient

from athena.db.repository import chunks as chunks_repo
from athena.db.repository import notes as notes_repo
from athena.indexing.chunking import Chunk
from athena.indexing.embedding import SparseVector
from athena.indexing.qdrant_store import ensure_collection, upsert_chunks
from athena.intelligence.related import RelatedNote, find_related


def _huey(tmp_path: Path) -> SqliteHuey:
    # SqliteHuey's storage opens a fresh connection per call -- ":memory:"
    # gives each call an empty, table-less database. A real temp file is
    # needed (same finding tests/retrieval/test_vector_search.py documents).
    return SqliteHuey(name="athena-test", filename=str(tmp_path / "huey.db"))


def _qdrant_client(tmp_path: Path) -> QdrantClient:
    client = QdrantClient(":memory:")
    ensure_collection(client, _huey(tmp_path))
    return client


async def _make_note(
    conn: aiosqlite.Connection, path: str = "a.md", *, content_hash: str = "h1"
) -> int:
    return await notes_repo.insert(
        conn,
        path=path,
        title=path,
        origin="human",
        provider=None,
        folder=None,
        content_hash=content_hash,
        created_at="t0",
    )


async def _index_note(
    conn: aiosqlite.Connection,
    qdrant_client: QdrantClient,
    *,
    note_id: int,
    text: str,
    dense_vector: list[float],
) -> str:
    """Insert a chunk row and a matching Qdrant point for `note_id`,
    mirroring what the real indexing pipeline does, following the pattern
    tests/retrieval/test_vector_search.py already established."""
    (point_id,) = upsert_chunks(
        qdrant_client,
        note_id=note_id,
        chunks=[Chunk(text=text, chunk_index=0, token_count=len(text.split()))],
        dense_vectors=[dense_vector],
        sparse_vectors=[SparseVector(indices=[1, 2], values=[0.5, 0.5])],
        payload_fields={
            "note_path": f"note-{note_id}.md",
            "tags": [],
            "folder": None,
            "status": "active",
            "origin": "human",
            "provider": None,
            "embedding_model_version": "test@1",
        },
    )
    await chunks_repo.insert(
        conn,
        note_id=note_id,
        chunk_index=0,
        chunk_text=text,
        content_hash=f"chunk-{note_id}",
        qdrant_point_id=point_id,
        embedding_model_version="test@1",
        token_count=len(text.split()),
        created_at="t0",
    )
    return point_id


async def test_excludes_the_queried_note_itself(conn: aiosqlite.Connection, tmp_path: Path) -> None:
    """Regression test for the self-exclusion guarantee (design doc §7's
    explicit call-out), even though it's already enforced one layer down
    by find_similar_by_point_id's own must_not/HasIdCondition filter."""
    qdrant_client = _qdrant_client(tmp_path)
    note_a = await _make_note(conn, "a.md", content_hash="ha")
    note_b = await _make_note(conn, "b.md", content_hash="hb")
    await _index_note(conn, qdrant_client, note_id=note_a, text="note a", dense_vector=[0.1] * 1024)
    await _index_note(conn, qdrant_client, note_id=note_b, text="note b", dense_vector=[0.1] * 1024)

    related = await find_related(conn, qdrant_client, note_a)

    assert note_a not in {r.note_id for r in related}
    assert {r.note_id for r in related} == {note_b}


async def test_returns_related_notes_with_path_and_score(
    conn: aiosqlite.Connection, tmp_path: Path
) -> None:
    qdrant_client = _qdrant_client(tmp_path)
    note_a = await _make_note(conn, "a.md", content_hash="ha")
    note_b = await _make_note(conn, "b.md", content_hash="hb")
    await _index_note(conn, qdrant_client, note_id=note_a, text="note a", dense_vector=[0.1] * 1024)
    await _index_note(conn, qdrant_client, note_id=note_b, text="note b", dense_vector=[0.1] * 1024)

    related = await find_related(conn, qdrant_client, note_a)

    assert len(related) == 1
    result = related[0]
    assert isinstance(result, RelatedNote)
    assert result.note_id == note_b
    assert result.note_path == "b.md"
    assert isinstance(result.score, float)


async def test_score_threshold_above_actual_similarity_returns_empty(
    conn: aiosqlite.Connection, tmp_path: Path
) -> None:
    qdrant_client = _qdrant_client(tmp_path)
    note_a = await _make_note(conn, "a.md", content_hash="ha")
    note_b = await _make_note(conn, "b.md", content_hash="hb")
    await _index_note(conn, qdrant_client, note_id=note_a, text="note a", dense_vector=[0.1] * 1024)
    await _index_note(conn, qdrant_client, note_id=note_b, text="note b", dense_vector=[0.1] * 1024)

    related = await find_related(conn, qdrant_client, note_a, score_threshold=2.0)

    assert related == []


async def test_note_with_no_chunks_returns_empty_list_without_raising(
    conn: aiosqlite.Connection, tmp_path: Path
) -> None:
    qdrant_client = _qdrant_client(tmp_path)
    note_id = await _make_note(conn, "never-indexed.md")

    related = await find_related(conn, qdrant_client, note_id)

    assert related == []


async def test_nonexistent_note_id_raises(conn: aiosqlite.Connection, tmp_path: Path) -> None:
    qdrant_client = _qdrant_client(tmp_path)

    with pytest.raises(ValueError, match="does not exist"):
        await find_related(conn, qdrant_client, 999)


async def test_soft_deleted_note_raises(conn: aiosqlite.Connection, tmp_path: Path) -> None:
    qdrant_client = _qdrant_client(tmp_path)
    note_id = await _make_note(conn, "deleted.md")
    await notes_repo.soft_delete(conn, note_id, deleted_at="t1")

    with pytest.raises(ValueError, match="does not exist or is soft-deleted"):
        await find_related(conn, qdrant_client, note_id)


async def test_hit_referencing_since_deleted_note_is_filtered_out(
    conn: aiosqlite.Connection, tmp_path: Path
) -> None:
    """A race: the point was indexed while the note was active, but the
    note has since been soft-deleted. The hit must be dropped silently,
    not crash the function (design doc §5's failure-modes table)."""
    qdrant_client = _qdrant_client(tmp_path)
    note_a = await _make_note(conn, "a.md", content_hash="ha")
    note_b = await _make_note(conn, "b.md", content_hash="hb")
    await _index_note(conn, qdrant_client, note_id=note_a, text="note a", dense_vector=[0.1] * 1024)
    await _index_note(conn, qdrant_client, note_id=note_b, text="note b", dense_vector=[0.1] * 1024)
    await notes_repo.soft_delete(conn, note_b, deleted_at="t1")

    related = await find_related(conn, qdrant_client, note_a)

    assert related == []

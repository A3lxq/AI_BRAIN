from __future__ import annotations

import aiosqlite

from athena.db.repository import chunks as chunks_repo
from athena.db.repository import notes as notes_repo
from athena.retrieval.fusion import fuse
from athena.retrieval.keyword_search import KeywordHit, NoteTitleHit
from athena.retrieval.vector_search import VectorHit


async def _make_note(conn: aiosqlite.Connection, path: str = "a.md") -> int:
    return await notes_repo.insert(
        conn,
        path=path,
        title="A",
        origin="human",
        provider=None,
        folder=None,
        content_hash="h1",
        created_at="t0",
    )


async def _make_chunk(conn: aiosqlite.Connection, note_id: int, chunk_index: int = 0) -> int:
    return await chunks_repo.insert(
        conn,
        note_id=note_id,
        chunk_index=chunk_index,
        chunk_text=f"chunk {chunk_index}",
        content_hash=f"c{chunk_index}",
        qdrant_point_id=f"p-{note_id}-{chunk_index}",
        embedding_model_version="v1",
        token_count=10,
        created_at="t0",
    )


async def test_chunk_in_two_lists_outscores_chunk_in_one(conn: aiosqlite.Connection) -> None:
    vector_hits = [
        VectorHit(chunk_id=1, note_id=10, qdrant_point_id="p1", rank=1),
        VectorHit(chunk_id=2, note_id=20, qdrant_point_id="p2", rank=1),
    ]
    chunk_keyword_hits = [KeywordHit(chunk_id=1, note_id=10, rank=1)]

    fused = await fuse(conn, vector_hits, chunk_keyword_hits, [])

    scores = {r.chunk_id: r.score for r in fused}
    assert scores[1] > scores[2]
    assert fused[0].chunk_id == 1


async def test_chunk_in_all_three_lists_outscores_chunk_in_two(
    conn: aiosqlite.Connection,
) -> None:
    note_a = await _make_note(conn, "a.md")
    chunk_a = await _make_chunk(conn, note_a)
    note_b = await _make_note(conn, "b.md")
    chunk_b = await _make_chunk(conn, note_b)

    vector_hits = [
        VectorHit(chunk_id=chunk_a, note_id=note_a, qdrant_point_id="p1", rank=3),
        VectorHit(chunk_id=chunk_b, note_id=note_b, qdrant_point_id="p2", rank=1),
    ]
    chunk_keyword_hits = [
        KeywordHit(chunk_id=chunk_a, note_id=note_a, rank=3),
        KeywordHit(chunk_id=chunk_b, note_id=note_b, rank=1),
    ]
    note_title_hits = [NoteTitleHit(note_id=note_a, rank=3)]

    fused = await fuse(conn, vector_hits, chunk_keyword_hits, note_title_hits)

    scores = {r.chunk_id: r.score for r in fused}
    assert scores[chunk_a] > scores[chunk_b]
    assert fused[0].chunk_id == chunk_a


async def test_empty_lists_still_produce_valid_result(conn: aiosqlite.Connection) -> None:
    fused = await fuse(conn, [], [KeywordHit(chunk_id=7, note_id=70, rank=1)], [])

    assert len(fused) == 1
    assert fused[0].chunk_id == 7
    assert fused[0].note_id == 70
    assert fused[0].score == 1.0 / 61


async def test_all_empty_lists_produce_empty_result(conn: aiosqlite.Connection) -> None:
    assert await fuse(conn, [], [], []) == []


async def test_vector_hit_with_none_chunk_id_is_skipped(conn: aiosqlite.Connection) -> None:
    vector_hits = [
        VectorHit(chunk_id=None, note_id=1, qdrant_point_id="p-old", rank=1),
        VectorHit(chunk_id=5, note_id=2, qdrant_point_id="p-new", rank=2),
    ]

    fused = await fuse(conn, vector_hits, [], [])

    assert len(fused) == 1
    assert fused[0].chunk_id == 5


async def test_note_title_hit_for_note_with_zero_chunks_is_dropped(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _make_note(conn, "empty.md")  # no chunks inserted

    fused = await fuse(conn, [], [], [NoteTitleHit(note_id=note_id, rank=1)])

    assert fused == []


async def test_note_title_hit_maps_to_first_chunk(conn: aiosqlite.Connection) -> None:
    note_id = await _make_note(conn, "multi.md")
    await _make_chunk(conn, note_id, chunk_index=1)
    first_chunk_id = await _make_chunk(conn, note_id, chunk_index=0)

    fused = await fuse(conn, [], [], [NoteTitleHit(note_id=note_id, rank=1)])

    assert len(fused) == 1
    assert fused[0].chunk_id == first_chunk_id
    assert fused[0].note_id == note_id


async def test_result_sorted_descending_by_score(conn: aiosqlite.Connection) -> None:
    vector_hits = [
        VectorHit(chunk_id=1, note_id=10, qdrant_point_id="p1", rank=5),
        VectorHit(chunk_id=2, note_id=20, qdrant_point_id="p2", rank=1),
    ]

    fused = await fuse(conn, vector_hits, [], [])

    assert [r.score for r in fused] == sorted((r.score for r in fused), reverse=True)
    assert fused[0].chunk_id == 2

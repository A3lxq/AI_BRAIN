from __future__ import annotations

import aiosqlite

from ai_brain.db.repository import chunks as chunks_repo
from ai_brain.db.repository import notes as notes_repo
from ai_brain.retrieval.context import build_context
from ai_brain.retrieval.reranking import RerankedResult


async def _make_note(conn: aiosqlite.Connection, path: str) -> int:
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


async def _make_chunk(
    conn: aiosqlite.Connection,
    note_id: int,
    *,
    chunk_index: int,
    text: str,
    token_count: int | None,
) -> int:
    return await chunks_repo.insert(
        conn,
        note_id=note_id,
        chunk_index=chunk_index,
        chunk_text=text,
        content_hash=f"c{note_id}-{chunk_index}",
        qdrant_point_id=f"p-{note_id}-{chunk_index}",
        embedding_model_version="v1",
        token_count=token_count,
        created_at="t0",
    )


async def test_greedy_inclusion_respects_max_tokens(conn: aiosqlite.Connection) -> None:
    note_id = await _make_note(conn, "budget.md")
    chunk1 = await _make_chunk(conn, note_id, chunk_index=0, text="first chunk", token_count=3000)
    chunk2 = await _make_chunk(conn, note_id, chunk_index=1, text="second chunk", token_count=3000)

    reranked = [
        RerankedResult(chunk_id=chunk1, score=0.9),
        RerankedResult(chunk_id=chunk2, score=0.8),
    ]

    result = await build_context(conn, reranked, max_tokens=4096)

    assert "first chunk" in result.text
    assert "second chunk" not in result.text
    assert result.note_ids == [note_id]


async def test_max_tokens_smaller_than_first_chunk_yields_empty_result(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _make_note(conn, "toobig.md")
    chunk_id = await _make_chunk(conn, note_id, chunk_index=0, text="huge chunk", token_count=5000)

    reranked = [RerankedResult(chunk_id=chunk_id, score=0.9)]

    result = await build_context(conn, reranked, max_tokens=4096)

    assert result.text == ""
    assert result.note_ids == []


async def test_citations_present_and_correctly_attributed(conn: aiosqlite.Connection) -> None:
    note_id = await _make_note(conn, "CLAUDE/source.md")
    chunk_id = await _make_chunk(
        conn, note_id, chunk_index=0, text="the actual content", token_count=10
    )

    reranked = [RerankedResult(chunk_id=chunk_id, score=0.9)]

    result = await build_context(conn, reranked, max_tokens=4096)

    assert "[Source: CLAUDE/source.md]" in result.text
    assert "the actual content" in result.text
    assert result.text.index("[Source: CLAUDE/source.md]") < result.text.index("the actual content")


async def test_note_with_multiple_chunks_appears_once_in_note_ids(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _make_note(conn, "multi.md")
    chunk1 = await _make_chunk(conn, note_id, chunk_index=0, text="part one", token_count=10)
    chunk2 = await _make_chunk(conn, note_id, chunk_index=1, text="part two", token_count=10)

    reranked = [
        RerankedResult(chunk_id=chunk1, score=0.9),
        RerankedResult(chunk_id=chunk2, score=0.8),
    ]

    result = await build_context(conn, reranked, max_tokens=4096)

    assert result.note_ids == [note_id]
    assert "part one" in result.text
    assert "part two" in result.text


async def test_missing_token_count_falls_back_to_char_estimate(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _make_note(conn, "notok.md")
    chunk_id = await _make_chunk(
        conn, note_id, chunk_index=0, text="x" * 40, token_count=None
    )

    reranked = [RerankedResult(chunk_id=chunk_id, score=0.9)]

    result = await build_context(conn, reranked, max_tokens=4096)

    assert "x" * 40 in result.text
    assert result.note_ids == [note_id]

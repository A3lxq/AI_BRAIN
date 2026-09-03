from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import aiosqlite
from qdrant_client import QdrantClient

from ai_brain.db.repository import chunks as chunks_repo
from ai_brain.db.repository import notes as notes_repo
from ai_brain.retrieval.search import search, search_ranked_note_paths


async def _make_note_with_chunk(
    conn: aiosqlite.Connection, *, path: str, title: str, chunk_text: str
) -> int:
    note_id = await notes_repo.insert(
        conn, path=path, title=title, origin="human", provider=None,
        folder=None, content_hash="h", created_at="t0",
    )
    await chunks_repo.insert(
        conn, note_id=note_id, chunk_index=0, chunk_text=chunk_text,
        content_hash="c1", qdrant_point_id="p1",
        embedding_model_version="v1", token_count=len(chunk_text.split()), created_at="t0",
    )
    return note_id


async def test_search_falls_back_to_keyword_only_when_qdrant_unreachable(
    conn: aiosqlite.Connection, tmp_path: Path
) -> None:
    await _make_note_with_chunk(
        conn, path="a.md", title="Binary Search Trees",
        chunk_text="A binary search tree is a hierarchical data structure",
    )

    # A client pointed at nothing -- query_points will raise a real connection error.
    unreachable_client = QdrantClient(url="http://127.0.0.1:1")

    result = await search(conn, unreachable_client, "binary search tree")

    assert result.text != ""
    assert len(result.note_ids) == 1


async def test_search_never_propagates_qdrant_failure(
    conn: aiosqlite.Connection, tmp_path: Path
) -> None:
    await _make_note_with_chunk(
        conn, path="a.md", title="Hash Tables", chunk_text="A hash table gives O(1) lookup"
    )
    unreachable_client = QdrantClient(url="http://127.0.0.1:1")

    with patch(
        "ai_brain.retrieval.search.vector_search.search",
        side_effect=RuntimeError("connection refused"),
    ):
        # Must not raise.
        result = await search(conn, unreachable_client, "hash table")

    assert isinstance(result.text, str)


async def test_search_with_no_matches_returns_empty_context(
    conn: aiosqlite.Connection, tmp_path: Path
) -> None:
    unreachable_client = QdrantClient(url="http://127.0.0.1:1")

    result = await search(conn, unreachable_client, "nonexistent query term xyzzy")

    assert result.text == ""
    assert result.note_ids == []


async def test_search_ranked_note_paths_returns_deduplicated_ranked_paths(
    conn: aiosqlite.Connection, tmp_path: Path
) -> None:
    await _make_note_with_chunk(
        conn, path="trees.md", title="Binary Search Trees",
        chunk_text="A binary search tree is a hierarchical data structure",
    )
    unreachable_client = QdrantClient(url="http://127.0.0.1:1")

    paths = await search_ranked_note_paths(conn, unreachable_client, "binary search tree")

    assert paths == ["trees.md"]

"""Tests for `ai_brain.retrieval.keyword_search` against a real migrated
SQLite file (docs/design/retrieval-pipeline.md §2.1/§7).
"""

from __future__ import annotations

import uuid

import aiosqlite

from ai_brain.db.repository import chunks, notes, tags
from ai_brain.retrieval import keyword_search
from ai_brain.retrieval.keyword_search import (
    KeywordHit,
    NoteTitleHit,
    sanitize_fts5_query,
    search_chunks,
    search_notes,
)

_CREATED_AT = "2026-09-02T00:00:00+00:00"


async def _make_note(
    conn: aiosqlite.Connection,
    *,
    path: str,
    title: str,
    folder: str | None = None,
    status: str | None = None,
    tag_names: list[str] | None = None,
    deleted: bool = False,
) -> int:
    note_id = await notes.insert(
        conn,
        path=path,
        title=title,
        origin="human",
        provider=None,
        folder=folder,
        content_hash=f"hash-{path}",
        created_at=_CREATED_AT,
        status=status,
    )
    for name in tag_names or []:
        tag_id = await tags.get_or_create(conn, name, name)
        await tags.attach(conn, note_id, tag_id)
    if deleted:
        await notes.soft_delete(conn, note_id, deleted_at=_CREATED_AT)
    return note_id


async def _make_chunk(conn: aiosqlite.Connection, *, note_id: int, chunk_text: str) -> int:
    return await chunks.insert(
        conn,
        note_id=note_id,
        chunk_index=0,
        chunk_text=chunk_text,
        content_hash=f"hash-{chunk_text}",
        qdrant_point_id=str(uuid.uuid4()),
        embedding_model_version="bge-m3@1",
        token_count=len(chunk_text.split()),
        created_at=_CREATED_AT,
    )


# ---------------------------------------------------------------------------
# sanitize_fts5_query
# ---------------------------------------------------------------------------


def test_sanitize_normal_multi_word() -> None:
    assert sanitize_fts5_query("hello world") == '"hello" "world"'


def test_sanitize_embedded_quote_doubles_it() -> None:
    result = sanitize_fts5_query('say "hi"')
    assert result == '"say" """hi"""'


def test_sanitize_operator_words_are_wrapped_as_literals() -> None:
    assert sanitize_fts5_query("OR NOT AND") == '"OR" "NOT" "AND"'


def test_sanitize_empty_string() -> None:
    assert sanitize_fts5_query("") == ""


def test_sanitize_whitespace_only() -> None:
    assert sanitize_fts5_query("   \t  ") == ""


# ---------------------------------------------------------------------------
# search_chunks
# ---------------------------------------------------------------------------


async def test_search_chunks_finds_and_ranks_matching_term(
    conn: aiosqlite.Connection,
) -> None:
    note_a = await _make_note(conn, path="CLAUDE/a.md", title="A")
    note_b = await _make_note(conn, path="CLAUDE/b.md", title="B")
    chunk_a = await _make_chunk(conn, note_id=note_a, chunk_text="quantum computing basics")
    await _make_chunk(conn, note_id=note_b, chunk_text="a note about gardening")

    hits = await search_chunks(conn, "quantum")

    assert len(hits) == 1
    assert hits[0] == KeywordHit(chunk_id=chunk_a, note_id=note_a, rank=1)


async def test_search_chunks_literal_or_word_is_not_treated_as_operator(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _make_note(conn, path="CLAUDE/or.md", title="Or note")
    chunk_id = await _make_chunk(
        conn, note_id=note_id, chunk_text="this chunk literally contains the word OR in it"
    )

    hits = await search_chunks(conn, "OR")

    assert hits == [KeywordHit(chunk_id=chunk_id, note_id=note_id, rank=1)]


async def test_search_chunks_filters_by_tags_folder_and_status(
    conn: aiosqlite.Connection,
) -> None:
    matching_note = await _make_note(
        conn,
        path="CLAUDE/match.md",
        title="Match",
        folder="CLAUDE",
        status="active",
        tag_names=["rag", "qdrant"],
    )
    matching_chunk = await _make_chunk(
        conn, note_id=matching_note, chunk_text="filters keyword content"
    )

    wrong_folder_note = await _make_note(
        conn,
        path="QWEN/wrongfolder.md",
        title="Wrong folder",
        folder="QWEN",
        status="active",
        tag_names=["rag", "qdrant"],
    )
    await _make_chunk(conn, note_id=wrong_folder_note, chunk_text="filters keyword content")

    wrong_status_note = await _make_note(
        conn,
        path="CLAUDE/wrongstatus.md",
        title="Wrong status",
        folder="CLAUDE",
        status="draft",
        tag_names=["rag", "qdrant"],
    )
    await _make_chunk(conn, note_id=wrong_status_note, chunk_text="filters keyword content")

    missing_tag_note = await _make_note(
        conn,
        path="CLAUDE/missingtag.md",
        title="Missing tag",
        folder="CLAUDE",
        status="active",
        tag_names=["rag"],
    )
    await _make_chunk(conn, note_id=missing_tag_note, chunk_text="filters keyword content")

    hits = await search_chunks(
        conn, "filters", tags=["rag", "qdrant"], folder="CLAUDE", status="active"
    )

    assert hits == [KeywordHit(chunk_id=matching_chunk, note_id=matching_note, rank=1)]


async def test_search_chunks_excludes_soft_deleted_notes(conn: aiosqlite.Connection) -> None:
    note_id = await _make_note(conn, path="CLAUDE/gone.md", title="Gone", deleted=True)
    await _make_chunk(conn, note_id=note_id, chunk_text="ephemeral content here")

    hits = await search_chunks(conn, "ephemeral")

    assert hits == []


async def test_search_chunks_empty_query_returns_empty_without_raising(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _make_note(conn, path="CLAUDE/whatever.md", title="Whatever")
    await _make_chunk(conn, note_id=note_id, chunk_text="some content")

    assert await search_chunks(conn, "") == []
    assert await search_chunks(conn, "   ") == []


# ---------------------------------------------------------------------------
# search_notes
# ---------------------------------------------------------------------------


async def test_search_notes_finds_and_ranks_matching_title(conn: aiosqlite.Connection) -> None:
    note_a = await _make_note(conn, path="CLAUDE/a.md", title="Quantum Computing Notes")
    await _make_note(conn, path="CLAUDE/b.md", title="Gardening Tips")

    hits = await search_notes(conn, "quantum")

    assert hits == [NoteTitleHit(note_id=note_a, rank=1)]


async def test_search_notes_literal_or_word_is_not_treated_as_operator(
    conn: aiosqlite.Connection,
) -> None:
    note_id = await _make_note(conn, path="CLAUDE/or.md", title="The word OR in a title")

    hits = await search_notes(conn, "OR")

    assert hits == [NoteTitleHit(note_id=note_id, rank=1)]


async def test_search_notes_filters_by_tags_folder_and_status(
    conn: aiosqlite.Connection,
) -> None:
    matching_note = await _make_note(
        conn,
        path="CLAUDE/match.md",
        title="Filterable Title",
        folder="CLAUDE",
        status="active",
        tag_names=["rag", "qdrant"],
    )

    wrong_folder_note = await _make_note(
        conn,
        path="QWEN/wrongfolder.md",
        title="Filterable Title",
        folder="QWEN",
        status="active",
        tag_names=["rag", "qdrant"],
    )
    assert wrong_folder_note != matching_note

    wrong_status_note = await _make_note(
        conn,
        path="CLAUDE/wrongstatus.md",
        title="Filterable Title",
        folder="CLAUDE",
        status="draft",
        tag_names=["rag", "qdrant"],
    )
    assert wrong_status_note != matching_note

    missing_tag_note = await _make_note(
        conn,
        path="CLAUDE/missingtag.md",
        title="Filterable Title",
        folder="CLAUDE",
        status="active",
        tag_names=["rag"],
    )
    assert missing_tag_note != matching_note

    hits = await search_notes(
        conn, "Filterable", tags=["rag", "qdrant"], folder="CLAUDE", status="active"
    )

    assert hits == [NoteTitleHit(note_id=matching_note, rank=1)]


async def test_search_notes_excludes_soft_deleted_notes(conn: aiosqlite.Connection) -> None:
    await _make_note(conn, path="CLAUDE/gone.md", title="Ephemeral Title", deleted=True)

    hits = await search_notes(conn, "ephemeral")

    assert hits == []


async def test_search_notes_empty_query_returns_empty_without_raising(
    conn: aiosqlite.Connection,
) -> None:
    await _make_note(conn, path="CLAUDE/whatever.md", title="Whatever Title")

    assert await search_notes(conn, "") == []
    assert await search_notes(conn, "   ") == []


def test_keyword_search_module_exports_expected_names() -> None:
    assert keyword_search.sanitize_fts5_query is sanitize_fts5_query
    assert keyword_search.search_chunks is search_chunks
    assert keyword_search.search_notes is search_notes

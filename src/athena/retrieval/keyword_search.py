"""Safe FTS5 keyword search over `chunks_fts`/`notes_fts` (docs/DATA_MODEL.md
§2.9; docs/design/retrieval-pipeline.md §2.1/§3).

Resolves docs/SECURITY_MODEL.md TB-7's FTS5 query-syntax-injection gap: every
`MATCH` string reaching SQLite must first pass through `sanitize_fts5_query`,
which quotes each word as a literal term so FTS5's own grammar (`AND`/`OR`/
`NOT`/`NEAR`/column filters/prefix `*`) can never be triggered by untrusted
input. Filters (tags/folder/status/soft-delete) are ordinary parameterized SQL
predicates against `notes` columns, joined in -- never concatenated into the
MATCH string itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiosqlite


def sanitize_fts5_query(raw: str) -> str:
    words = raw.split()
    if not words:
        return ""
    return " ".join('"' + word.replace('"', '""') + '"' for word in words)


@dataclass(frozen=True)
class KeywordHit:
    chunk_id: int
    note_id: int
    rank: int  # 1-based position in this result list


@dataclass(frozen=True)
class NoteTitleHit:
    note_id: int
    rank: int


def _tag_matches(tags_text: str, tag: str) -> bool:
    return tag in tags_text.split()


async def search_chunks(
    conn: aiosqlite.Connection,
    query: str,
    *,
    tags: list[str] | None = None,
    folder: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[KeywordHit]:
    sanitized = sanitize_fts5_query(query)
    if not sanitized:
        return []

    sql = (
        "SELECT chunks.id, chunks.note_id, notes.tags_text, "
        "bm25(chunks_fts) AS score "
        "FROM chunks_fts "
        "JOIN chunks ON chunks.id = chunks_fts.rowid "
        "JOIN notes ON notes.id = chunks.note_id "
        "WHERE chunks_fts MATCH ? AND notes.deleted_at IS NULL"
    )
    params: list[Any] = [sanitized]

    if folder is not None:
        sql += " AND notes.folder = ?"
        params.append(folder)
    if status is not None:
        sql += " AND notes.status = ?"
        params.append(status)

    # bm25() is lower-is-better; over-fetch when a Python-side tag filter will
    # drop some rows, then truncate to `limit` after filtering below.
    fetch_limit = limit if not tags else max(limit * 4, limit)
    sql += " ORDER BY score LIMIT ?"
    params.append(fetch_limit)

    cursor = await conn.execute(sql, params)  # noqa: S608 -- no user input interpolated
    rows = await cursor.fetchall()

    hits: list[KeywordHit] = []
    for row in rows:
        chunk_id, note_id, tags_text = row[0], row[1], row[2]
        # tags_text is a space-joined string (DATA_MODEL.md §2.2/§2.3); matching
        # "all requested tags" is done in Python (whole-word membership) rather
        # than a fragile LIKE-based SQL predicate -- simpler and unambiguous,
        # at the cost of over-fetching before filtering (see fetch_limit above).
        if tags and not all(_tag_matches(tags_text, tag) for tag in tags):
            continue
        hits.append(KeywordHit(chunk_id=chunk_id, note_id=note_id, rank=len(hits) + 1))
        if len(hits) >= limit:
            break

    return hits


async def search_notes(
    conn: aiosqlite.Connection,
    query: str,
    *,
    tags: list[str] | None = None,
    folder: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[NoteTitleHit]:
    sanitized = sanitize_fts5_query(query)
    if not sanitized:
        return []

    sql = (
        "SELECT notes.id, notes.tags_text, bm25(notes_fts) AS score "
        "FROM notes_fts "
        "JOIN notes ON notes.id = notes_fts.rowid "
        "WHERE notes_fts MATCH ? AND notes.deleted_at IS NULL"
    )
    params: list[Any] = [sanitized]

    if folder is not None:
        sql += " AND notes.folder = ?"
        params.append(folder)
    if status is not None:
        sql += " AND notes.status = ?"
        params.append(status)

    fetch_limit = limit if not tags else max(limit * 4, limit)
    sql += " ORDER BY score LIMIT ?"
    params.append(fetch_limit)

    cursor = await conn.execute(sql, params)  # noqa: S608 -- no user input interpolated
    rows = await cursor.fetchall()

    hits: list[NoteTitleHit] = []
    for row in rows:
        note_id, tags_text = row[0], row[1]
        if tags and not all(_tag_matches(tags_text, tag) for tag in tags):
            continue
        hits.append(NoteTitleHit(note_id=note_id, rank=len(hits) + 1))
        if len(hits) >= limit:
            break

    return hits

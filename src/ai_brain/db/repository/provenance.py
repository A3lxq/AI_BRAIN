"""Repository functions for `provenance`/`provenance_sources` (docs/DATA_MODEL.md §2.4).

Covers only the PROV Activity (`provenance`) and "used" (`provenance_sources`)
relations the ingestion pipeline needs -- `provenance_derivations` (multi-source
merges) is out of this task's scope per the design doc §2.2.
"""

from __future__ import annotations

import aiosqlite


async def insert_activity(
    conn: aiosqlite.Connection,
    *,
    note_id: int,
    activity_type: str,
    provider: str | None,
    model: str | None,
    human_edited: bool,
    occurred_at: str,
    recorded_at: str,
    transformation_notes: str | None = None,
    research_job_id: int | None = None,
) -> int:
    cursor = await conn.execute(
        "INSERT INTO provenance (note_id, activity_type, provider, model, "
        "human_edited, research_job_id, transformation_notes, occurred_at, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            note_id,
            activity_type,
            provider,
            model,
            1 if human_edited else 0,
            research_job_id,
            transformation_notes,
            occurred_at,
            recorded_at,
        ),
    )
    await conn.commit()
    provenance_id = cursor.lastrowid
    if provenance_id is None:
        raise RuntimeError("INSERT INTO provenance did not yield a rowid")
    return provenance_id


async def insert_source(
    conn: aiosqlite.Connection,
    *,
    provenance_id: int,
    url: str,
    title: str | None = None,
    accessed_at: str | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO provenance_sources (provenance_id, url, title, accessed_at) "
        "VALUES (?, ?, ?, ?)",
        (provenance_id, url, title, accessed_at),
    )
    await conn.commit()

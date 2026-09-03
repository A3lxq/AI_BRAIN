"""Repository functions for `research_jobs` (docs/DATA_MODEL.md §2.7).

Per the design doc §1, this task's scope covers only `job_type='ingestion'`
usage -- the functions themselves are generic over `job_type` since the DDL
is, but no research/duplicate-scan business logic is implemented here.
"""

from __future__ import annotations

import aiosqlite

_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


async def insert(
    conn: aiosqlite.Connection,
    *,
    huey_task_id: str,
    job_type: str,
    created_at: str,
    query: str | None = None,
    requested_by: str | None = None,
) -> int:
    cursor = await conn.execute(
        "INSERT INTO research_jobs (huey_task_id, job_type, query, requested_by, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (huey_task_id, job_type, query, requested_by, created_at),
    )
    await conn.commit()
    job_id = cursor.lastrowid
    if job_id is None:
        raise RuntimeError("INSERT INTO research_jobs did not yield a rowid")
    return job_id


async def mark_started(conn: aiosqlite.Connection, job_id: int, started_at: str) -> None:
    await conn.execute(
        "UPDATE research_jobs SET status = 'running', started_at = ? WHERE id = ?",
        (started_at, job_id),
    )
    await conn.commit()


async def mark_finished(
    conn: aiosqlite.Connection,
    job_id: int,
    *,
    status: str,
    finished_at: str,
    error_message: str | None = None,
    result_note_id: int | None = None,
) -> None:
    if status not in _TERMINAL_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(_TERMINAL_STATUSES)}, got {status!r}"
        )
    await conn.execute(
        "UPDATE research_jobs SET status = ?, finished_at = ?, error_message = ?, "
        "result_note_id = ? WHERE id = ?",
        (status, finished_at, error_message, result_note_id, job_id),
    )
    await conn.commit()

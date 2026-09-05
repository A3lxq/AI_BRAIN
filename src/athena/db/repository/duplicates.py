"""Repository functions for `duplicate_candidates`/`note_minhash_signatures`
(docs/DATA_MODEL.md §2.6, docs/design/knowledge-intelligence.md §2.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiosqlite

__all__ = [
    "DuplicateCandidateRow",
    "upsert_candidate",
    "get_by_id",
    "list_by_status",
    "update_resolution",
    "MinhashSignatureRow",
    "upsert_signature",
    "list_all_signatures",
]

_CANDIDATE_COLUMNS = (
    "id, note_a_id, note_b_id, detection_method, lexical_score, semantic_score, "
    "metadata_match_score, combined_score, status, detected_at, resolved_at, "
    "resolved_by, resolution_note"
)


@dataclass(frozen=True)
class DuplicateCandidateRow:
    id: int
    note_a_id: int
    note_b_id: int
    detection_method: str
    lexical_score: float | None
    semantic_score: float | None
    metadata_match_score: float | None
    combined_score: float
    status: str
    detected_at: str
    resolved_at: str | None
    resolved_by: str | None
    resolution_note: str | None


def _row_to_candidate(row: Any) -> DuplicateCandidateRow:
    return DuplicateCandidateRow(
        id=row[0],
        note_a_id=row[1],
        note_b_id=row[2],
        detection_method=row[3],
        lexical_score=row[4],
        semantic_score=row[5],
        metadata_match_score=row[6],
        combined_score=row[7],
        status=row[8],
        detected_at=row[9],
        resolved_at=row[10],
        resolved_by=row[11],
        resolution_note=row[12],
    )


async def upsert_candidate(
    conn: aiosqlite.Connection,
    *,
    note_a_id: int,
    note_b_id: int,
    detection_method: str,
    lexical_score: float | None,
    semantic_score: float | None,
    metadata_match_score: float | None,
    combined_score: float,
    detected_at: str,
) -> int:
    """Insert a candidate pair, or refresh its scores on a rescan.

    Canonical ordering (`note_a_id < note_b_id`) is enforced here, not left
    to the caller, since the schema's own CHECK constraint would otherwise
    reject half of all naturally-ordered calls. A rescan only updates scores
    while the row is still `'pending'` -- once a human has reviewed and
    resolved a candidate (`confirmed`/`rejected`/`merged`), a later scan
    finding the same pair again must not silently overwrite that audit
    trail (docs/design/knowledge-intelligence.md §2.1/§5).
    """
    if note_a_id == note_b_id:
        raise ValueError("a note cannot be a duplicate of itself")
    ordered_a, ordered_b = (
        (note_a_id, note_b_id) if note_a_id < note_b_id else (note_b_id, note_a_id)
    )

    await conn.execute(
        "INSERT INTO duplicate_candidates "
        "(note_a_id, note_b_id, detection_method, lexical_score, semantic_score, "
        "metadata_match_score, combined_score, detected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (note_a_id, note_b_id) DO UPDATE SET "
        "detection_method = excluded.detection_method, "
        "lexical_score = excluded.lexical_score, "
        "semantic_score = excluded.semantic_score, "
        "metadata_match_score = excluded.metadata_match_score, "
        "combined_score = excluded.combined_score, "
        "detected_at = excluded.detected_at "
        "WHERE duplicate_candidates.status = 'pending'",
        (
            ordered_a,
            ordered_b,
            detection_method,
            lexical_score,
            semantic_score,
            metadata_match_score,
            combined_score,
            detected_at,
        ),
    )
    await conn.commit()
    # The row's id is needed regardless of whether this was a fresh insert,
    # a refreshed pending row, or a no-op against an already-resolved row
    # (the WHERE clause above silently skips the UPDATE in that last case,
    # per SQLite's UPSERT semantics) -- one lookup covers all three.
    cursor = await conn.execute(
        "SELECT id FROM duplicate_candidates WHERE note_a_id = ? AND note_b_id = ?",
        (ordered_a, ordered_b),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("upsert into duplicate_candidates did not yield a row")
    return int(row[0])


async def get_by_id(
    conn: aiosqlite.Connection, candidate_id: int
) -> DuplicateCandidateRow | None:
    cursor = await conn.execute(
        f"SELECT {_CANDIDATE_COLUMNS} FROM duplicate_candidates WHERE id = ?",  # noqa: S608
        (candidate_id,),
    )
    row = await cursor.fetchone()
    return _row_to_candidate(row) if row is not None else None


async def list_by_status(
    conn: aiosqlite.Connection, status: str
) -> list[DuplicateCandidateRow]:
    cursor = await conn.execute(
        f"SELECT {_CANDIDATE_COLUMNS} FROM duplicate_candidates "  # noqa: S608
        "WHERE status = ? ORDER BY combined_score DESC",
        (status,),
    )
    rows = await cursor.fetchall()
    return [_row_to_candidate(row) for row in rows]


async def update_resolution(
    conn: aiosqlite.Connection,
    candidate_id: int,
    *,
    status: str,
    resolved_at: str,
    resolved_by: str,
    resolution_note: str | None = None,
) -> None:
    await conn.execute(
        "UPDATE duplicate_candidates SET status = ?, resolved_at = ?, resolved_by = ?, "
        "resolution_note = ? WHERE id = ?",
        (status, resolved_at, resolved_by, resolution_note, candidate_id),
    )
    await conn.commit()


@dataclass(frozen=True)
class MinhashSignatureRow:
    note_id: int
    num_perm: int
    signature: bytes
    computed_at: str


async def upsert_signature(
    conn: aiosqlite.Connection,
    *,
    note_id: int,
    num_perm: int,
    signature: bytes,
    computed_at: str,
) -> None:
    await conn.execute(
        "INSERT INTO note_minhash_signatures (note_id, num_perm, signature, computed_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT (note_id) DO UPDATE SET "
        "num_perm = excluded.num_perm, signature = excluded.signature, "
        "computed_at = excluded.computed_at",
        (note_id, num_perm, signature, computed_at),
    )
    await conn.commit()


async def list_all_signatures(conn: aiosqlite.Connection) -> list[MinhashSignatureRow]:
    """Every persisted MinHash signature -- used to rebuild the in-process
    `MinHashLSH` index at the start of each duplicate-detection scan, since
    the index itself is never persisted (docs/design/knowledge-intelligence.md
    §0/§2.1)."""
    cursor = await conn.execute(
        "SELECT note_id, num_perm, signature, computed_at FROM note_minhash_signatures"
    )
    rows = await cursor.fetchall()
    return [
        MinhashSignatureRow(note_id=row[0], num_perm=row[1], signature=row[2], computed_at=row[3])
        for row in rows
    ]

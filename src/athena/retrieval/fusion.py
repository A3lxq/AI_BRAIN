"""Hand-written Reciprocal Rank Fusion (docs/design/retrieval-pipeline.md
§2.3), combining the vector, chunk-keyword, and note-title rank lists into
one chunk-level ranking. Not `ranx` -- ADR-0003 left this hand-rollable, and
a few lines of pure math don't justify a new dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

from athena.db.repository import chunks as chunks_repo
from athena.retrieval.keyword_search import KeywordHit, NoteTitleHit
from athena.retrieval.vector_search import VectorHit


@dataclass(frozen=True)
class FusedResult:
    chunk_id: int
    note_id: int
    score: float


async def fuse(
    conn: aiosqlite.Connection,
    vector_hits: list[VectorHit],
    chunk_keyword_hits: list[KeywordHit],
    note_title_hits: list[NoteTitleHit],
    *,
    k: int = 60,
) -> list[FusedResult]:
    scores: dict[int, float] = {}
    note_ids: dict[int, int] = {}

    for vector_hit in vector_hits:
        if vector_hit.chunk_id is None:
            # Pre-Phase-4 Qdrant point whose payload predates chunk_id
            # (§2.2/§8) -- can't be joined to a chunks row, so it's dropped
            # from chunk-level fusion rather than crashing on it.
            continue
        scores[vector_hit.chunk_id] = scores.get(vector_hit.chunk_id, 0.0) + 1.0 / (
            k + vector_hit.rank
        )
        note_ids[vector_hit.chunk_id] = vector_hit.note_id

    for keyword_hit in chunk_keyword_hits:
        scores[keyword_hit.chunk_id] = scores.get(keyword_hit.chunk_id, 0.0) + 1.0 / (
            k + keyword_hit.rank
        )
        note_ids[keyword_hit.chunk_id] = keyword_hit.note_id

    for note_hit in note_title_hits:
        chunk_id = await chunks_repo.get_first_chunk_id_for_note(conn, note_hit.note_id)
        if chunk_id is None:
            # Note matched on title/tags but has no indexed chunks yet --
            # dropped, not fused with a nonexistent chunk_id.
            continue
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + note_hit.rank)
        note_ids[chunk_id] = note_hit.note_id

    fused = [
        FusedResult(chunk_id=chunk_id, note_id=note_ids[chunk_id], score=score)
        for chunk_id, score in scores.items()
    ]
    fused.sort(key=lambda r: r.score, reverse=True)
    return fused

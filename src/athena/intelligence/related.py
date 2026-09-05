"""Related-notes suggestions (docs/design/knowledge-intelligence.md §2.3).

An on-demand, purely read-side query -- reuses the exact same "query by
point ID + self-exclusion" mechanism as the semantic-duplicate leg of
`athena.intelligence.duplicates` (§2.1), but at a much lower
`score_threshold` (default 0.5 vs. duplicate detection's 0.85) since the
intent here is "topically similar," not "possibly the same note."

Deliberately not persisted: unlike duplicate candidates (which need a
review workflow and audit trail), related-notes results are cheap to
recompute on demand and go stale the moment either note's content
changes, so persisting them would just be another cache-invalidation
problem for no real benefit (§2.3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiosqlite
from qdrant_client import QdrantClient

from athena.db.repository import chunks as chunks_repo
from athena.db.repository import notes as notes_repo
from athena.retrieval.vector_search import find_similar_by_point_id

logger = logging.getLogger(__name__)

__all__ = ["RelatedNote", "find_related"]


@dataclass(frozen=True)
class RelatedNote:
    note_id: int
    note_path: str
    score: float


async def find_related(
    conn: aiosqlite.Connection,
    qdrant_client: QdrantClient,
    note_id: int,
    *,
    limit: int = 5,
    score_threshold: float = 0.5,
) -> list[RelatedNote]:
    """Notes topically similar to `note_id`, ranked by cosine similarity.

    Raises `ValueError` if `note_id` doesn't exist or is soft-deleted --
    there's no sensible "related notes" answer for a note that isn't
    active. Returns an empty list (not an error) if the note has never
    been indexed (no chunks yet) -- a normal, expected state, logged at
    INFO, distinct from the note simply not existing.
    """
    note = await notes_repo.get_by_id(conn, note_id)
    if note is None or note.deleted_at is not None:
        raise ValueError(f"note_id {note_id} does not exist or is soft-deleted")

    first_chunk_id = await chunks_repo.get_first_chunk_id_for_note(conn, note_id)
    if first_chunk_id is None:
        logger.info("note_id %d has no chunks yet (never indexed) -- no related notes", note_id)
        return []

    (chunk_row,) = await chunks_repo.get_by_ids(conn, [first_chunk_id])
    point_id = chunk_row.qdrant_point_id

    hits = find_similar_by_point_id(
        qdrant_client, point_id, limit=limit, score_threshold=score_threshold
    )

    related: list[RelatedNote] = []
    for hit in hits:
        hit_note = await notes_repo.get_by_id(conn, hit.note_id)
        if hit_note is None or hit_note.deleted_at is not None:
            # Race: the note could have been deleted since the point was
            # indexed. Skip silently rather than crash (§2.3/failure modes).
            continue
        # find_similar_by_point_id always populates score (dense-only,
        # single-vector-space query) -- 0.0 fallback only satisfies typing.
        score = hit.score if hit.score is not None else 0.0
        related.append(RelatedNote(note_id=hit_note.id, note_path=hit_note.path, score=score))
    return related

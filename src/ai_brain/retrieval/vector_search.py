"""Qdrant hybrid dense+sparse vector search (docs/design/retrieval-pipeline.md
§0/§2.2; DATA_MODEL.md §3).

A query is embedded exactly like a chunk (`embed_dense`/`embed_sparse`) --
no separate query encoder exists for BGE-M3/miniCOIL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient, models

from ai_brain.indexing.embedding import embed_dense, embed_sparse
from ai_brain.indexing.qdrant_store import COLLECTION_ALIAS

_DENSE_VECTOR_NAME = "dense"
_SPARSE_VECTOR_NAME = "minicoil"


@dataclass(frozen=True)
class VectorHit:
    # None for points upserted before chunk_id was added to the payload --
    # ai_brain.indexing.qdrant_store.upsert_chunks deliberately omits it.
    chunk_id: int | None
    note_id: int
    qdrant_point_id: str
    rank: int  # 1-based


def _build_filter(
    tags: list[str] | None, folder: str | None, status: str | None
) -> models.Filter | None:
    # Typed loosely (Any) to match models.Filter.must's broad condition union
    # without fighting list-invariance for no real benefit here.
    conditions: list[Any] = []
    if tags:
        match = models.MatchAny(any=tags) if len(tags) > 1 else models.MatchValue(value=tags[0])
        conditions.append(models.FieldCondition(key="tags", match=match))
    if folder:
        folder_match = models.MatchValue(value=folder)
        conditions.append(models.FieldCondition(key="folder", match=folder_match))
    if status:
        status_match = models.MatchValue(value=status)
        conditions.append(models.FieldCondition(key="status", match=status_match))
    if not conditions:
        return None
    return models.Filter(must=conditions)


def search(
    client: QdrantClient,
    query_text: str,
    *,
    tags: list[str] | None = None,
    folder: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[VectorHit]:
    dense_vector = embed_dense([query_text])[0]
    sparse_vector = embed_sparse([query_text])[0]

    query_filter = _build_filter(tags, folder, status)

    # The filter must be set on EVERY Prefetch, not only on the outer
    # query_filter: in embedded (":memory:") mode, an outer-only filter was
    # empirically found to be silently ignored (design doc §0). query_filter
    # is still set too, for defense-in-depth/documented intent.
    result = client.query_points(
        collection_name=COLLECTION_ALIAS,
        prefetch=[
            models.Prefetch(
                query=dense_vector,
                using=_DENSE_VECTOR_NAME,
                limit=limit,
                filter=query_filter,
            ),
            models.Prefetch(
                query=models.SparseVector(
                    indices=sparse_vector.indices, values=sparse_vector.values
                ),
                using=_SPARSE_VECTOR_NAME,
                limit=limit,
                filter=query_filter,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        query_filter=query_filter,
        limit=limit,
        with_payload=True,
    )

    hits = []
    for rank, point in enumerate(result.points, start=1):
        payload = point.payload if point.payload is not None else {}
        hits.append(
            VectorHit(
                chunk_id=payload.get("chunk_id"),
                note_id=payload["note_id"],
                qdrant_point_id=str(point.id),
                rank=rank,
            )
        )
    return hits

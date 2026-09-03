"""Qdrant collection lifecycle, point upsert/delete (design doc §2.4;
DATA_MODEL.md §3; SECURITY_MODEL.md TB-8).

All application code addresses the collection exclusively through
`COLLECTION_ALIAS` -- the versioned collection name below is an
implementation detail of `ensure_collection` and must never leak out.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from huey import Huey
from huey.exceptions import TaskLockedException
from qdrant_client import QdrantClient, models

from athena.indexing.chunking import Chunk
from athena.indexing.embedding import SparseVector

logger = logging.getLogger(__name__)

COLLECTION_ALIAS = "athena_chunks"

# Versioned per ADR-0008: a dimension/model change gets a new name, never an
# in-place mutation of this one -- the alias is what moves.
_VERSIONED_COLLECTION_NAME = "athena_chunks_bge_m3_v1"

_DENSE_VECTOR_NAME = "dense"
_DENSE_VECTOR_SIZE = 1024
_SPARSE_VECTOR_NAME = "minicoil"

_ALIAS_LOCK_NAME = "qdrant-alias-mutation"


def _create_versioned_collection(client: QdrantClient) -> None:
    client.create_collection(
        collection_name=_VERSIONED_COLLECTION_NAME,
        vectors_config={
            _DENSE_VECTOR_NAME: models.VectorParams(
                size=_DENSE_VECTOR_SIZE, distance=models.Distance.COSINE
            )
        },
        sparse_vectors_config={
            # modifier=IDF is required -- omitting it silently produces
            # meaningless miniCOIL vectors rather than raising (design doc §0).
            _SPARSE_VECTOR_NAME: models.SparseVectorParams(modifier=models.Modifier.IDF)
        },
    )
    for field_name, schema in (
        ("tags", models.PayloadSchemaType.KEYWORD),
        ("folder", models.PayloadSchemaType.KEYWORD),
        ("status", models.PayloadSchemaType.KEYWORD),
        ("note_id", models.PayloadSchemaType.INTEGER),
        ("embedding_model_version", models.PayloadSchemaType.KEYWORD),
    ):
        client.create_payload_index(
            collection_name=_VERSIONED_COLLECTION_NAME,
            field_name=field_name,
            field_schema=schema,
        )


def ensure_collection(client: QdrantClient, huey: Huey) -> None:
    if not client.collection_exists(_VERSIONED_COLLECTION_NAME):
        _create_versioned_collection(client)

    # A single atomic alias-repoint call, never a separate
    # check-then-create-then-repoint sequence (SECURITY_MODEL.md TB-8).
    # Locked so two overlapping bootstrap attempts don't race each other;
    # a lock conflict means someone else is already bootstrapping, so this
    # call is a safe no-op (the alias-repoint operation is itself
    # idempotent/atomic on the winning caller's side).
    try:
        with huey.lock_task(_ALIAS_LOCK_NAME):
            client.update_collection_aliases(
                change_aliases_operations=[
                    models.CreateAliasOperation(
                        create_alias=models.CreateAlias(
                            collection_name=_VERSIONED_COLLECTION_NAME,
                            alias_name=COLLECTION_ALIAS,
                        )
                    )
                ]
            )
    except TaskLockedException:
        logger.info(
            "Skipping alias mutation for %s -- another bootstrap is already in progress.",
            COLLECTION_ALIAS,
        )
        return


def upsert_chunks(
    client: QdrantClient,
    *,
    note_id: int,
    chunks: list[Chunk],
    dense_vectors: list[list[float]],
    sparse_vectors: list[SparseVector],
    payload_fields: dict[str, Any],
) -> list[str]:
    """Mints and returns a fresh `qdrant_point_id` per chunk.

    `chunk_id` (the SQLite `chunks.id` primary key) is deliberately NOT in
    the payload: `index_note` (design doc §2.5) calls this function *before*
    inserting the corresponding `chunks` rows, specifically so a failure here
    leaves zero partial `chunks` rows in SQLite (a stronger invariant than
    the payload's `chunk_id` convenience field, per DATA_MODEL.md §3's own
    framing of it as an optional round-trip-avoidance denormalization, not
    load-bearing -- nothing filters/deletes by it, only by `note_id`).
    """
    point_ids = [str(uuid.uuid4()) for _ in chunks]
    points = [
        models.PointStruct(
            id=point_id,
            vector={
                _DENSE_VECTOR_NAME: dense_vector,
                _SPARSE_VECTOR_NAME: models.SparseVector(
                    indices=sparse_vector.indices, values=sparse_vector.values
                ),
            },
            payload={
                **payload_fields,
                "note_id": note_id,
                "chunk_index": chunk.chunk_index,
                "content_hash": hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
            },
        )
        for point_id, chunk, dense_vector, sparse_vector in zip(
            point_ids, chunks, dense_vectors, sparse_vectors, strict=True
        )
    ]
    client.upsert(collection_name=COLLECTION_ALIAS, points=points)
    return point_ids


def delete_points_for_note(client: QdrantClient, note_id: int) -> None:
    client.delete(
        collection_name=COLLECTION_ALIAS,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key="note_id", match=models.MatchValue(value=note_id))]
            )
        ),
    )

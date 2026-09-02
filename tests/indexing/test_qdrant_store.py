from __future__ import annotations

from pathlib import Path

import pytest
from huey import SqliteHuey
from qdrant_client import QdrantClient, models

from ai_brain.indexing.chunking import Chunk
from ai_brain.indexing.embedding import SparseVector
from ai_brain.indexing.qdrant_store import (
    COLLECTION_ALIAS,
    delete_points_for_note,
    ensure_collection,
    upsert_chunks,
)


def _huey(tmp_path: Path) -> SqliteHuey:
    # SqliteHuey's storage opens a fresh connection per call -- ":memory:"
    # gives each call an empty, table-less database (same finding
    # tests/vault/conftest.py already documents). A real temp file is needed.
    return SqliteHuey(name="ai-brain-test", filename=str(tmp_path / "huey.db"))


def test_ensure_collection_creates_expected_vector_and_sparse_config(tmp_path: Path) -> None:
    client = QdrantClient(":memory:")
    huey = _huey(tmp_path)

    ensure_collection(client, huey)

    info = client.get_collection(COLLECTION_ALIAS)
    dense = info.config.params.vectors
    assert isinstance(dense, dict)
    assert dense["dense"].size == 1024
    assert dense["dense"].distance == models.Distance.COSINE

    sparse = info.config.params.sparse_vectors
    assert sparse is not None
    assert sparse["minicoil"].modifier == models.Modifier.IDF


def test_ensure_collection_is_idempotent(tmp_path: Path) -> None:
    client = QdrantClient(":memory:")
    huey = _huey(tmp_path)

    ensure_collection(client, huey)
    ensure_collection(client, huey)  # must not raise, must not duplicate the alias

    aliases = [a for a in client.get_aliases().aliases if a.alias_name == COLLECTION_ALIAS]
    assert len(aliases) == 1


def test_ensure_collection_alias_resolves_to_a_real_collection(tmp_path: Path) -> None:
    client = QdrantClient(":memory:")
    huey = _huey(tmp_path)

    ensure_collection(client, huey)

    assert client.collection_exists(COLLECTION_ALIAS)


# --- Integration tests: require a real Qdrant server. -----------------------
#
# Docker access is blocked in this development environment (design doc §0/§8)
# -- these are written as real, correct test code against a real server and
# are skipped, not omitted, so they are visible in the suite and can be
# unskipped by anyone with Docker access (or in a future CI environment).

_SKIP_REASON = (
    "requires a real Qdrant server; Docker access blocked in this dev "
    "environment, see docs/design/indexing-pipeline.md §8"
)


@pytest.mark.skip(reason=_SKIP_REASON)
def test_ensure_collection_alias_resolves_against_real_server(tmp_path: Path) -> None:
    client = QdrantClient(url="http://127.0.0.1:6333")
    huey = _huey(tmp_path)

    ensure_collection(client, huey)

    aliases = client.get_collection_aliases(
        client.get_collection_aliases(COLLECTION_ALIAS).aliases[0].collection_name
    )
    assert any(a.alias_name == COLLECTION_ALIAS for a in aliases.aliases)


@pytest.mark.skip(reason=_SKIP_REASON)
def test_upsert_and_delete_round_trip_against_real_server(tmp_path: Path) -> None:
    client = QdrantClient(url="http://127.0.0.1:6333")
    huey = _huey(tmp_path)
    ensure_collection(client, huey)

    chunks = [
        Chunk(text="first chunk of note 1", chunk_index=0, token_count=5),
        Chunk(text="second chunk of note 1", chunk_index=1, token_count=5),
    ]
    dense_vectors = [[0.1] * 1024, [0.2] * 1024]
    sparse_vectors = [
        SparseVector(indices=[1, 2], values=[0.5, 0.5]),
        SparseVector(indices=[3, 4], values=[0.5, 0.5]),
    ]
    payload_fields = {
        "note_path": "notes/a.md",
        "tags": ["rag"],
        "folder": "notes",
        "status": "active",
        "origin": "imported",
        "provider": None,
        "embedding_model_version": "bge-m3@abc123",
    }

    point_ids = upsert_chunks(
        client,
        note_id=1,
        chunks=chunks,
        dense_vectors=dense_vectors,
        sparse_vectors=sparse_vectors,
        payload_fields=payload_fields,
    )
    assert len(point_ids) == 2

    fetched = client.retrieve(collection_name=COLLECTION_ALIAS, ids=point_ids, with_payload=True)
    assert {p.payload["chunk_index"] for p in fetched if p.payload is not None} == {0, 1}  # type: ignore[index]

    delete_points_for_note(client, note_id=1)

    remaining = client.retrieve(collection_name=COLLECTION_ALIAS, ids=point_ids)
    assert remaining == []


@pytest.mark.skip(reason=_SKIP_REASON)
def test_delete_points_for_note_only_removes_target_note(tmp_path: Path) -> None:
    client = QdrantClient(url="http://127.0.0.1:6333")
    huey = _huey(tmp_path)
    ensure_collection(client, huey)

    chunk_a = [Chunk(text="note a chunk", chunk_index=0, token_count=3)]
    chunk_b = [Chunk(text="note b chunk", chunk_index=0, token_count=3)]
    dense = [[0.1] * 1024]
    sparse = [SparseVector(indices=[1], values=[1.0])]
    payload_a = {
        "note_path": "a.md",
        "tags": [],
        "folder": "",
        "status": "active",
        "origin": "imported",
        "provider": None,
        "embedding_model_version": "bge-m3@abc123",
    }
    payload_b = {**payload_a, "note_path": "b.md"}

    ids_a = upsert_chunks(
        client, note_id=1, chunks=chunk_a, dense_vectors=dense,
        sparse_vectors=sparse, payload_fields=payload_a,
    )
    ids_b = upsert_chunks(
        client, note_id=2, chunks=chunk_b, dense_vectors=dense,
        sparse_vectors=sparse, payload_fields=payload_b,
    )

    delete_points_for_note(client, note_id=1)

    assert client.retrieve(collection_name=COLLECTION_ALIAS, ids=ids_a) == []
    remaining_b = client.retrieve(collection_name=COLLECTION_ALIAS, ids=ids_b)
    assert len(remaining_b) == 1


@pytest.mark.skip(reason=_SKIP_REASON)
def test_sparse_vector_upsert_and_query_round_trip_with_idf_modifier(tmp_path: Path) -> None:
    client = QdrantClient(url="http://127.0.0.1:6333")
    huey = _huey(tmp_path)
    ensure_collection(client, huey)

    chunks = [Chunk(text="qdrant hybrid search with sparse vectors", chunk_index=0, token_count=6)]
    dense = [[0.1] * 1024]
    sparse = [SparseVector(indices=[10, 20, 30], values=[1.5, 2.0, 0.5])]
    payload_fields = {
        "note_path": "a.md",
        "tags": [],
        "folder": "",
        "status": "active",
        "origin": "imported",
        "provider": None,
        "embedding_model_version": "bge-m3@abc123",
    }

    upsert_chunks(
        client, note_id=1, chunks=chunks, dense_vectors=dense,
        sparse_vectors=sparse, payload_fields=payload_fields,
    )

    results = client.query_points(
        collection_name=COLLECTION_ALIAS,
        using="minicoil",
        query=models.SparseVector(indices=[10, 20], values=[1.0, 1.0]),
        limit=5,
    )
    assert len(results.points) == 1
    assert results.points[0].score > 0

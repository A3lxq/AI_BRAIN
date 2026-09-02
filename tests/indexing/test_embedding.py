"""Tests for `ai_brain.indexing.embedding`.

These tests run the real `sentence-transformers` (BGE-M3) and `fastembed`
(miniCOIL) models against real text — no mocking — since the point is to
verify real embedding behavior (dimension, determinism, batching) rather
than the shape of a mock. The first run downloads both models from
Hugging Face Hub (a few hundred MB to a few GB combined) and caches them
locally; subsequent runs are fast.
"""

from __future__ import annotations

from ai_brain.indexing.embedding import (
    EMBEDDING_MODEL_VERSION,
    SparseVector,
    embed_dense,
    embed_sparse,
)


class TestEmbeddingModelVersion:
    def test_contains_pinned_revision_not_main(self) -> None:
        assert "@" in EMBEDDING_MODEL_VERSION
        prefix, _, revision_prefix = EMBEDDING_MODEL_VERSION.partition("@")
        assert prefix == "bge-m3"
        assert revision_prefix != "main"
        assert revision_prefix != ""
        # A real (prefix of a) git commit SHA: fixed-length lowercase hex.
        assert len(revision_prefix) >= 8
        assert all(char in "0123456789abcdef" for char in revision_prefix)


class TestEmbedDense:
    def test_same_text_twice_is_deterministic(self) -> None:
        # Two separate calls, not two copies within one batch: encoding the
        # same text alongside itself in one batch call was empirically found
        # to differ at ~1e-7 (float32 batch-padding rounding noise from the
        # underlying torch/CPU inference path, not a bug in this module) --
        # a real finding, not something to paper over with a tolerance. Two
        # independent calls are the actually meaningful determinism
        # guarantee (no hidden randomness/dropout) and are bit-for-bit equal.
        text = "AI_BRAIN indexes an Obsidian vault for retrieval."
        [first] = embed_dense([text])
        [second] = embed_dense([text])
        assert first == second

    def test_output_dimension_is_1024(self) -> None:
        [vector] = embed_dense(["a short sentence about knowledge management"])
        assert len(vector) == 1024

    def test_returns_plain_python_lists_of_floats(self) -> None:
        [vector] = embed_dense(["plain python list check"])
        assert isinstance(vector, list)
        assert all(isinstance(component, float) for component in vector)

    def test_batch_of_distinct_texts_produces_distinct_vectors(self) -> None:
        texts = [
            "The quick brown fox jumps over the lazy dog.",
            "Qdrant stores dense and sparse vectors for hybrid search.",
            "Obsidian vaults are the source of truth for AI_BRAIN.",
        ]
        vectors = embed_dense(texts)
        assert len(vectors) == 3
        assert vectors[0] != vectors[1]
        assert vectors[0] != vectors[2]
        assert vectors[1] != vectors[2]


class TestEmbedSparse:
    def test_same_text_twice_is_deterministic(self) -> None:
        text = "miniCOIL produces sparse vectors weighted by token frequency."
        [first] = embed_sparse([text])
        [second] = embed_sparse([text])
        assert first == second

    def test_indices_and_values_are_same_length_and_non_empty(self) -> None:
        [vector] = embed_sparse(["Qdrant collections use aliases for zero-downtime cutover."])
        assert isinstance(vector, SparseVector)
        assert len(vector.indices) == len(vector.values)
        assert len(vector.indices) > 0

    def test_indices_and_values_are_plain_python_lists(self) -> None:
        [vector] = embed_sparse(["plain python list check for sparse vectors"])
        assert isinstance(vector.indices, list)
        assert isinstance(vector.values, list)
        assert all(isinstance(index, int) for index in vector.indices)
        assert all(isinstance(value, float) for value in vector.values)

    def test_batch_of_distinct_texts_produces_distinct_vectors(self) -> None:
        texts = [
            "The quick brown fox jumps over the lazy dog.",
            "Qdrant stores dense and sparse vectors for hybrid search.",
            "Obsidian vaults are the source of truth for AI_BRAIN.",
        ]
        vectors = embed_sparse(texts)
        assert len(vectors) == 3
        assert vectors[0] != vectors[1]
        assert vectors[0] != vectors[2]
        assert vectors[1] != vectors[2]

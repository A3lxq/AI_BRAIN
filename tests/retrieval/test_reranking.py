from __future__ import annotations

import pytest

from athena.retrieval import reranking
from athena.retrieval.reranking import RerankCandidate, rerank


def test_reranker_revision_is_pinned() -> None:
    assert reranking._RERANKER_REVISION != "main"
    assert len(reranking._RERANKER_REVISION) == 40  # a full git commit SHA


def test_empty_candidates_returns_empty_without_loading_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail() -> None:
        raise AssertionError("_get_reranker should not be called for an empty candidate list")

    monkeypatch.setattr(reranking, "_get_reranker", _fail)

    assert rerank("query", []) == []


def test_relevant_passage_ranks_above_irrelevant_one() -> None:
    query = "What is the capital of France?"
    candidates = [
        RerankCandidate(chunk_id=101, chunk_text="Bananas are a good source of potassium."),
        RerankCandidate(chunk_id=102, chunk_text="Paris is the capital of France."),
    ]

    results = rerank(query, candidates, top_k=2)

    assert len(results) == 2
    assert results[0].chunk_id == 102


def test_corpus_id_to_chunk_id_round_trips() -> None:
    query = "What is the capital of France?"
    candidates = [
        RerankCandidate(chunk_id=101, chunk_text="Paris is the capital of France."),
        RerankCandidate(chunk_id=102, chunk_text="Bananas are a good source of potassium."),
        RerankCandidate(chunk_id=103, chunk_text="The sky is blue on a clear day."),
    ]

    results = rerank(query, candidates, top_k=3)

    result_chunk_ids = {r.chunk_id for r in results}
    assert result_chunk_ids == {101, 102, 103}
    # An accidental "just returned the index" bug would produce {0, 1, 2},
    # which shares no elements with the real chunk_id space chosen here.
    assert result_chunk_ids.isdisjoint({0, 1, 2})

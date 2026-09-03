"""Cross-encoder reranking via `BAAI/bge-reranker-v2-m3` (docs/design/
retrieval-pipeline.md §0/§2.4; ADR-0008).

One process-lifetime `CrossEncoder` singleton, lazily constructed on first
use -- same pattern as `ai_brain.indexing.embedding`'s dense/sparse model
singletons.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import torch
from sentence_transformers import CrossEncoder

# Resolved via `huggingface_hub.HfApi().model_info("BAAI/bge-reranker-v2-m3").sha`
# (cross-checked against the raw HF HTTP API directly) on 2026-09-02. Pinned
# per SECURITY_MODEL.md P1 item 15 -- never "main". Re-resolve the same way
# on any future reranker version bump.
_RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"


@dataclass(frozen=True)
class RerankCandidate:
    chunk_id: int
    chunk_text: str


@dataclass(frozen=True)
class RerankedResult:
    chunk_id: int
    score: float


_reranker: CrossEncoder | None = None


def _get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoder(
            "BAAI/bge-reranker-v2-m3",
            revision=_RERANKER_REVISION,
            activation_fn=torch.nn.Sigmoid(),
        )
    return _reranker


def rerank(
    query_text: str, candidates: list[RerankCandidate], *, top_k: int = 10
) -> list[RerankedResult]:
    if not candidates:
        return []

    model = _get_reranker()
    ranked = model.rank(query_text, [c.chunk_text for c in candidates], top_k=top_k)

    results: list[RerankedResult] = []
    for entry in ranked:
        corpus_id = cast(int, entry["corpus_id"])
        score = cast(float, entry["score"])
        results.append(RerankedResult(chunk_id=candidates[corpus_id].chunk_id, score=score))
    return results

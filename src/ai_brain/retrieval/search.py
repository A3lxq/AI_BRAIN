"""The retrieval orchestrator (docs/design/retrieval-pipeline.md §2.7/§3).

vector_search + keyword_search (chunks + notes) -> fuse -> rerank top-N ->
build_context. No MCP dependency (CLAUDE.md rule 15) -- a future Phase 6
`vault_search` tool calls this directly.
"""

from __future__ import annotations

import logging

import aiosqlite
from qdrant_client import QdrantClient

from ai_brain.db.repository import chunks as chunks_repo
from ai_brain.db.repository import notes as notes_repo
from ai_brain.retrieval import keyword_search, vector_search
from ai_brain.retrieval.context import ContextResult, build_context
from ai_brain.retrieval.fusion import fuse
from ai_brain.retrieval.reranking import RerankCandidate, RerankedResult, rerank
from ai_brain.retrieval.vector_search import VectorHit

logger = logging.getLogger(__name__)

__all__ = ["search", "search_ranked_note_paths"]


def _vector_search_or_degrade(
    qdrant_client: QdrantClient,
    query_text: str,
    *,
    tags: list[str] | None,
    folder: str | None,
    status: str | None,
    limit: int,
) -> list[VectorHit]:
    try:
        return vector_search.search(
            qdrant_client, query_text, tags=tags, folder=folder, status=status, limit=limit
        )
    except Exception:
        logger.warning(
            "Qdrant unreachable during search -- falling back to keyword-only fusion",
            exc_info=True,
        )
        return []


async def _reranked_results(
    conn: aiosqlite.Connection,
    qdrant_client: QdrantClient,
    query_text: str,
    *,
    tags: list[str] | None,
    folder: str | None,
    status: str | None,
    fusion_pool_size: int,
    rerank_pool_size: int,
    top_k: int,
) -> list[RerankedResult]:
    vector_hits = _vector_search_or_degrade(
        qdrant_client, query_text, tags=tags, folder=folder, status=status, limit=fusion_pool_size
    )
    chunk_keyword_hits = await keyword_search.search_chunks(
        conn, query_text, tags=tags, folder=folder, status=status, limit=fusion_pool_size
    )
    note_title_hits = await keyword_search.search_notes(
        conn, query_text, tags=tags, folder=folder, status=status, limit=fusion_pool_size
    )

    fused = await fuse(conn, vector_hits, chunk_keyword_hits, note_title_hits)

    pool = fused[:rerank_pool_size]
    chunk_rows = await chunks_repo.get_by_ids(conn, [r.chunk_id for r in pool])
    text_by_id = {row.id: row.chunk_text for row in chunk_rows}
    candidates = [
        RerankCandidate(chunk_id=r.chunk_id, chunk_text=text_by_id[r.chunk_id])
        for r in pool
        if r.chunk_id in text_by_id
    ]

    return rerank(query_text, candidates, top_k=top_k)


async def search(
    conn: aiosqlite.Connection,
    qdrant_client: QdrantClient,
    query_text: str,
    *,
    tags: list[str] | None = None,
    folder: str | None = None,
    status: str | None = None,
    fusion_pool_size: int = 50,
    rerank_pool_size: int = 20,
    top_k: int = 10,
    max_context_tokens: int = 4096,
) -> ContextResult:
    reranked = await _reranked_results(
        conn, qdrant_client, query_text, tags=tags, folder=folder, status=status,
        fusion_pool_size=fusion_pool_size, rerank_pool_size=rerank_pool_size, top_k=top_k,
    )
    return await build_context(conn, reranked, max_tokens=max_context_tokens)


async def search_ranked_note_paths(
    conn: aiosqlite.Connection,
    qdrant_client: QdrantClient,
    query_text: str,
    *,
    tags: list[str] | None = None,
    folder: str | None = None,
    status: str | None = None,
    fusion_pool_size: int = 50,
    rerank_pool_size: int = 20,
    top_k: int = 10,
) -> list[str]:
    """A ranked list of vault-relative note paths, deduplicated in rank order,
    independent of context.build_context's token-budget truncation -- the
    interface docs/design/retrieval-pipeline.md §2.6's evaluation harness
    needs, since Recall@K/Precision@K operate on a fixed top-K by rank, not
    on however many chunks happen to fit in a token budget."""
    reranked = await _reranked_results(
        conn, qdrant_client, query_text, tags=tags, folder=folder, status=status,
        fusion_pool_size=fusion_pool_size, rerank_pool_size=rerank_pool_size, top_k=top_k,
    )
    chunk_rows = await chunks_repo.get_by_ids(conn, [r.chunk_id for r in reranked])
    note_id_by_chunk = {row.id: row.note_id for row in chunk_rows}

    paths: list[str] = []
    seen_note_ids: set[int] = set()
    for result in reranked:
        note_id = note_id_by_chunk.get(result.chunk_id)
        if note_id is None or note_id in seen_note_ids:
            continue
        note = await notes_repo.get_by_id(conn, note_id)
        if note is None:
            continue
        seen_note_ids.add(note_id)
        paths.append(note.path)
    return paths

# Session 005 — Phase 0 RAG Orchestration Research

- **Date:** 2026-08-22
- **Phase:** 0 — Architecture & Research
- **Depends on:** Session 003 (ADR-0001, Python), Session 004 (ADR-0002, Huey/SQLite)

## Objective

Research and decide AI_BRAIN's RAG orchestration approach: structure-aware chunking, embeddings, hybrid retrieval + fusion + reranking, multi-LLM-provider abstraction, provenance/lineage, and duplicate detection — evaluating LangChain, LlamaIndex, a hand-rolled composable-primitives approach, and a middle-ground cherry-picking pattern.

## Completed Work

- Researched all four options against architecture fit, decoupling, chunking, provenance, duplicate detection, security, testability, maintainability, maturity, multi-provider abstraction quality, and learning curve, using current 2026 primary sources.
- Wrote `docs/research/2026-08-22_rag_orchestration.md` following the Documentation Standards research-doc structure.
- Drafted `docs/adr/0003-rag-orchestration-approach.md` recommending hand-rolled composable primitives, initially status Proposed.
- Maintainer reviewed and **accepted ADR-0003 as proposed** — status now Accepted.

## Key Decision

AI_BRAIN's RAG pipeline will be built from **hand-rolled composable primitives**: `qdrant-client` (already decided) + `chonkie` (chunking) + SQLite FTS5 (keyword search) + a small hand-written cross-store fusion module + `sentence-transformers` (embeddings/reranking) + a small `Protocol`-based multi-provider LLM adapter + a provenance schema built against W3C PROV + duplicate detection via hash/MinHash-LSH/cosine similarity. No LangChain or LlamaIndex adoption.

## Key Findings

- **LangChain has a disclosed CVE (CVE-2025-68664, CVSS 9.3)**: `dumps()`/`dumpd()` failed to escape attacker-controlled dicts, letting retrieved/tool-output data be deserialized as trusted framework objects — a real instance of the exact trust-boundary risk AI_BRAIN's security model targets.
- **LlamaIndex's core carries a heavy dependency tree** (SQLAlchemy, NetworkX, NLTK, tiktoken) and a **global mutable `Settings` singleton**, flagged by its own community as a poor fit for concurrent systems — conflicts with the already-accepted asyncio/Huey concurrency model.
- Every genuinely hard sub-problem (structure-aware chunking, hybrid fusion, reranking, lexical dedup) already has a mature, narrowly-scoped library — full framework adoption buys comparatively little.

## Files Changed

- `docs/research/2026-08-22_rag_orchestration.md` (new)
- `docs/adr/0003-rag-orchestration-approach.md` (new, Proposed → Accepted)
- `CURRENT_STATE.md` (updated)
- `NEXT_SESSION.md` (updated)
- `CHANGELOG.md` (updated)
- `SESSION_LOG.md` (updated)
- `docs/sessions/2026-08-22_phase0-rag-orchestration.md` (this file, new)

## Tests

None — research-only session per CLAUDE.md Phase discipline; no code was written.

## Unresolved Issues (carried forward as implementation-time checks, not blockers)

- Verify `chonkie`'s YAML-frontmatter handling early in Phase 1.
- Decide `litellm` (narrow/pinned) vs. hand-rolled `Protocol` adapter for multi-provider LLM access.
- Decide hand-written RRF vs. `ranx` for cross-store fusion.
- Design the provenance schema against W3C PROV before the indexing subsystem is implemented.

## Next Steps

Proceed to the next Phase 0 research queue items per `NEXT_SESSION.md`: SQLite access layer (including the Huey-database-sharing question from ADR-0002), Git automation library, MCP tool contract design, Qdrant deployment specifics, embeddings model choice, filesystem event architecture.

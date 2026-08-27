# ADR-0003: RAG Orchestration Approach for AI_BRAIN

- **ID:** ADR-0003
- **Title:** RAG Orchestration Approach for AI_BRAIN
- **Status:** Accepted
- **Date proposed:** 2026-08-22
- **Date accepted:** 2026-08-22
- **Depends on:** ADR-0001 (runtime: Python, accepted), ADR-0002 (job queue: Huey/SQLite, accepted), and the already-accepted choice of `qdrant-client` as the vector DB client

## Context

AI_BRAIN needs an orchestration approach for structure-aware Markdown chunking, embeddings, hybrid retrieval (vector + keyword + metadata, fused) with reranking, context construction, multi-LLM-provider abstraction, provenance/lineage tracking, duplicate detection with explicit merge policies, and treating retrieved content as untrusted — while keeping internal modules decoupled from MCP transport and testable standalone, per the constitution's "small composable modules" principle.

Four options were researched: full adoption of LangChain+LangGraph, full adoption of LlamaIndex, a hand-rolled composable-primitives approach, and a middle-ground pattern of cherry-picking single components from either framework. Full findings: [`docs/research/2026-08-22_rag_orchestration.md`](../research/2026-08-22_rag_orchestration.md).

Two concrete findings shaped the recommendation:
- **LangChain has a disclosed CVE (CVE-2025-68664, CVSS 9.3)**: `langchain-core`'s `dumps()`/`dumpd()` failed to escape attacker-controlled dicts, allowing retrieved/tool-output data to be deserialized as trusted framework objects — a real instance of exactly the trust-boundary conflation AI_BRAIN's security model is architected to prevent.
- **LlamaIndex's core carries a heavy dependency tree** (SQLAlchemy, NetworkX, NLTK, tiktoken) and a **global mutable `Settings` singleton**, flagged by its own community ([Issue #11543](https://github.com/run-llama/llama_index/issues/11543)) as a poor fit for concurrent systems — directly relevant given AI_BRAIN's asyncio/Huey concurrency model.

## Decision

**Accepted:** Build AI_BRAIN's RAG pipeline from **hand-rolled composable primitives** on top of already-narrow, purpose-built libraries, rather than adopting LangChain, LlamaIndex, or a framework component middle-ground:

- `qdrant-client` (already decided) for vector search + native in-store hybrid fusion (RRF/DBSF).
- `chonkie` for structure-aware Markdown chunking (verify frontmatter handling during Phase 1 prototyping).
- SQLite FTS5 (stdlib) for keyword search.
- A small hand-written cross-store fusion module (RRF; `ranx` as an alternative to hand-writing if preferred at implementation time).
- `sentence-transformers` for embeddings and cross-encoder reranking.
- A small `Protocol`-based multi-provider LLM adapter over the official OpenAI/Anthropic/Google/Ollama SDKs (or `litellm` used narrowly/pinned, without its proxy/gateway component, if the adapter surface proves large enough to warrant it — a decision left to implementation time).
- A provenance schema designed against the W3C PROV (PROV-DM/PROV-O) entity/activity/agent/derivation model, custom-built.
- Duplicate detection via content hash + `datasketch` MinHash-LSH (lexical) + embedding cosine similarity (semantic), with a hand-written merge-policy layer.

The maintainer reviewed the research and comparison and accepted this ADR as proposed on 2026-08-22.

## Alternatives Considered

| Option | Verdict |
|---|---|
| LangChain + LangGraph | Rejected — reintroduces vendor indirection around Qdrant that ADR-0001/0002's stack already avoided by choosing `qdrant-client` directly; carries a disclosed CVE (CVE-2025-68664) directly in the untrusted-retrieved-content trust boundary AI_BRAIN's security model targets; LangGraph would duplicate Huey's already-accepted role as job/orchestration layer. |
| LlamaIndex | Rejected as primary — cleaner package separation than LangChain (Workflows is now a standalone package), but core's dependency footprint (SQLAlchemy/NetworkX/NLTK/tiktoken) and global `Settings` singleton actively conflict with the accepted asyncio/Huey concurrency model. Native provenance support (structural `NodeRelationship` lineage) is a genuine partial plus but doesn't cover AI_BRAIN's required fields (source/provider/transform history) regardless. |
| Middle-ground (cherry-pick one component, e.g. just a text splitter) | Rejected — real and used in practice, but neither framework is engineered to be dependency-light when consumed this way: LangChain's splitter package has had a dependency-hygiene bug and inherits `langchain-core`'s CVE-linked object model; LlamaIndex's node parsers drag in 27 required packages. A purpose-built chunker (`chonkie`) achieves the same result without either cost. |

## Rationale

1. **Every genuinely hard sub-problem already has a mature, narrowly-scoped library.** Structure-aware chunking (`chonkie`), hybrid fusion (Qdrant-native RRF/DBSF + a trivial cross-store fusion layer), reranking (`sentence-transformers`' `CrossEncoder`), and lexical dedup (`datasketch`) are all covered by focused, actively-maintained libraries — a general-purpose RAG framework buys comparatively little beyond what these already provide.
2. **Both frameworks' native provenance/dedup support falls well short of AI_BRAIN's requirements regardless of adoption.** LangChain has none beyond a metadata dict; LlamaIndex has partial structural lineage but nothing for source/provider/transformation history. AI_BRAIN must build this custom either way, so framework adoption doesn't reduce this work.
3. **Architecture fit favors the hand-rolled path on two already-accepted decisions.** Using `qdrant-client` directly avoids LangChain's vendor-indirection wrapper; avoiding LlamaIndex's `Settings` singleton avoids fighting the asyncio/Huey concurrency model accepted in ADR-0002.
4. **Security posture favors the hand-rolled path.** LangChain's disclosed CVE is a real, non-hypothetical instance of the exact trust-boundary risk the constitution's security model is designed around; LlamaIndex explicitly disclaims prompt-injection defense as out of scope. Either way, AI_BRAIN must build its own retrieved-content/instruction separation — the hand-rolled path doesn't add a framework's additional exposure on top of that necessary work.
5. **Testability and maintainability favor small, plain modules** with no framework runtime to stub, directly serving the constitution's "small composable modules" principle and Article 4 ("tests are part of the feature").
6. **`litellm`'s 2026 security incidents** (a supply-chain compromise and an exploited SQL-injection CVE) argue against adopting it as core multi-provider infrastructure; a small hand-rolled adapter or narrow/pinned `litellm` use is preferred.

## Consequences

- The RAG pipeline (chunking → embedding → indexing → retrieval → fusion → reranking → context construction) will be built as a set of small, independently-testable Python modules, each callable without any MCP or job-queue dependency, per the constitution's decoupling rule.
- `chonkie`'s frontmatter handling must be verified early in Phase 1; if inadequate, pair with `markdown-it-py`'s frontmatter plugin or strip frontmatter as a preprocessing step before chunking.
- The multi-provider LLM adapter design (hand-rolled `Protocol` vs. narrow `litellm` use) is deferred to implementation time as a low-stakes decision, not requiring its own ADR.
- The cross-store fusion implementation (hand-written RRF vs. `ranx`) is likewise deferred to implementation time.
- The provenance schema must be designed against the W3C PROV model before the indexing subsystem is implemented, per Constitution Article 2 (every significant subsystem needs a design doc covering purpose/interfaces/failure modes before coding).
- Neither LangChain nor LlamaIndex is adopted for any purpose, including orchestration/agent workflows — Huey (ADR-0002) remains AI_BRAIN's sole job/orchestration layer.
- If a future need arises that a narrow library doesn't cover well, LlamaIndex's node parsers are the more defensible one-off borrow of the two frameworks (Workflows is separable from core) — but this should be evaluated case-by-case against its dependency weight, not treated as a standing exception.

## References

See [`docs/research/2026-08-22_rag_orchestration.md`](../research/2026-08-22_rag_orchestration.md) §12 for the full primary-source citation list.

## Open Questions

Resolved: the maintainer accepted this ADR as proposed on 2026-08-22, with no modifications requested.

Remaining open item, carried forward as an implementation-time check rather than a blocking question: verify `chonkie`'s frontmatter handling early in Phase 1; if inadequate, pair with `markdown-it-py`'s frontmatter plugin or strip frontmatter as a preprocessing step before chunking.

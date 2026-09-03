# Research: RAG Orchestration Approach for ATHENA AI-BRAIN

- **Research date:** 2026-08-22
- **Researcher:** Claude Code (ATHENA AI-BRAIN Phase 0)
- **Status:** Candidate evaluation — feeds ADR-0003 (RAG orchestration approach)
- **Depends on:** ADR-0001 (Python runtime), ADR-0002 (Huey/SQLite job queue), and the already-accepted choice of `qdrant-client` as the vector DB client

## 1. Executive Summary

Four options were evaluated: full adoption of **LangChain+LangGraph**, full adoption of **LlamaIndex**, a **hand-rolled composable-primitives** approach built on already-narrow, purpose-built libraries, and a **middle-ground** pattern of cherry-picking single components from either framework. The research surfaced two concrete, non-hypothetical findings that shape the recommendation: LangChain has a **disclosed CVE (CVE-2025-68664, CVSS 9.3)** in exactly the untrusted-retrieved-content trust boundary ATHENA AI-BRAIN's security model is built around, and LlamaIndex's core carries a heavy dependency tree (SQLAlchemy, NetworkX, NLTK, tiktoken) plus a **global mutable `Settings` singleton** that actively conflicts with ATHENA AI-BRAIN's asyncio/Huey concurrency model. Every genuinely hard sub-problem ATHENA AI-BRAIN needs (structure-aware chunking, hybrid fusion, reranking, lexical dedup) already has a mature, narrowly-scoped library doing exactly that piece — so a hand-rolled composition of those pieces is not "reinventing the wheel," it's the better-fitting architecture.

## 2. Problem Being Solved

ATHENA AI-BRAIN needs an orchestration approach for: structure-aware/semantic Markdown chunking, embeddings generation with a pluggable provider interface, hybrid retrieval (vector + keyword + metadata + optional graph, fused), reranking, context construction, multi-LLM-provider abstraction (without provider-specific code leaking into core logic), provenance/lineage tracking, duplicate detection with explicit merge policies, and treating retrieved content as untrusted (prompt-injection mitigation) — all while keeping internal modules decoupled from the MCP transport and usable/testable standalone, per the constitution's "small composable modules" principle.

## 3. Technology Overview

Both major Python RAG frameworks reached architectural milestones in the last year: LangChain and LangGraph hit 1.0 GA on 2025-10-22, with LangGraph's graph/state runtime becoming the execution layer under LangChain's agent API. LlamaIndex has been modular since its v0.10 refactor (Feb 2024) — `llama-index-core` plus 300+ separate integration packages — and as of 2025-06-30 spun its "Workflows" orchestration layer out into a fully separate package (`llama-index-workflows`), decoupled from core.

## 4. Architecture Fit

- **Qdrant-native hybrid fusion**: Qdrant has supported server-side hybrid fusion since v1.10 (RRF, parameterizable `k` since v1.16, weighted RRF since v1.17; DBSF since v1.11) via `qdrant-client`'s `prefetch` + `RrfQuery`/`FusionQuery` — but this only fuses signals living *inside* Qdrant (dense + sparse vectors in the same collection). It does not reach SQLite FTS5, so a cross-store fusion layer is required regardless of framework choice.
- **Chunking**: neither framework has native YAML-frontmatter-aware Markdown chunking; both have documented rough edges on heading/structure parsing. `chonkie` (purpose-built, actively maintained, v1.7.0 as of 2026-07-07) directly covers the structure-aware chunking requirement with dedicated `MarkdownChef`/`RecursiveChunker(recipe="markdown")`/`CodeChunker`/`TableChunker` — a dedicated chunker is needed either way, so framework choice doesn't reduce this work.
- **Fusion + reranking**: Reciprocal Rank Fusion is a ~20-line algorithm (or the small dedicated `ranx` library if a tested implementation is preferred over hand-writing); `sentence-transformers`' `CrossEncoder` is the standard reranker regardless of orchestration choice.
- **Multi-LLM-provider abstraction**: `litellm` was previously identified as a candidate, but this research surfaced a **2026 supply-chain compromise** (malicious PyPI packages live ~40 minutes, March 2026) and a **critical SQL-injection CVE exploited within 36 hours of disclosure** (April 2026) — this argues for either narrow/pinned use as a thin call-shim (not its proxy/gateway server) or a small hand-rolled `Protocol`-based adapter over the four providers' stable official SDKs.

## 5. Alternatives Considered

| Option | Summary |
|---|---|
| LangChain + LangGraph (full adoption) | Package split (`langchain-core`/`langchain`/partner packages/`langgraph`) is genuinely decoupled in principle, but `langchain-qdrant` reintroduces vendor indirection around Qdrant that was already avoided by choosing `qdrant-client` directly. LangGraph would duplicate Huey's role as job/orchestration layer. |
| LlamaIndex (full adoption) | Cleaner separation since Workflows became a standalone package, but core's dependency footprint is heavy and its `Settings` singleton is global mutable state, flagged by LlamaIndex's own community as a poor fit for concurrent systems ([Issue #11543](https://github.com/run-llama/llama_index/issues/11543)). |
| Hand-rolled composable primitives | Compose `qdrant-client` (already decided) + `chonkie` + SQLite FTS5 + a small hand-written fusion module + `sentence-transformers` + a small `Protocol`-based multi-provider LLM adapter. Every hard sub-problem already has a mature, narrow library; the genuinely custom work (cross-store fusion glue, provenance schema, dedup policy) is small and testable. |
| Middle-ground (cherry-pick one component from a framework) | Real and documented as usable (LangChain's `langchain-text-splitters` is independently packaged; LlamaIndex's node parsers work standalone), but neither framework is engineered to be dependency-light when consumed this way — LangChain's splitter package has had a dependency-hygiene bug ([#32747](https://github.com/langchain-ai/langchain/issues/32747)) and importing anything touching `langchain-core` inherits the CVE-linked object/serialization model; LlamaIndex's node parsers drag in 27 required packages. A purpose-built chunker (`chonkie`) avoids both problems for the one piece most likely to be cherry-picked. |

## 6. Comparison Against Evaluation Criteria

| Criterion | LangChain+LangGraph | LlamaIndex | Hand-rolled | Middle-ground |
|---|---|---|---|---|
| Hybrid retrieval + fusion + reranking | Good (`EnsembleRetriever`, RRF), but relocating into `langchain-classic` (deprecation signal) | Good (`QueryFusionRetriever`, RRF), overlaps with Qdrant-native fusion | Fully achievable: Qdrant-native RRF/DBSF in-store + trivial/`ranx` cross-store RRF + `sentence-transformers` reranking | Same primitives as hand-rolled if only cherry-picking retrieval pieces |
| Decoupling from vector store/LLM provider | Package split is real, but `langchain-qdrant` reintroduces vendor indirection already rejected | Cleaner (Workflows now separate), but still a full integration-package model | Maximal — `qdrant-client` used directly, no wrapper layer | Risk of inheriting LangChain's core object model or LlamaIndex's global `Settings` |
| Structure-aware Markdown chunking | Partial (headings/code), no frontmatter support, documented bugs (#22256, #22738) | Partial (headings/code/tables), no frontmatter support | Strong: `chonkie` purpose-built, actively maintained | Same as hand-rolled if choosing `chonkie` instead |
| Provenance/lineage | None native beyond a metadata dict | Partial: native structural lineage (`NodeRelationship`), no source/provider/transform fields | None native, but W3C PROV (PROV-DM/PROV-O) gives a design scaffold used by other 2026 RAG-provenance writeups | Same as chosen framework/hand-rolled |
| Duplicate detection | Exact-hash only (`SQLRecordManager`) | Exact-hash only (`IngestionPipeline`) | Full stack achievable: hash + `datasketch` MinHash-LSH (lexical) + embedding cosine (semantic) | Fully custom regardless |
| Security / untrusted-retrieval handling | **Disclosed CVE-2025-68664 (CVSS 9.3)** directly on point — `dumps()`/`dumpd()` failed to escape attacker-controlled dicts, allowing retrieved/tool-output data to be deserialized as trusted framework objects | Explicitly **out of scope by design** (`SECURITY.md` disclaims prompt-injection defense, assumes trusted execution environment) | Fully ATHENA AI-BRAIN's own responsibility either way — no framework abstraction layer to leak through | Inherits the chosen framework's exposure |
| Testability | Good at component level; full LangGraph graphs harder | Plain classes/synchronous methods, favorable in isolation | Best — plain functions/small classes, no framework runtime to stub | Depends on cherry-picked surface |
| Maintainability / lock-in / dependency footprint | Moderate; real version-churn history pre-1.0, 1.0 stability promise ~10 months old | Heavier core tree (SQLAlchemy/NetworkX/NLTK/tiktoken); packaging churn history (full rename from "GPT Index", v0.10 refactor) | Best — minimal, purpose-scoped dependencies per problem | Worse than pure hand-rolled for the same sub-problem in either framework |
| Multi-LLM-provider abstraction | Clean per-provider partner packages, but the full framework wants to own orchestration | Similar per-provider integration packages | `litellm` viable but had 2026 supply-chain + SQLi incidents — narrow/pinned use or hand-rolled `Protocol` adapter preferred | N/A — orthogonal to chunking/retrieval cherry-picking |
| Maturity 2026 | Very active; 1.0 GA ~10 months old; documented 2026 trend of production teams migrating off LangChain to raw SDKs | Very active, 51.5k+ stars, frequent releases | All named pieces (Qdrant, `sentence-transformers`, `datasketch`, SQLite) are long-lived, stable | N/A |

## 7. ATHENA AI-BRAIN Relevance

The hand-rolled path directly serves several constitution rules simultaneously: it avoids the vendor-indirection layer around Qdrant that ADR-0001/ADR-0002's stack already rejected, it keeps every module a plain, independently-testable unit with no framework runtime to stub, and it sidesteps LlamaIndex's global-state pattern that would otherwise fight the asyncio/Huey concurrency model. Provenance and duplicate-detection requirements (both explicitly named in the master specification) are only thinly served by either framework regardless — ATHENA AI-BRAIN must build its own schema/policy either way, so framework adoption buys little there.

## 8. Security

LangChain's CVE-2025-68664 is the standout finding: it's a real, disclosed instance of the exact conflation-of-trust-boundaries bug class the constitution's security model is trying to architect around (retrieved/tool-output data being deserialized as trusted framework objects). LlamaIndex doesn't have a comparable disclosed incident, but its `SECURITY.md` explicitly disclaims prompt-injection defense as out of scope — meaning either framework leaves ATHENA AI-BRAIN needing to build its own retrieved-content/instruction separation regardless, while LangChain additionally adds a documented historical risk in the mechanism it would otherwise be trusted to handle. `litellm`'s 2026 supply-chain compromise and exploited SQLi CVE argue for narrow/pinned use (thin call-shim only, not its proxy/gateway server) or a small hand-rolled adapter instead.

## 9. Performance

Not a primary differentiator at this stage — all four paths ultimately rely on the same underlying primitives (Qdrant, `sentence-transformers`, SQLite FTS5) for the actual retrieval/ranking work. Framework overhead (LangGraph's runtime, LlamaIndex's Workflows if adopted) is avoided entirely by the hand-rolled path, consistent with "do not optimize prematurely" cutting both ways — no premature framework overhead, and no premature custom-optimization either.

## 10. Operational Concerns

- The middle-ground pattern (cherry-picking one component) is real and used in practice, but neither framework is engineered to be dependency-light when consumed this way — a purpose-built library (`chonkie`) is a cleaner choice for the component (chunking) most likely to be cherry-picked.
- If a framework component becomes attractive later, LlamaIndex's node parsers are the more defensible one-off borrow of the two (Workflows is now separable), but should still be weighed against its dependency weight and `Settings` global-state pattern case-by-case.
- LangGraph or LangChain's agent orchestration should not be adopted regardless of the chunking/retrieval decision — it would duplicate Huey's already-accepted role as the job/orchestration layer.

## 11. Recommendation

**Hand-rolled composable primitives**, built from already-narrow, purpose-built libraries rather than a general-purpose RAG framework:

- `qdrant-client` directly (already decided) for vector search + native in-store hybrid fusion (RRF/DBSF).
- `chonkie` for structure-aware Markdown chunking; verify frontmatter handling specifically (supplement with `markdown-it-py`'s frontmatter plugin if needed — a small, isolated gap, not a reason to adopt a framework).
- SQLite FTS5 (stdlib) for keyword search.
- A small hand-written cross-store fusion module (RRF, or the small `ranx` library if a tested implementation is preferred over hand-writing).
- `sentence-transformers` for embeddings and cross-encoder reranking.
- A small hand-written `Protocol`-based multi-provider LLM adapter over the official OpenAI/Anthropic/Google/Ollama SDKs, rather than full `litellm` adoption — or `litellm` used narrowly (pinned, no proxy/gateway) if the adapter surface proves large enough to warrant it.
- Provenance schema designed against the W3C PROV entity/activity/agent/derivation model (custom-built regardless of framework choice).
- Duplicate detection via content hash + `datasketch` MinHash-LSH (lexical) + embedding cosine similarity (semantic), with a hand-written merge-policy layer.

This is not a "not-invented-here" reflex: every genuinely hard sub-problem already has a mature, narrowly-scoped library, while the two frameworks would each cost real architectural fit (LangChain: vendor indirection + a disclosed CVE in the exact trust-boundary space ATHENA AI-BRAIN cares about most; LlamaIndex: heavy dependency tree + a global-state pattern that fights the accepted concurrency model) for comparatively little the narrow libraries don't already provide.

## 12. References

- LangChain/LangGraph: [langchain · PyPI](https://pypi.org/project/langchain/) · [langgraph · PyPI](https://pypi.org/project/langgraph/) · [langchain-qdrant · PyPI](https://pypi.org/project/langchain-qdrant/) · [LangChain and LangGraph Reach v1.0](https://www.langchain.com/blog/langchain-langgraph-1dot0) · [MarkdownHeaderTextSplitter reference](https://reference.langchain.com/python/langchain-text-splitters/markdown) · [Issue #22256](https://github.com/langchain-ai/langchain/issues/22256) · [Issue #22738](https://github.com/langchain-ai/langchain/issues/22738) · [Issue #32747](https://github.com/langchain-ai/langchain/issues/32747) · [EnsembleRetriever reference](https://reference.langchain.com/python/langchain-classic/retrievers/ensemble/EnsembleRetriever) · [Security Affairs — CVE writeup](https://securityaffairs.com/186185/hacking/langchain-core-vulnerability-allows-prompt-injection-and-data-exposure.html) · [Cyata "LangGrinch" CVE-2025-68664 writeup](https://cyata.ai/blog/langgrinch-langchain-core-cve-2025-68664/) · [GHSA-r399-636x-v7f6](https://github.com/langchain-ai/langchainjs/security/advisories/GHSA-r399-636x-v7f6) · [Ravoid — The LangChain Exit](https://ravoid.com/blog/langchain-exit-raw-sdk-migration-2026/)
- LlamaIndex: [llama-index-core · PyPI](https://pypi.org/project/llama-index-core/) · [Workflows 1.0 announcement](https://www.llamaindex.ai/blog/announcing-workflows-1-0-a-lightweight-framework-for-agentic-systems) · [Node Parser Modules docs](https://developers.llamaindex.ai/python/framework/module_guides/loading/node_parsers/modules/) · [Issue #17650](https://github.com/run-llama/llama_index/issues/17650) · [Reciprocal Rerank Fusion Retriever example](https://developers.llamaindex.ai/python/examples/retrievers/reciprocal_rerank_fusion/) · [v0.10 Migration Guide](https://www.llamaindex.ai/blog/llamaindex-v0-10-838e735948f8) · [SECURITY.md](https://github.com/run-llama/llama_index/blob/main/SECURITY.md) · [Settings/ServiceContext discussion Issue #11543](https://github.com/run-llama/llama_index/issues/11543)
- Hand-rolled primitives: [Qdrant Hybrid Queries docs](https://qdrant.tech/documentation/search/hybrid-queries/) · [chonkie GitHub](https://github.com/feyninc/chonkie) · [sentence-transformers · PyPI](https://pypi.org/project/sentence-transformers/) · [CrossEncoder docs](https://sbert.net/docs/package_reference/cross_encoder/cross_encoder.html) · [litellm · PyPI](https://pypi.org/project/litellm/) · [LiteLLM Security Update: Supply Chain Incident](https://docs.litellm.ai/blog/security-update-march-2026) · [markdown-it-py · PyPI](https://pypi.org/project/markdown-it-py/) · [Reciprocal Rank Fusion explained](https://blog.serghei.pl/posts/reciprocal-rank-fusion-explained/) · [datasketch documentation](https://ekzhu.com/datasketch/documentation.html) · [Data Provenance For RAG Systems](https://pklavc.com/blog/data-provenance-rag-systems/)
- Middle-ground: [langchain_text_splitters reference](https://reference.langchain.com/python/langchain-text-splitters)

## 13. Open Questions

- Does `chonkie` handle YAML frontmatter boundaries adequately, or does ATHENA AI-BRAIN need to pair it with `markdown-it-py`'s frontmatter plugin (or strip frontmatter before chunking as a preprocessing step)? Needs verification during Phase 1 prototyping.
- Should the multi-provider LLM adapter use `litellm` narrowly (pinned, no proxy) or a fully hand-rolled `Protocol`-based adapter over 4 SDKs? This is small enough to decide during implementation rather than requiring its own ADR — flagged here for that decision point.
- Should `ranx` be adopted for cross-store RRF fusion, or is a hand-written ~20-line implementation preferable per "small composable modules"? Low-stakes, can be decided at implementation time.

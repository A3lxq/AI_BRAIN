# Research: Embeddings Model Choice for AI_BRAIN

- **Research date:** 2026-08-24
- **Researcher:** Claude Code (AI_BRAIN Phase 0)
- **Status:** Candidate evaluation — feeds ADR-0008 (embeddings/sparse/reranker model choice)
- **Depends on:** ADR-0003 (RAG orchestration, committed to `sentence-transformers` and Qdrant hybrid dense+sparse fusion)

## 1. Executive Summary

The current open-weight embedding landscape (as of August 2026) is led by the Qwen3-Embedding family, with BGE-M3 remaining the de facto "production standard" default in RAG stacks despite not topping raw MTEB averages, thanks to its unique one-model dense+sparse+multi-vector output, MIT license, and long track record. For AI_BRAIN's personal-vault scale (thousands to low tens of thousands of chunks, single local machine, no GPU required), the recommendation is **`BAAI/bge-m3` for dense embeddings, `BAAI/bge-reranker-v2-m3` for reranking, and Qdrant's `miniCOIL` (via `fastembed`) for the sparse leg of hybrid fusion**, with `Qwen/Qwen3-Embedding-0.6B` + `Qwen/Qwen3-Reranker-0.6B` documented as a legitimate, closely-competitive fallback (notably with a much longer 32K-token context window). A real caveat surfaced: miniCOIL is currently English-only, a genuine gap against AI_BRAIN's explicit non-English-only assumption, requiring an explicit fallback plan. The MTEB leaderboard itself could not be fetched live (JS-rendered) — scores are drawn from official model cards, and the live leaderboard should be re-checked immediately before finalizing.

## 2. Problem Being Solved

AI_BRAIN needs a concrete dense embedding model, sparse-vector generation method (for Qdrant's hybrid fusion per ADR-0003), and reranker model — all runnable locally on a single Kali machine without requiring a dedicated GPU, multilingual-capable since a personal vault may mix languages/technical jargon, and compatible with the already-chosen `sentence-transformers` library.

## 3. Technology Overview

The Qwen3-Embedding family (0.6B/4B/8B variants) is the current open-weight MTEB leader, with the 8B variant scoring 70.58 mean on MTEB Multilingual and 75.22 on MTEB English v2 (per its own model card, dated June 2025). BGE-M3 (BAAI) remains the de facto production-standard default despite a lower raw average, due to its unique one-forward-pass dense+sparse+multi-vector (ColBERT-style) output, 100+ language support, MIT license, and 8192-token context. Google's EmbeddingGemma-300M is purpose-built for on-device/CPU use, the highest-ranking open multilingual model under 500M params on MTEB. Qdrant's own current guidance recommends **miniCOIL** (`Qdrant/minicoil-v1`, via `fastembed`) over SPLADE++ as the default sparse-vector model for new projects.

## 4. Architecture Fit

- **Qdrant hybrid fusion (ADR-0003) needs a dense vector + a sparse vector, fused by Qdrant** — not a single model's combined output. This means BGE-M3's native multi-vector output, while architecturally elegant, is redundant rather than harmful in AI_BRAIN's design: the practical integration path (dense from the embedding model, sparse from miniCOIL, fused by Qdrant) is identical regardless of which dense model is chosen.
- **Query/document asymmetry** (a classic RAG concern) is already solved within a single model: both Qwen3-Embedding and EmbeddingGemma support `prompt_name="query"` vs. document-side encoding, avoiding the need for separate query/document encoder models.
- **Matryoshka Representation Learning (MRL)** support in all top candidates gives a cheap lever to shrink dimensions for short fields (titles/tags) without a second model family — relevant if AI_BRAIN later wants per-field dimension tuning without the complexity of per-content-type models.
- **Model swappability has a real architectural cost**: Qdrant's vector size is hard-tied to the chosen model's dimension, and Qdrant does not support in-place dimension changes. Per Qdrant's own migration documentation, the supported patterns are named vectors (add a new named vector, backfill, cut over, drop the old one — Qdrant ≥1.18) or blue-green alias-based migration (parallel collection, backfill, atomic alias swap). This means AI_BRAIN must address collections via an alias, never a hardcoded name, and retain original chunk text (not just vectors) for any future re-embedding.

## 5. Alternatives Considered

| Candidate | Verdict |
|---|---|
| Qwen3-Embedding-8B | Rejected as primary — oversized for AI_BRAIN's corpus scale; native 4096-dim would inflate Qdrant storage ~4x for benchmark-scale quality gains that likely don't manifest at low-tens-of-thousands-of-chunks scale, where embedding-quality gaps compress hard versus MTEB's stress-test-scale benchmarks. |
| NV-Embed-v2 | Rejected — CC-BY-NC-4.0 license, non-commercial only, disqualifying despite strong raw scores. |
| Jina-embeddings-v4 | Rejected — licensing ambiguity (initially mislabeled, actually derived under the Qwen Research License via its base model) plus lower retrieval score than Qwen3-Embedding. |
| EmbeddingGemma-300M | Held as a lighter fallback, not primary — multilingual and CPU/edge-optimized, but its 2,048-token context is short for note-body chunks and its retrieval scores trail BGE-M3/Qwen3-Embedding. Worth reconsidering only if disk/RAM ever becomes a real constraint (unlikely at AI_BRAIN's scale). |
| **BGE-M3 (primary recommendation)** | MIT license, 100+ languages, 8192-token context, native `sentence-transformers` support, most battle-tested "production standard" as of 2026 with a mature reranker pairing (`bge-reranker-v2-m3`). |
| **Qwen3-Embedding-0.6B (documented fallback)** | Modestly higher English MTEB score, much longer context window (32K vs 8K tokens) — a legitimate alternative if long-context whole-note embedding matters more than production track record; close enough in resource footprint and quality to BGE-M3 that this is a low-stakes choice. |

## 6. Comparison Against Evaluation Criteria

| Criterion | BGE-M3 | Qwen3-Embedding-0.6B | EmbeddingGemma-300M |
|---|---|---|---|
| Retrieval-task quality | Strong per-task (e.g. ArguAna 54.04); not top-of-leaderboard average but production-proven | English-v2 mean 70.70, Multi 64.33 — modestly ahead on raw score | Multi 61.15 / Eng 69.67 — behind both, but purpose-built for lighter deployment |
| Resource footprint | ~2.2GB, standard BERT-scale CPU inference, well-trodden | ~1.2–2.4GB fp16, ~0.56GB INT8-quantized; CPU-feasible with ONNX quantization | <200MB quantized; explicitly designed for CPU/edge, no GPU needed |
| Multilingual | 100+ languages | 100+ languages | 100+ languages |
| License | MIT | Apache 2.0 | Apache 2.0 (Gemma terms) |
| `sentence-transformers` compatibility | Native | Native | Native |
| Context length | 8,192 tokens | 32,000 tokens | 2,048 tokens |
| Qdrant hybrid fit | Native dense+sparse+multi-vector (redundant with, not harmful to, Qdrant-native fusion) | Dense-only, pairs with separate sparse model (miniCOIL) | Dense-only, pairs with separate sparse model |
| Maintenance/viability | Long-running BAAI project, actively cited as community standard in 2026 | Actively maintained, Qwen team, strong 2026 momentum | Actively maintained by Google DeepMind, purpose-marketed for on-device 2025–2026 |

## 7. AI_BRAIN Relevance

Both leading candidates (BGE-M3, Qwen3-Embedding-0.6B) run comfortably on CPU on a single Kali machine without a dedicated GPU, satisfying the local-first principle without hardware requirements the constitution wouldn't want assumed. The choice between them is explicitly framed as low-stakes given their close resource/quality footprint — the decision should be recorded as provisional-but-documented (this ADR) rather than treated as permanent, consistent with "measure before optimizing" and re-evaluated in 6–12 months against the then-current MTEB leaderboard via a new ADR, not a silent swap.

## 8. Security

Not a significant differentiator among the shortlisted candidates — all are permissively licensed (MIT/Apache 2.0), loaded via `sentence-transformers`'s standard model-loading path (no custom/untrusted code execution), and run entirely locally with no data leaving the machine, consistent with the local-first security posture.

## 9. Performance

CPU inference is feasible for both BGE-M3 and Qwen3-Embedding-0.6B at AI_BRAIN's scale; reranking cost is bounded regardless of corpus size since rerankers only score a top-K shortlist (~20–100 candidates) per query, not the full corpus. A small consumer GPU would speed up bulk reindexing but is not required.

## 10. Operational Concerns

- **miniCOIL is currently English-only** (its input encoder is `jina-embeddings-v2-small-en`; Qdrant's own materials list multilingual expansion as roadmap, not shipped) — a real gap against AI_BRAIN's explicit assumption that vault content isn't English-only. If the vault is English-majority with occasional foreign terms, miniCOIL is fine (the multilingual dense model carries cross-lingual semantic recall). If a meaningful fraction of notes are in another language, Qdrant's built-in BM25-based full-text/sparse indexing (language-agnostic, no neural model) is the more honest fallback for the sparse leg, at the cost of slightly lower semantic-aware sparse ranking.
- **Swappability requires deliberate design**: collection aliasing (never a hardcoded collection name), retaining original chunk text for re-embedding, and recording the embedding model name/version/dimension as explicit config rather than hardcoded inline.
- The MTEB leaderboard's live table could not be fetched this research session (JS-rendered Hugging Face Space) — scores are model-card-sourced and should be spot-checked live before this ADR is finalized, since rankings shift monthly.

## 11. Recommendation

**Primary**: `BAAI/bge-m3` (dense) + `BAAI/bge-reranker-v2-m3` (reranker) + Qdrant `miniCOIL` via `fastembed` (sparse).

**Documented fallback/alternative**: `Qwen/Qwen3-Embedding-0.6B` + `Qwen/Qwen3-Reranker-0.6B` — pick this instead if long-context whole-note embedding (32K vs. 8K tokens) matters more than BGE-M3's longer production track record; treat as a genuinely close, low-stakes alternative rather than a clearly inferior option.

**Sparse-leg fallback**: if measurement of actual vault language composition shows meaningful non-English content, fall back from miniCOIL to Qdrant's built-in BM25-based sparse indexing for the sparse leg.

No per-content-type embedding strategy (separate models for titles/tags vs. bodies) is recommended — this pattern is justified mainly at commercial search volume, not AI_BRAIN's personal-vault scale, and the constitution's "do not optimize prematurely" rule applies directly.

## 12. References

- [Qwen3-Embedding-8B model card](https://huggingface.co/Qwen/Qwen3-Embedding-8B) · [Qwen3-Embedding-0.6B model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) · [Qwen3-Embedding GitHub](https://github.com/QwenLM/Qwen3-Embedding) · [Technical report](https://arxiv.org/pdf/2506.05176) · [Qwen3-Reranker-0.6B model card](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)
- [EmbeddingGemma official model card](https://ai.google.dev/gemma/docs/embeddinggemma/model_card) · [HF page](https://huggingface.co/google/embeddinggemma-300m) · [Google Developers Blog launch post](https://developers.googleblog.com/en/introducing-embeddinggemma/)
- [BAAI/bge-m3 model card](https://huggingface.co/BAAI/bge-m3) · [BAAI/bge-reranker-v2-m3 model card](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- [NVIDIA NV-Embed-v2 model card (license reference)](https://huggingface.co/nvidia/NV-Embed-v2) · [Jina Embeddings v4 model card](https://huggingface.co/jinaai/jina-embeddings-v4)
- [Qdrant — miniCOIL: on the Road to Usable Sparse Neural Retrieval](https://qdrant.tech/articles/minicoil/) · [Qdrant — Working with miniCOIL (docs)](https://qdrant.tech/documentation/fastembed/fastembed-minicoil/) · [Qdrant — Working with SPLADE (docs)](https://qdrant.tech/documentation/fastembed/fastembed-splade/) · [Qdrant/minicoil-v1 model card](https://huggingface.co/Qdrant/minicoil-v1)
- [Qdrant — Migrate to a New Embedding Model (official docs)](https://qdrant.tech/documentation/tutorials-operations/embedding-model-migration/) · [Qdrant — Hybrid Queries (docs)](https://qdrant.tech/documentation/search/hybrid-queries/)
- [sentence-transformers — Pretrained Cross-Encoder Models](https://sbert.net/docs/cross_encoder/pretrained_models.html)
- [MTEB leaderboard (Hugging Face Space)](https://huggingface.co/spaces/mteb/leaderboard) — live table not fetchable this session, re-check before finalizing
- [premai.io — Best Embedding Models for RAG 2026 (secondary aggregator)](https://www.premai.io/blog/best-embedding-models-for-rag-2026-ranked-by-mteb-score-cost-and-self-hosting/)

## 13. Open Questions

- Should the live MTEB leaderboard be re-checked directly (not via secondary aggregation) immediately before this ADR is finalized, given the JS-rendering fetch limitation encountered this session?
- Should AI_BRAIN measure actual vault language composition early in Phase 1 to settle the miniCOIL-vs-BM25 sparse-leg question before committing, or default to miniCOIL and revisit if needed?
- Is BGE-M3's shorter 8K context sufficient for AI_BRAIN's chunking strategy (per ADR-0003's `chonkie` chunking, chunks are expected to be well under whole-note length), or does Qwen3-Embedding-0.6B's 32K context matter enough to flip the primary recommendation?

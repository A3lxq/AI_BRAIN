# ADR-0008: Embeddings, Sparse-Vector, and Reranker Model Choice for AI_BRAIN

- **ID:** ADR-0008
- **Title:** Embeddings, Sparse-Vector, and Reranker Model Choice for AI_BRAIN
- **Status:** Accepted
- **Date proposed:** 2026-08-24
- **Date accepted:** 2026-08-24
- **Depends on:** ADR-0003 (RAG orchestration, committed to `sentence-transformers` and Qdrant hybrid dense+sparse fusion)

## Context

AI_BRAIN needs a concrete dense embedding model, sparse-vector generation method, and reranker model — locally runnable on a single Kali machine, multilingual-capable, and compatible with `sentence-transformers` (already chosen, ADR-0003). Full findings: [`docs/research/2026-08-24_embeddings_model_choice.md`](../research/2026-08-24_embeddings_model_choice.md).

Key findings: the current open-weight leader on raw MTEB score is the Qwen3-Embedding family, but BGE-M3 remains the de facto production-standard default due to its long track record, MIT license, and mature reranker pairing. Qdrant's own current guidance recommends **miniCOIL** over SPLADE++ for the sparse leg of hybrid fusion — but miniCOIL is **currently English-only**, a real gap against AI_BRAIN's assumption that vault content isn't English-only. Qdrant's vector storage is hard-tied to the chosen model's dimension with no in-place migration, making collection aliasing a required design element regardless of which model is chosen.

## Decision

**Accepted:**
1. **Dense embeddings**: `BAAI/bge-m3` (MIT license, 100+ languages, 8192-token context).
2. **Reranker**: `BAAI/bge-reranker-v2-m3` (paired with BGE-M3, multilingual).
3. **Sparse vectors**: Qdrant's `miniCOIL` (`Qdrant/minicoil-v1`, via `fastembed`), with a documented fallback to Qdrant's built-in BM25-based sparse indexing if measured vault language composition shows meaningful non-English content.
4. **Documented alternative**: `Qwen/Qwen3-Embedding-0.6B` + `Qwen/Qwen3-Reranker-0.6B` — an explicitly close, low-stakes alternative to switch to if long-context whole-note embedding (32K vs. 8K tokens) proves more valuable in practice than BGE-M3's production track record.
5. Qdrant collections will be addressed via an **alias**, never a hardcoded name, and original chunk text will always be retained independent of stored vectors, to keep future model swaps a config change rather than a data-migration crisis.

The maintainer reviewed the research and comparison and accepted this ADR as proposed on 2026-08-24. This decision is explicitly **provisional-but-documented** — to be re-evaluated in 6–12 months against the then-current MTEB leaderboard via a new ADR, not a silent swap, per Constitution Article 14 ("No Silent Architecture Changes").

## Alternatives Considered

| Option | Verdict |
|---|---|
| Qwen3-Embedding-8B | Rejected as primary — oversized for AI_BRAIN's corpus scale; 4096-dim would inflate Qdrant storage ~4x for benchmark-scale quality gains unlikely to manifest at AI_BRAIN's actual (low tens of thousands of chunks) scale. |
| NV-Embed-v2 | Rejected — CC-BY-NC-4.0 license (non-commercial only), disqualifying despite strong scores. |
| Jina-embeddings-v4 | Rejected — licensing ambiguity/restriction risk. |
| EmbeddingGemma-300M | Held as a lighter fallback, not primary — shorter 2,048-token context and lower retrieval scores than BGE-M3/Qwen3-Embedding; reconsider only if disk/RAM ever becomes a real constraint. |
| SPLADE++ for sparse vectors | Rejected as primary — Qdrant's own current guidance recommends miniCOIL over SPLADE++ for new projects; SPLADE++ is also English-only, so it doesn't solve the multilingual gap either. |
| Per-content-type embedding models (titles/tags vs. bodies) | Rejected — this pattern is justified mainly at commercial search volume; unjustified complexity at AI_BRAIN's personal-vault scale per "do not optimize prematurely." |

## Rationale

1. **BGE-M3 is the most battle-tested production choice** among viable candidates, with a mature reranker pairing and permissive MIT license, while running comfortably on CPU at AI_BRAIN's scale without requiring a GPU.
2. **Qwen3-Embedding-0.6B is retained as a documented, legitimate alternative rather than dismissed**, since the research found the two candidates close enough in resource footprint and quality that the choice is genuinely low-stakes — this is recorded explicitly so a future switch doesn't require re-litigating the whole decision.
3. **miniCOIL's English-only limitation is treated as an operational risk to monitor, not ignored** — a documented fallback (Qdrant's BM25-based sparse indexing) exists precisely because AI_BRAIN's constitution requires evidence-based decisions, and the actual language composition of a given user's vault isn't known until Phase 1 measurement.
4. **Swappability is designed in from the start** (collection aliasing, retained original text, explicit config for model name/version/dimension) because Qdrant's own migration documentation confirms dimension changes are not in-place — this is a real architectural cost avoided cheaply now rather than expensively later.
5. **Treating this as provisional-but-documented directly follows the constitution's "measure before optimizing" and "no silent architecture changes" articles** — this field moves fast enough (evidenced by the churn from BGE/E5/GTE-era models to the current Qwen3-Embedding/EmbeddingGemma generation) that permanence would be an overclaim.

## Consequences

- The Qdrant collection schema (vector size = BGE-M3's dimension, sparse vector configuration for miniCOIL) can now be finalized as part of ADR-0006's deployment setup.
- Collection access must go through an alias from day one — this is a required Phase 1 implementation detail, not optional.
- Original chunk text must be retained independent of Qdrant-stored vectors (e.g., in AI_BRAIN's SQLite metadata store per ADR-0004), enabling re-embedding without needing to re-derive text from the vault on every model swap.
- A Phase 1 measurement step should assess actual vault language composition to confirm or revisit the miniCOIL choice for the sparse leg.
- The live MTEB leaderboard should be spot-checked directly (not via secondary aggregation) before this ADR is finalized, since this research session could not fetch its JS-rendered table.
- This decision must be revisited via a new ADR within 6–12 months or upon a clear, measured case that a newer model materially outperforms the current choice — not changed silently.

## References

See [`docs/research/2026-08-24_embeddings_model_choice.md`](../research/2026-08-24_embeddings_model_choice.md) §12 for the full primary-source citation list.

## Open Questions

Resolved: the maintainer accepted this ADR as proposed on 2026-08-24, with no modifications requested.

Remaining open items, carried forward as implementation-time checks:
- Spot-check the live MTEB leaderboard directly before Phase 1 model download, since this research session could not fetch its JS-rendered table.
- Should Phase 1 include an explicit vault-language-composition measurement step before finalizing the sparse-vector model choice (miniCOIL vs. BM25 fallback)?

# Session 008 — Phase 0 Parallel Research Batch (ADR-0006 through ADR-0009)

- **Date:** 2026-08-24
- **Phase:** 0 — Architecture & Research
- **Depends on:** Sessions 003–007 (ADR-0001 through ADR-0005)

## Objective

Given a stated time constraint, research the four remaining Phase 0 topics (Qdrant deployment specifics, MCP tool contract design, embeddings model choice, filesystem event architecture) in parallel rather than sequentially, then bring all findings and draft ADRs back for maintainer review as a batch.

## Completed Work

- Launched four parallel research agents, one per topic, each using current 2026 primary sources.
- Wrote four research documents following the Documentation Standards structure:
  - `docs/research/2026-08-24_qdrant_deployment.md`
  - `docs/research/2026-08-24_mcp_tool_contract.md`
  - `docs/research/2026-08-24_embeddings_model_choice.md`
  - `docs/research/2026-08-24_filesystem_event_architecture.md`
- Drafted four ADRs, initially status Proposed:
  - `docs/adr/0006-qdrant-deployment-mode.md`
  - `docs/adr/0007-mcp-tool-contract.md`
  - `docs/adr/0008-embeddings-model-choice.md`
  - `docs/adr/0009-filesystem-event-architecture.md`
- Maintainer reviewed and **accepted all four ADRs as proposed, in one batch** — all now Accepted.

## Key Decisions

1. **Qdrant deployment (ADR-0006)**: Docker server, bound to `127.0.0.1` only — not the embedded "local mode" client, which is a from-scratch reimplementation capped near AI_BRAIN's own scale with a documented hybrid-fusion parity bug.
2. **MCP tool contract (ADR-0007)**: a full tool table covering every master-spec tool family; destructive operations (`note_delete`, `note_merge`) require Multi Round-Trip Request confirmation; genuinely irreversible Git operations are excluded from the MCP surface entirely.
3. **Embeddings/sparse/reranker (ADR-0008)**: BGE-M3 (dense) + bge-reranker-v2-m3 (reranker) + Qdrant miniCOIL via `fastembed` (sparse), with Qwen3-Embedding-0.6B documented as a close fallback; decision is explicitly provisional-but-documented, to be re-evaluated in 6–12 months.
4. **Filesystem event architecture (ADR-0009)**: light, non-semantic debouncing plus idempotent Huey jobs as the real reliability mechanism, with a periodic reconciliation/full-scan job as backstop for events the pipeline can't guarantee to catch.

## Key Findings Worth Carrying Forward

- **MCP has no protocol-level defense against a client LLM conflating retrieved vault content with instructions.** Tool annotations are explicitly documented as informational only, not enforcement. This must be named as a residual risk in the security threat model, not treated as solved.
- **Qdrant's embedded "local mode" is not a viable production deployment** — Qdrant's own docs frame it as dev/test/CI-only, and this research found a concrete, documented parity bug in exactly the hybrid-fusion feature AI_BRAIN already committed to (ADR-0003).
- **Filesystem event precision is fundamentally limited by inotify's own guarantees** (non-atomic move-pairing, cross-boundary move degradation) — the architecture deliberately biases toward over-triggering (cheap, absorbed by idempotent jobs) over under-triggering (silently stale index), matching the Testing Strategy's explicit tolerance for duplicate events.
- **Embedding model choice in this space has a real shelf-life** — recorded as provisional-but-documented rather than permanent, consistent with "measure before optimizing" and "no silent architecture changes."

## Files Changed

- 4 new research docs, 4 new ADRs (listed above), all Proposed → Accepted
- `CURRENT_STATE.md`, `NEXT_SESSION.md`, `CHANGELOG.md`, `SESSION_LOG.md` (updated)
- `docs/sessions/2026-08-24_phase0-parallel-research-batch.md` (this file, new)

## Tests

None — research-only session per CLAUDE.md Phase discipline; no code was written.

## Unresolved Issues

All nine Phase 0 technology-selection ADRs are now accepted, but this is not the same as Phase 0 being complete. Per `docs/00_MASTER_PROJECT_SPECIFICATION.md` §18, Phase 0 exit criteria additionally require: a consolidated architecture document, a formal data model, a formal event model, an updated security threat model (incorporating the residual risks named above), testing strategy elaboration, and a Git strategy runbook (including the Qdrant snapshot-before-upgrade procedure). These are synthesis/formalization tasks that can largely draw on the now-accepted ADRs rather than requiring new research.

## Next Steps

See `NEXT_SESSION.md` for the itemized list of remaining Phase 0 exit-criteria deliverables and per-ADR implementation-time follow-ups to track into Phase 1.

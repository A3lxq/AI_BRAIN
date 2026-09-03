# ATHENA AI-BRAIN — Long-Term Viability Notes

- **Date:** 2026-08-24
- **Author:** Claude Code (ATHENA AI-BRAIN Phase 0)
- **Purpose:** A standing record of the long-term-viability reasoning behind ATHENA AI-BRAIN's Phase 0 technology choices — for the maintainer today, and for any future maintainer (including a future version of the current maintainer) inheriting this project years from now.
- **Status:** Living document — revisit whenever a monitored risk below changes status, or when re-evaluating any ADR per its own "provisional" markers.

## Why this document exists

Nine Phase 0 technology-selection ADRs (0001–0009) are now accepted. Each ADR justifies its own decision in isolation. This document instead asks the cross-cutting question: **taken together, will this stack still be a good foundation in five or ten years, for this user and for anyone else who picks the project up?** Per CLAUDE.md rule 17, this reasoning must not live only in chat history — it belongs in the repository.

## The structural choice that matters most: nothing here locks the project in

The single biggest longevity decision wasn't any one library — it was the repeated pattern, across ADR-0003 (RAG orchestration), ADR-0004 (SQLite access layer), and ADR-0005 (Git automation), of **refusing framework/library dependencies that would own ATHENA AI-BRAIN's architecture**, in favor of thin wrappers over durable standards (SQL, git, the Qdrant wire protocol) and independently-testable internal modules. LangChain and LlamaIndex were rejected specifically because they want to own orchestration, not just provide a utility. GitPython was rejected in part because its own maintainer states its design is broken beyond repair. What survives that filter is either a language-level standard (SQLite, git) or a first-party client for one well-defined external service (`qdrant-client`).

The practical consequence: if any single piece of this stack degrades or is abandoned a decade from now, the fix is to swap that piece — not to rewrite the system around a dead framework's assumptions. That replaceability is the actual insurance policy here, more than any individual library's current popularity or momentum.

## Durable, low-risk components

- **Python, SQLite, and git** — three of the most institutionally stable pieces of software in existence. SQLite's on-disk file format is a de facto permanent standard; git is not going anywhere; CPython has multi-body formal governance (an annually-elected Steering Council, and a newly-approved Packaging Council as of April 2026) that is, if anything, more robust now than in past years. See ADR-0001.
- **The hand-rolled Git automation module** (ADR-0005) — by not depending on GitPython, ATHENA AI-BRAIN is immune to that specific project's decline. The cost is more code to write and maintain directly; the payoff is that this code cannot rot because someone else's dependency rotted.
- **The hand-rolled RAG and SQLite-access layers** (ADR-0003, ADR-0004) — same trade-off. More of ATHENA AI-BRAIN's logic is code the project owns outright, which is more maintenance surface today but carries zero dependency-abandonment risk later.
- **Qdrant** (ADR-0006) — actively developed with frequent releases and real snapshot/upgrade tooling. Its main long-term liability is a process one (no-skip-minor-version upgrades), not a viability one — manageable with a documented runbook, not a structural risk.

## Monitored risks — named, not ignored

- **Astral's tooling (`uv`, `ruff`, `ty`) is now owned by OpenAI** (ADR-0001), following its March 2026 acquisition. Stated commitment to keep the tools open source; `uv` remains pip-compatible, giving a fallback path if that commitment changes. Worth a periodic check-in, not a cause for present concern.
- **MCP itself is young and still moving fast.** The spec changed substantially during this very research effort (a shift to a stateless protocol, Multi Round-Trip Requests replacing server-initiated push, "tasks" moving out of core into an optional extension — see ADR-0007). ATHENA AI-BRAIN's MCP tool wrappers are deliberately thin specifically because the protocol is still settling: if MCP's shape changes again, only the thin adapter layer needs to change, not the indexing/retrieval logic underneath it.
- **Embedding models have a real shelf-life** — realistically 12–18 months before a meaningfully better open-weight model displaces the current choice. ADR-0008 treats this as provisional-but-documented by design, not an oversight. The mitigation is architectural, not aspirational: Qdrant collection aliasing plus retained original chunk text means a future model swap is a backfill job, not a system rebuild.
- **Huey's async bridge and the hand-rolled SQLite repository layer are the least battle-tested pieces in the stack.** Both are explicitly flagged in their ADRs (0002, 0004) for early Phase 1 validation against a real workload, with a documented fallback (a hand-rolled asyncio+SQLite queue; Peewee) if either proves awkward in practice. This is the honest "not yet proven in production" risk in the design.
- **miniCOIL (the sparse-vector model, ADR-0008) is currently English-only.** A vault that turns out to be meaningfully multilingual needs the documented BM25 fallback for the sparse leg of hybrid retrieval — flagged for measurement early in Phase 1, not assumed away.

## Why this design serves future users, not just the current one

Several choices were made with a future, possibly different user explicitly in mind, even though there is only one user today:

- **No cloud dependency anywhere in the core path** — Qdrant runs locally (ADR-0006), SQLite is local, and the default embedding model runs locally (ADR-0008). A future user inheriting this project does not inherit anyone's API bills or cloud account.
- **A multi-provider LLM abstraction with no provider-specific code in core logic** (ADR-0003) — a future user is not locked into whichever LLM vendor happens to be dominant when they pick this up.
- **Permissive licensing throughout** — every chosen library (Python's stdlib, SQLite, `sentence-transformers`, BGE-M3/MIT, Qwen3-Embedding/Apache 2.0, Qdrant/Apache 2.0, Huey, `aiosqlite`) is MIT- or Apache-2.0-licensed. Nothing in the accepted stack creates a redistribution problem.
- **Every decision has a written ADR recording rejected alternatives and rationale**, not just the final choice. That is what actually makes a project inheritable by a future maintainer — understanding *why* something was chosen, not only what was chosen, is what lets someone else safely change it later instead of being afraid to touch it.

## Bottom line

The individual pieces of this stack are, deliberately, mostly ordinary and boring: Python, SQLite, git — software with decades of institutional stability behind it — plus a small number of actively-maintained but comparatively young pieces (Qdrant, MCP, Huey) chosen because they are the best current fit for ATHENA AI-BRAIN's specific requirements. The architecture is built so that the young, less-proven pieces are the ones that can be swapped without disturbing the old, proven ones. That separation — not any single tool's current popularity — is what should keep this project viable well beyond the timeframe any one library's hype cycle would suggest.

## Cross-references

- ADR-0001 — Runtime Language/Stack Selection (`docs/adr/0001-runtime-language-selection.md`)
- ADR-0002 — Job/Queue Architecture (`docs/adr/0002-job-queue-architecture.md`)
- ADR-0003 — RAG Orchestration Approach (`docs/adr/0003-rag-orchestration-approach.md`)
- ADR-0004 — SQLite Access Layer (`docs/adr/0004-sqlite-access-layer.md`)
- ADR-0005 — Git Automation Library (`docs/adr/0005-git-automation-library.md`)
- ADR-0006 — Qdrant Deployment Mode (`docs/adr/0006-qdrant-deployment-mode.md`)
- ADR-0007 — MCP Tool Contract (`docs/adr/0007-mcp-tool-contract.md`)
- ADR-0008 — Embeddings, Sparse-Vector, and Reranker Model Choice (`docs/adr/0008-embeddings-model-choice.md`)
- ADR-0009 — Filesystem Event Architecture (`docs/adr/0009-filesystem-event-architecture.md`)

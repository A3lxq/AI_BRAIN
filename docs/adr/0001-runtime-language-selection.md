# ADR-0001: Runtime Language/Stack Selection for ATHENA AI-BRAIN

- **ID:** ADR-0001
- **Title:** Runtime Language/Stack Selection for ATHENA AI-BRAIN
- **Status:** Accepted
- **Date proposed:** 2026-08-22
- **Date accepted:** 2026-08-22

## Context

`NEXT_SESSION.md` scoped the first Phase 0 research task as evaluating the programming language/runtime for ATHENA AI-BRAIN, requiring comparison of at least Python, TypeScript/Node.js, Go, and Rust rather than assuming Python by default. Per `docs/RESEARCH_PROTOCOL.md`, each candidate was researched against 15 criteria (ecosystem, MCP support, AI/RAG ecosystem, filesystem/event tooling, SQLite support, vector database clients, async/concurrency model, type safety, performance, security, deployment, Linux/Kali compatibility, maintainability, developer productivity, long-term viability) using current primary documentation as of 2026-08-22.

Full findings are recorded in:
- [`docs/research/2026-08-22_runtime_python.md`](../research/2026-08-22_runtime_python.md)
- [`docs/research/2026-08-22_runtime_typescript_nodejs.md`](../research/2026-08-22_runtime_typescript_nodejs.md)
- [`docs/research/2026-08-22_runtime_go.md`](../research/2026-08-22_runtime_go.md)
- [`docs/research/2026-08-22_runtime_rust.md`](../research/2026-08-22_runtime_rust.md)
- [`docs/research/2026-08-22_runtime_comparison.md`](../research/2026-08-22_runtime_comparison.md) (synthesis + weighted discussion)

Key finding that changed the shape of the decision: **all four candidates now have an official, first-party MCP SDK** maintained under (or in collaboration with) the `modelcontextprotocol` GitHub org. This was expected going in to be a significant differentiator (particularly favoring TypeScript, the language MCP originally shipped in) and turned out to be a wash — removing what might have been a deciding factor and shifting the decision weight onto AI/RAG ecosystem depth, security posture, and fit for a solo/small-team, iteration-heavy engineering context.

## Decision

**Accepted:** Python is the primary implementation language/runtime for ATHENA AI-BRAIN.

The maintainer reviewed the research and comparison documents and accepted this ADR as proposed on 2026-08-22. Phase 1 (Foundation) implementation may now proceed on this basis, subject to the open sub-decisions listed under Consequences, each of which requires its own design doc before implementation per Constitution Article 2.

## Alternatives Considered

| Option | Verdict |
|---|---|
| TypeScript/Node.js | Strong: MCP's reference-implementation language, good AI/RAG tooling (Vercel AI SDK, LangChain.js/LlamaIndex.TS), official Qdrant client. Rejected as primary due to thinner reranking ecosystem and a real, escalating npm supply-chain-attack risk requiring ongoing operational discipline. |
| Go | Strong concurrency-model fit, official MCP (Google co-maintained) and Qdrant clients, excellent security defaults. Rejected as primary due to the thinnest AI/RAG orchestration ecosystem of the four candidates (no LangChain/LlamaIndex equivalent, no pure-Go local embeddings) and a forced SQLite-driver trade-off between performance and static-binary deployment simplicity. |
| Rust | Strongest security/correctness guarantees (memory safety, structurally safe subprocess/Git APIs), first-class official MCP and Qdrant clients. Rejected as primary due to the steepest learning curve and most integration glue-work in the AI/RAG layer of any candidate — a real velocity tax for this project's solo/small-team, iteration-heavy context, confirmed by Rust's own official 2025 community survey citing compile time and learning curve as the top self-reported pain points. |

## Rationale

1. **MCP support is now equivalent across all candidates** — an official SDK exists for each, removing this as a differentiator.
2. **AI/RAG ecosystem depth is the criterion that matters most for this project's actual differentiated work.** ATHENA AI-BRAIN's core engineering effort is iterative RAG-pipeline tuning (chunking strategy, embedding model choice, hybrid retrieval fusion, reranking), not primarily systems-level infrastructure work. Python has the deepest, most purpose-built ecosystem against every specifically named requirement in the master specification: `sentence-transformers` (embeddings + reranking), `chonkie` (semantic/structure-aware chunking), `qdrant-client` (official, sync+async), `litellm` (multi-provider LLM abstraction), `watchdog` (filesystem events), `python-frontmatter` (YAML frontmatter).
3. **Python's known weaknesses land outside ATHENA AI-BRAIN's actual workload shape.** The GIL's partial resolution (PEP 779, officially supported free-threading as of 3.14, ecosystem still catching up) and raw CPU-bound slowness matter far less for a system whose workload is dominated by I/O (filesystem watching, LLM API calls, vector DB queries, MCP transport) plus ML inference already delegated to native PyTorch/ONNX backends. This is a direct application of the constitution's "measure before optimizing" principle — the theoretical performance gap doesn't apply where the work actually happens.
4. **Python's security gaps are well-documented, mitigable through discipline, and process-covered by this project's own constitution.** Unsafe subprocess patterns, YAML/pickle deserialization traps, and the lack of in-language sandboxing are known, named risks with known, named mitigations (list-form `subprocess.run`, `yaml.safe_load`, OS-level isolation if untrusted-code execution is ever needed) — this project's mandatory threat-modeling step (Constitution Article 9, `docs/SECURITY_MODEL.md`) is designed to catch exactly this class of risk regardless of language.
5. **Fit for a solo/small-team, teaching-oriented context.** CLAUDE.md establishes this project as a learning system built by one person with Claude Code. Rust's and Go's steeper AI/RAG integration costs (hand-rolled orchestration, thinner libraries, in Rust's case a real learning-curve tax) would slow the project's actual bottleneck — RAG quality iteration — more than Python's weaknesses would.

## Consequences

- Phase 1 (Foundation) will proceed with Python tooling: `uv` for environment/dependency management, `ruff` for lint/format, `mypy`/`pyright` for type checking (exact CI strictness policy to be decided in a Phase 1 design doc).
- The MCP server will be built on `modelcontextprotocol/python-sdk`.
- The vector store client will be `qdrant-client` (official, sync+async).
- Several sub-decisions remain explicitly **open** and are NOT resolved by this ADR — each requires its own design doc before implementation, per Constitution Article 2:
  - Job/queue architecture (Celery vs. Dramatiq vs. Taskiq vs. RQ vs. hand-rolled)
  - RAG orchestration: adopt LangChain/LlamaIndex vs. hand-roll on `qdrant-client` + `sentence-transformers` + `chonkie`
  - SQLite access layer: raw `sqlite3`/`aiosqlite` vs. SQLAlchemy 2.x async ORM
  - Git automation: raw `subprocess` with strict argument-list validation vs. `GitPython`
  - Reranking approach: hosted API vs. local cross-encoder
- **Monitored risk, not a blocker:** OpenAI's announced acquisition of Astral (2026-03-19) concentrates ownership of core Python tooling (`uv`, `ruff`, `ty`) in a single AI company. Mitigated by `uv` remaining pip-compatible (fallback path exists) and Astral's stated open-source commitment. This should be revisited if the tooling's licensing or maintenance posture changes materially.
- If a future performance bottleneck is identified and measured (not assumed) in a specific hot path, a targeted Go or Rust component may be considered for that path alone — this is explicitly not pre-decided now, per "measure before optimizing," and would require its own ADR if proposed.

## References

See [`docs/research/2026-08-22_runtime_comparison.md`](../research/2026-08-22_runtime_comparison.md) §6 for the full primary-source citation list across all four candidate research documents.

## Open Questions

Resolved: the maintainer accepted this ADR as proposed on 2026-08-22, with no modifications requested.

Remaining open items are tracked as sub-decisions under Consequences above, not as open questions on the language choice itself.

# ADR-0007: MCP Tool Contract for ATHENA AI-BRAIN

- **ID:** ADR-0007
- **Title:** MCP Tool Contract for ATHENA AI-BRAIN
- **Status:** Accepted
- **Date proposed:** 2026-08-24
- **Date accepted:** 2026-08-24
- **Depends on:** ADR-0002 (Huey job queue), ADR-0003 (RAG orchestration), ADR-0005 (Git automation)

## Context

ATHENA AI-BRAIN's master specification requires one unified MCP server, with internal business logic decoupled from MCP transport and independently testable, exposing tool families: search, read, create, update, move, rename, delete, related, duplicate detection, merge, research, summarize, link, reindex, status, history, provenance, Git operations, diagnostics — with "the final tool contract determined during architecture design." Full findings: [`docs/research/2026-08-24_mcp_tool_contract.md`](../research/2026-08-24_mcp_tool_contract.md).

Key findings shaping this ADR:
- The current MCP spec (2026-07-28) is stateless; server-initiated push requests were replaced by Multi Round-Trip Requests (MRTR); the "tasks" concept for long-running operations is an official but optional extension not yet implemented by the Python SDK (v2.0.0).
- **Tool annotations (`readOnlyHint`, `destructiveHint`, etc.) are explicitly documented by the MCP project as informational only, not enforcement** — "a server can claim `readOnlyHint: true` and delete your files anyway."
- **The spec provides no protocol-level mechanism to prevent a client LLM from conflating retrieved vault-note content with instructions.** Its own Security Best Practices document has no section on this threat.
- Prior art (the official filesystem reference server; the mature community `obsidian-mcp-server`) directly informed the design: move+rename collapse into one tool, dry-run previews are cheap and always available, and destructive operations require explicit confirmation.

## Decision

**Accepted:** Adopt the following MCP tool contract for ATHENA AI-BRAIN's unified server:

| Tool / primitive | Family | Classification | Execution | Permission / confirmation notes |
|---|---|---|---|---|
| `vault://{path}` (Resource) | read | read-only | n/a (resources/read) | Application-driven primary note-content access; supports `subscriptions/listen` |
| `note_read` | read | read-only | sync | Model-driven fallback when the host doesn't expose resource browsing |
| `vault_search` | search | read-only | sync | Combines FTS5 + Qdrant vector search (ADR-0003) |
| `note_related` | related | read-only | sync | Graph-link + semantic neighbors |
| `note_duplicates` | duplicate detection | read-only | sync | Candidates similar to one given note |
| `duplicates_scan` | duplicate detection | read-only | **task-backed** | Vault-wide sweep, dispatched to Huey |
| `note_summarize` | summarize | read-only | sync | `openWorldHint=true` — calls external LLM provider (ADR-0003 adapter) |
| `note_history` | history | read-only | sync | Git log/show only, via ADR-0005's subprocess wrapper |
| `note_provenance` | provenance | read-only | sync | Origin metadata per CLAUDE.md rule 24 |
| `vault_status` | status | read-only | sync | Index freshness, queue depth, last sync timestamp |
| `system_diagnostics` | diagnostics | read-only | sync | DB/Qdrant/Huey-worker/embedding-model health checks |
| `git_status`, `git_log` | Git ops | read-only | sync | No mutation possible |
| `job_status` / `job_cancel` | (interim tasks shim) | read-only / mutating | sync, polls Huey | Mirrors official tasks-extension vocabulary for future migration |
| `note_create` | create | mutating | sync | Fails if path exists, does not overwrite |
| `note_update` | update | mutating | sync | Patch mode (non-destructive) or full-overwrite (destructive); supports `dry_run` |
| `note_link` | link | mutating | sync | Thin wrapper over `note_update`'s patch path |
| `note_move` | move + rename (collapsed) | mutating | sync | Requires elicitation confirm if target path exists |
| `note_delete` | delete | mutating, **destructive** | sync | **MUST** use MRTR elicitation requiring exact path echo before executing |
| `note_merge` | duplicate detection → merge | mutating, **destructive** | sync | Only reachable after `note_duplicates`/`duplicates_scan`; `dry_run` supported; requires elicitation confirm |
| `research_start` | research | mutating (draft only) | **task-backed** | Dispatched to Huey; returns job handle |
| `research_commit` | research → create | mutating | sync | Explicit separate step to write a research draft into the vault; defaults `dry_run=true` |
| `reindex_start` | reindex | mutating (index only) | **task-backed** | Dispatched to Huey |
| `git_commit` | Git ops | mutating, non-destructive | sync | Auto-invoked after other mutations for provenance trail; also exposed standalone |
| *(excluded)* `git reset --hard`, force-push, branch delete | Git ops | destructive | **not exposed via MCP** | Human-operated CLI only, per CLAUDE.md rules 22–23 |

Every tool is designed as a thin wrapper over an independently-testable internal business-logic function.

The maintainer reviewed the research and comparison and accepted this ADR as proposed on 2026-08-24.

## Alternatives Considered

| Option | Verdict |
|---|---|
| Relying on the official tasks extension now | Rejected for now — the Python SDK v2.0.0 does not yet implement it; an interim `job_status`/`job_cancel` shim mirroring the same state vocabulary is adopted instead, to be migrated once SDK support lands. |
| Separate `note_rename` tool distinct from `note_move` | Rejected — the official filesystem reference server precedent (`move_file` handles both) shows no semantic gain from splitting, and it only adds a decision the model must make for no benefit. |
| Relying on tool annotations (`destructiveHint`) as the enforcement mechanism for dangerous operations | Rejected — the MCP project's own documentation states annotations are informational only; ATHENA AI-BRAIN's actual enforcement is server-side validation and MRTR confirmation, independent of what any client chooses to do with annotation hints. |
| Exposing destructive Git operations (force-push, hard reset, branch deletion) via MCP behind a confirmation gate | Rejected — CLAUDE.md rules 22–23 require these to never be triggered automatically; keeping them off the MCP surface entirely is a stronger guarantee than any confirmation gate, which still depends on the calling model correctly relaying a request to a human. |
| Treating retrieved-content/instruction conflation as solved by MCP's `audience`/`priority` content annotations | Rejected — these are display/inclusion hints for the client application, not a trust or instruction boundary; verified there is no MCP mechanism equivalent to a system/data separation at the content-block level. |

## Rationale

1. **Decoupling is enforced by construction**: every tool is a thin wrapper, directly satisfying the master specification's explicit requirement that internal capabilities be callable/testable without MCP.
2. **Destructive-operation safety is layered and does not depend on protocol trust**: MRTR confirmation for `note_delete`/`note_merge`, combined with excluding genuinely irreversible Git operations from the MCP surface entirely, follows both the direct prior-art precedent (`obsidian-mcp-server`'s confirmed-delete pattern) and CLAUDE.md's non-negotiable rules.
3. **The retrieved-content/instruction conflation risk is named explicitly rather than assumed solved.** Since no protocol-level mechanism exists, ATHENA AI-BRAIN's mitigation is deliberately defense-in-depth at the application layer (server-side validation independent of model intent, confirmation gates, structured content envelopes, optional heuristic scanning, audit logging) — this must be reflected as a residual risk in the security threat model design step, not treated as closed by this ADR.
4. **The interim tasks-shim keeps ATHENA AI-BRAIN spec-aligned without blocking on upstream SDK work**, while being explicitly documented as temporary so it doesn't calcify into permanent architecture.
5. **Prior art directly validated two design choices** (move+rename collapse; cheap always-available dry-run) rather than these being invented from scratch, consistent with the constitution's "research before implementation" article.

## Consequences

- Internal business-logic functions must be designed and implemented before or alongside their MCP tool wrappers, never as an afterthought bolted onto a tool handler — this shapes Phase 1/2 module boundaries.
- The security threat model (a required Phase 0 exit-criterion deliverable) must explicitly address retrieved-content/instruction conflation as a named residual risk with documented, layered mitigations — not claim it as solved.
- The interim `job_status`/`job_cancel` tool pair must be revisited once the Python MCP SDK implements the official `io.modelcontextprotocol/tasks` extension.
- `note_summarize`'s external-LLM-provider call (`openWorldHint=true`) may warrant an explicit user opt-in/configuration gate — flagged as an open question, not decided here.
- Force-push, hard reset, branch deletion, and history rewriting remain permanently outside the MCP tool surface; any future request to expose them would require a new ADR explicitly overriding this one, not a quiet addition.

## References

See [`docs/research/2026-08-24_mcp_tool_contract.md`](../research/2026-08-24_mcp_tool_contract.md) §12 for the full primary-source citation list.

## Open Questions

Resolved: the maintainer accepted this ADR as proposed on 2026-08-24, with no modifications requested.

Remaining open items, carried forward as implementation-time/design-time decisions:
- Should `note_summarize` require explicit user opt-in/configuration before being exposed, given it's the one read tool that calls an external LLM provider?
- Should the optional heuristic injection-pattern scanning be built in Phase 1, or deferred to the security threat model design step?

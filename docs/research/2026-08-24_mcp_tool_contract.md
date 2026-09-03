# Research: MCP Tool Contract Design for ATHENA AI-BRAIN

- **Research date:** 2026-08-24
- **Researcher:** Claude Code (ATHENA AI-BRAIN Phase 0)
- **Status:** Candidate evaluation — feeds ADR-0007 (MCP tool contract)
- **Depends on:** ADR-0002 (Huey job queue), ADR-0003 (RAG orchestration), ADR-0005 (Git automation)

## 1. Executive Summary

The current MCP specification (2026-07-28) is a substantial revision from earlier versions: the protocol is now stateless, server-initiated push requests were replaced by Multi Round-Trip Requests (MRTR), and the "tasks" concept for long-running operations moved out of core into an official but optional extension (`io.modelcontextprotocol/tasks`) — which the official Python SDK (v2.0.0) does **not yet implement**, requiring an interim shim for ATHENA AI-BRAIN's Huey-backed jobs. The most consequential finding is that **the spec provides no protocol-level mechanism to prevent a client LLM from conflating retrieved vault-note content with instructions** — tool annotations (`readOnlyHint`, `destructiveHint`, etc.) are explicitly documented as informational only, not enforcement, and the spec's own Security Best Practices document has no section on this threat at all. This makes ATHENA AI-BRAIN's own server-side validation and confirmation gates the actual security backstop, not a protocol feature. Prior art (the official filesystem reference server and the mature community `obsidian-mcp-server`) directly informed the proposed tool contract: move+rename collapse into one tool, dry-run previews are cheap and always available, and destructive operations require explicit multi-round-trip confirmation — with genuinely irreversible Git operations excluded from the MCP surface entirely.

## 2. Problem Being Solved

ATHENA AI-BRAIN needs a concrete MCP tool/resource contract exposing its internal capabilities (search, read, create, update, move, delete, duplicate detection, merge, research, summarize, link, reindex, status, history, provenance, Git operations, diagnostics — per the master specification's tool-family list) as one unified MCP server, with internal business logic decoupled from the MCP transport and callable/testable independently, per the constitution's explicit requirement.

## 3. Technology Overview

MCP's current stable spec revision is 2026-07-28. It is now a stateless protocol — no `initialize`/session-handshake, no session ID; any cross-call state (e.g., a job handle) is an ordinary opaque value passed as a tool argument. The official Python SDK (`mcp` on PyPI) is on a v2.0.0 rewrite (`FastMCP`-style decorator/type-hint-driven server API), with the legacy v1 line maintained separately for anyone not yet migrating.

## 4. Architecture Fit

- **Tasks extension vs. Huey**: the official `io.modelcontextprotocol/tasks` extension (states: `working`, `input_required`, `completed`, `failed`, `cancelled`; client polls `tasks/get`, submits interim input via `tasks/update`, cancels via `tasks/cancel`) maps cleanly onto Huey job semantics (ADR-0002) conceptually, but the Python SDK v2.0.0 explicitly does not implement it yet (removed the old experimental Tasks API, deferred the new one to future SDK work). ATHENA AI-BRAIN needs an interim `job_status`/`job_cancel` tool pair mirroring the same state vocabulary, so migration to native SDK support is mechanical later.
- **Elicitation for destructive-op confirmation**: MRTR's `form` mode (structured, JSON-Schema-validated, non-sensitive data) is exactly right for ATHENA AI-BRAIN's delete/merge confirmations — the spec explicitly forbids form-mode elicitation for secrets (must use `url` mode instead), which doesn't apply here.
- **Resources vs. tools for note content**: individual notes fit MCP's Resources concept (`vault://{path}`, application-driven, supports `subscriptions/listen` for live updates) better than a pure tool-call model, but since not every MCP host surfaces resource browsing to the model, a thin `note_read` tool is retained alongside as a model-driven fallback.
- **Sync vs. Python's async/sync split**: the SDK supports both `async def` (awaited directly) and plain `def` (run on a worker thread, non-blocking) handlers — relevant since ATHENA AI-BRAIN's repository layer (ADR-0004) is async but some hand-rolled RAG primitives (ADR-0003) may be synchronous.

## 5. Alternatives Considered / Prior Art

- **Official filesystem reference server** (16 tools): move+rename are one tool (`move_file`), not two — adopted directly for ATHENA AI-BRAIN's `note_move`. `edit_file`'s `dryRun` parameter (returns a diff without applying) is a cheap, always-available preview pattern — adopted for `note_update`/`note_merge`. Notably, **no delete tool exists at all** in the reference server — informative for how conservatively official servers treat irreversible operations.
- **cyanheads/obsidian-mcp-server** (mature community server, closest real-world analog to ATHENA AI-BRAIN's vault surface): `obsidian_delete_note` requires mandatory MRTR-based user confirmation before deleting — direct precedent for ATHENA AI-BRAIN's delete/merge confirmation design. Permission boundaries are folder-scoped, server-side, and environment-variable-driven, independent of any client-declared trust — validates that server-side deterministic controls (not annotations) are the real enforcement layer. Optional/gated tools are hidden from `tools/list` entirely unless explicitly enabled, rather than exposed-but-permission-checked — worth considering for any future higher-risk tool family.

## 6. Comparison Against Evaluation Criteria

| Criterion | Finding |
|---|---|
| Spec compliance / current best practice | Design uses current annotations (`readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint`), MRTR for confirmation, and an interim tasks-shim pending SDK support — all grounded in the 2026-07-28 spec, not stale prior-version assumptions. |
| Security — read-only/mutating/destructive classification | Every proposed tool is explicitly classified (see table in ADR); destructive tools (`note_delete`, `note_merge`) require MRTR confirmation; genuinely irreversible Git operations (hard reset, force-push, branch deletion) are excluded from the MCP surface entirely per CLAUDE.md rules 22–23. |
| Security — retrieved-content vs. instructions | **No protocol-level solution exists.** This is confirmed by direct inspection of the spec's Security Best Practices document (covers OAuth-adjacent threats — confused deputy, token passthrough, SSRF — but has no section on prompt injection via tool/resource content) and the tool-annotations blog post, which explicitly states annotations "don't stop models from following malicious instructions embedded in untrusted content." ATHENA AI-BRAIN's mitigation is entirely application-level defense-in-depth (see §7). |
| Decoupling from MCP transport | Every tool is designed as a thin wrapper over an independently-testable internal business-logic function, satisfying the master specification's explicit requirement. |
| Completeness vs. master spec's tool-family list | All named families (search, read, create, update, move, rename, delete, related, duplicate detection, merge, research, summarize, link, reindex, status, history, provenance, Git operations, diagnostics) are covered, with explicit rationale for collapsing move+rename and treating duplicate-detection→merge as a review-then-act pair. |
| Usability for a client LLM | Tool names are family-prefixed and single-purpose (`note_move`, `note_delete`, `vault_search`) rather than one omnibus tool, following the reference server's naming convention. |

## 7. ATHENA AI-BRAIN Relevance

Since ATHENA AI-BRAIN's internal capabilities must be callable/testable without MCP (master spec requirement), every tool in the proposed contract is a thin wrapper — this is directly enforceable by construction, not just a design intention. The absence of protocol-level retrieved-content/instruction separation means ATHENA AI-BRAIN's security model (`docs/SECURITY_MODEL.md`) must treat this as a named, residual risk requiring layered mitigation:

1. **Server-side input validation independent of model intent** — the actual backstop; internal business logic validates paths/ownership/scope on every call regardless of why the model proposed it.
2. **No destructive tool executes on a single call** — delete/merge always require an MRTR confirmation round-trip with a narrow, explicit schema (e.g., echo the exact note filename).
3. **Structured content envelopes** separating note body from metadata, with server-authored (not note-authored) tool descriptions stating explicitly that body content is retrieved data, never a command — a structural signal, not an enforced guarantee.
4. **Optional lightweight heuristic scanning** of note content for obvious injection patterns, as a logged defense-in-depth signal only.
5. **Audit logging of tool invocations**, so a successful injection-driven call is at least detectable after the fact.

## 8. Security

This is the section carrying the most weight in this research. Tool annotations are explicitly documented by the MCP project itself as a "risk vocabulary," not an enforcement mechanism — "a server can claim `readOnlyHint: true` and delete your files anyway." ATHENA AI-BRAIN's own server never relies on annotations for actual safety; they exist for client-side display/warning purposes only. The genuinely irreversible Git operations (force-push, hard reset, branch deletion, history rewriting) are kept off the MCP surface entirely — not merely gated behind confirmation — directly satisfying CLAUDE.md's non-negotiable rules 22 and 23.

## 9. Performance

Not a differentiator — tool classification as sync vs. task-backed is driven by whether the underlying operation is already a Huey job (ADR-0002), not by raw latency tuning.

## 10. Operational Concerns

- The interim `job_status`/`job_cancel` tool pair is a deliberate, documented workaround for the Python SDK's current lack of official tasks-extension support — must be revisited and migrated once SDK support lands, not treated as permanent architecture.
- Research-generated content is written to the vault only via a separate, explicit `research_commit` step (defaulting to `dry_run=true`) rather than automatically by `research_start` — keeps generative-content writes auditable and opt-in, consistent with the master spec's distinction between source material and AI-generated synthesis.
- `git_commit` is both auto-invoked after other mutating tools (for the provenance trail required by CLAUDE.md rule 24) and exposed standalone.

## 11. Recommendation

Adopt the proposed tool contract (full table in ADR-0007), built on: thin per-tool wrappers over independently-testable internal functions; move+rename collapsed into `note_move`; duplicate-detection as a read-only scan feeding a separate, confirmed `note_merge`; individual notes exposed primarily as Resources with a `note_read` tool fallback; Huey-backed operations (`duplicates_scan`, `research_start`, `reindex_start`) exposed via the interim tasks-shim; MRTR-based confirmation required for `note_delete` and `note_merge`; and irreversible Git operations excluded from the MCP surface entirely.

## 12. References

- [MCP Specification changelog (2026-07-28)](https://modelcontextprotocol.io/specification/2026-07-28/changelog) · [Tools spec](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) · [Resources spec](https://modelcontextprotocol.io/specification/2026-07-28/server/resources) · [Server concepts overview](https://modelcontextprotocol.io/docs/learn/server-concepts)
- [Tasks extension overview](https://modelcontextprotocol.io/extensions/tasks/overview) · [ext-tasks repo (SEP-2663)](https://github.com/modelcontextprotocol/ext-tasks)
- [Elicitation spec](https://modelcontextprotocol.io/specification/draft/client/elicitation)
- [MCP Security Best Practices](https://modelcontextprotocol.io/docs/draft/tutorials/security/security_best_practices)
- ["Tool Annotations as Risk Vocabulary" (MCP blog)](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/) · [2026-07-28 release blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
- [Python SDK repo](https://github.com/modelcontextprotocol/python-sdk) · [Docs — Tools](https://py.sdk.modelcontextprotocol.io/servers/tools/) · [Docs — What's new in v2](https://py.sdk.modelcontextprotocol.io/whats-new/)
- [Official filesystem reference server](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem)
- [cyanheads/obsidian-mcp-server](https://github.com/cyanheads/obsidian-mcp-server)

## 13. Open Questions

- Should the interim `job_status`/`job_cancel` shim be built to mirror the official tasks-extension vocabulary exactly (as proposed), or should ATHENA AI-BRAIN wait for SDK support before exposing any task-backed tools via MCP?
- Should the optional heuristic injection-pattern scanning (mitigation #4 in §7) be built in Phase 1, or deferred until the security threat model design step formally addresses this residual risk?
- Should `note_summarize` (which calls an external LLM provider per ADR-0003's adapter) require explicit user opt-in/configuration before being exposed at all, given it's the one read-only-to-the-vault tool with `openWorldHint=true`?

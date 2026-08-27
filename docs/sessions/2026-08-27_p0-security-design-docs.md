# Session 011 — P0 Security Remediation Design Documents

- **Date:** 2026-08-27
- **Phase:** 0 — Architecture & Research (closing out the security remediation gate before Phase 1)
- **Depends on:** Session 009 (security threat model + red-team review), Session 010 (ADR-0010 accepted, Phase 0 readiness assessed)

## Objective

Per the user's explicit request to "work through the P0 checklist as design docs first," produce Constitution Article 2-compliant design documents (purpose, responsibilities, interfaces, dependencies, failure modes, security considerations, test strategy) for all six P0 items in `docs/SECURITY_MODEL.md`'s Prioritized Remediation Checklist, without writing any implementation code — these are prerequisites the maintainer identified as blocking Phase 1, not optional polish.

## Completed Work

Launched four parallel design agents, grouping the six P0 items by concern area:

1. **`docs/design/vault-safety-boundary.md`** — P0 #1 (path-traversal/symlink mechanism) + P0 #3 (`python-frontmatter` YAML-safety verification). Directly verified `python-frontmatter`'s upstream source (not assumed) and confirmed it defaults to `yaml.SafeLoader`; designed `SafeVaultPath`/`resolve_vault_path()` as the single shared implementation every path-accepting entry point (MCP tools, Git module, watcher, reconciliation job) must route through, with a reasoned CREATE-vs-EXISTING-mode resolution for the `Path.resolve(strict=True)` wrinkle, and a specific recommendation to reject in-vault symlinks (not just escaping ones) grounded in AI_BRAIN's own data-model assumptions (unique `notes.path`, folder-based provenance inference).
2. **`docs/design/os-level-process-sandboxing.md`** — P0 #2 (OS-level backstop). Identified that the two AI_BRAIN processes (MCP server via stdio, Huey worker as a daemon) have structurally different lifecycles requiring different sandboxing mechanisms (bubblewrap for the client-spawned MCP server; systemd for the independent Huey worker) — and corrected two specifics in the threat model's own literal wording (`DynamicUser=` doesn't fit AI_BRAIN's vault-ownership model; `ProtectHome=yes`, not `read-only`, is needed given the read-heavy tool surface).
3. **`docs/design/storage-runtime-hardening.md`** — P0 #4 (Huey serializer startup assertion) + P0 #5 (file-permission hardening). Verified Huey's actual serializer API against source. Found that Qdrant's Docker image runs as root by default, meaning host-side permission hardening alone does not constrain the containerized process without also pinning a non-root image variant — a real technical subtlety the naive remediation text didn't anticipate.
4. **`docs/design/pre-ingestion-secret-scanning.md`** — P0 #6. Recommended `detect-secrets` (in-process, no subprocess, no network egress) over gitleaks/trufflehog specifically for this use case, and — reasoning from the real vault's actual content profile (`docs/DATA_MODEL.md` §0's confirmed OWASP security-training folder) — recommended redact-and-flag as the default on-detection behavior rather than hard-block, to avoid making legitimate educational content permanently unsearchable.

All four documents were written to `docs/design/` (a new directory, created this session) in full, without any implementation code, consistent with the constitution's design-before-coding discipline.

## Key Findings

- Two of the security threat model's own remediation *suggestions* needed correction once actually designed in detail: `DynamicUser=yes` and `ProtectHome=read-only` were both shown, via direct verification against systemd's own documentation, to not achieve what the threat model assumed — a concrete example of why "design before coding" catches real problems that a checklist item alone doesn't surface.
- Qdrant's default root-in-container behavior means the file-permission hardening item is incomplete without also addressing container image choice — a cross-cutting dependency between two previously-separate-seeming remediation items.
- The secret-scanning design's on-detection behavior decision was directly shaped by the real vault sample inspected in an earlier session — a concrete case of that earlier structural finding continuing to pay off in downstream design decisions.
- Two of the four designs (secret scanning, and implicitly the serializer/permission hardening) flag the need for follow-on ADRs or ADR amendments once implemented, following the same pattern ADR-0010 established for the events table.

## Files Changed

- `docs/design/vault-safety-boundary.md` (new)
- `docs/design/os-level-process-sandboxing.md` (new)
- `docs/design/storage-runtime-hardening.md` (new)
- `docs/design/pre-ingestion-secret-scanning.md` (new)
- `CURRENT_STATE.md`, `NEXT_SESSION.md`, `CHANGELOG.md`, `SESSION_LOG.md` (updated)
- `docs/sessions/2026-08-27_p0-security-design-docs.md` (this file, new)

## Tests

None — design-only session per CLAUDE.md Phase discipline; no code was written.

## Unresolved Issues

- None of the four designs are implemented yet — this session produced designs only, per the user's explicit request.
- A follow-on ADR is needed for the secret-scanning schema additions (`notes.secret_scan_status`, `note_secret_findings`, `secret_scan_allowlist`) before that design is implemented.
- Each design doc carries its own small set of open questions/empirical Phase-1 checks (e.g., whether `~/.gitconfig` access is needed inside the sandbox, whether `MemoryDenyWriteExecute=yes` survives the embedding pipeline, whether redaction measurably affects retrieval quality) — consolidated in `NEXT_SESSION.md`.

## Next Steps

Review the four design documents (or proceed directly to implementation if no discussion is needed), draft the follow-on secret-scanning schema ADR, then begin Phase 1 implementation per `docs/ROADMAP.md`.

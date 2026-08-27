# Session 007 — Phase 0 Git Automation Library Research

- **Date:** 2026-08-24
- **Phase:** 0 — Architecture & Research
- **Depends on:** Session 004 (ADR-0002, Huey/SQLite job queue)

## Objective

Research and decide AI_BRAIN's Git automation library approach for the knowledge-vault backup/versioning workflow — status detection, safe commits, push policies, rollback/recovery, conflict detection, dry-run, secret scanning — evaluating raw `subprocess`+git CLI, GitPython, pygit2, and Dulwich.

## Completed Work

- Researched all four approaches against security (structural injection prevention, current CVE history), feature completeness, async fit, dependency footprint, behavioral fidelity, error-handling clarity, testability, and maintenance status, using current 2026 primary sources including CVE/advisory databases.
- Researched secret-scanning integration options (detect-secrets, gitleaks, trufflehog, ggshield, git-secrets) and dry-run patterns across all four approaches.
- Wrote `docs/research/2026-08-24_git_automation_library.md` following the Documentation Standards research-doc structure.
- Drafted `docs/adr/0005-git-automation-library.md` recommending a purpose-built subprocess wrapper, initially status Proposed.
- Maintainer reviewed and **accepted ADR-0005 as proposed** — status now Accepted.

## Key Decision

AI_BRAIN's Git automation will be a **purpose-built module wrapping the real `git` CLI via `asyncio.create_subprocess_exec`** — argument lists only, `--`/`--end-of-options` insertion before untrusted paths/refs, explicit branch-name allow-listing, a hand-built exit-code/stderr failure taxonomy, dry-run via git's own flags, and `gitleaks` via the `pre-commit` framework for secret scanning. Dulwich (`--pure` mode) is retained only as an optional, non-load-bearing read-side convenience. GitPython is not adopted.

## Key Finding

Neither "safer-seeming" library is actually injection-risk-free: **GitPython** has an open 2026 config-injection CVE (CVE-2026-42215) and its own maintainer has declared it in maintenance mode, calling its design "deeply flawed and broken beyond repair." **Dulwich**, despite being pure Python, ships a merge-driver feature that internally uses `subprocess.run(..., shell=True)`, producing a real 2026 CVE (CVE-2026-42563, CVSS 8.8, OS command injection). This confirms the constitution's security-first instinct: enforcing the "never construct shell commands from untrusted text" rule directly in AI_BRAIN's own auditable code is safer than trusting an opaque dependency to have done so.

## Files Changed

- `docs/research/2026-08-24_git_automation_library.md` (new)
- `docs/adr/0005-git-automation-library.md` (new, Proposed → Accepted)
- `CURRENT_STATE.md` (updated)
- `NEXT_SESSION.md` (updated)
- `CHANGELOG.md` (updated)
- `SESSION_LOG.md` (updated)
- `docs/sessions/2026-08-24_phase0-git-automation-library.md` (this file, new)

## Tests

None — research-only session per CLAUDE.md Phase discipline; no code was written.

## Unresolved Issues (carried forward as implementation-time checks, not blockers)

- Verify Kali's installed git version supports `--end-of-options` for every subcommand AI_BRAIN needs.
- Defer the subprocess wrapper module's exact interface design until the MCP tool contract design settles the operations it needs to expose.
- Decide gitleaks vs. trufflehog as the primary pre-commit secret scanner.

## Next Steps

Proceed to the next Phase 0 research queue items per `NEXT_SESSION.md`: MCP tool contract design, Qdrant deployment specifics, embeddings model choice, filesystem event architecture.

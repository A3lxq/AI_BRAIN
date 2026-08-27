# ADR-0005: Git Automation Library for AI_BRAIN

- **ID:** ADR-0005
- **Title:** Git Automation Library for AI_BRAIN
- **Status:** Accepted
- **Date proposed:** 2026-08-24
- **Date accepted:** 2026-08-24
- **Depends on:** ADR-0001 (runtime: Python, accepted), ADR-0002 (job queue: Huey/SQLite, accepted — Git operations likely run as background jobs)

## Context

AI_BRAIN needs Git automation for the knowledge-vault backup/versioning workflow: status detection, safe commits with structured messages, configurable/safe push policies, rollback/recovery, conflict detection, dry-run preview, clear operation logging, and pre-commit secret scanning — while never constructing shell commands from untrusted text (`docs/SECURITY_MODEL.md`) and never auto-triggering destructive operations without explicit user intent (CLAUDE.md rules 22–23).

Four approaches were researched: raw `subprocess`/`asyncio.create_subprocess_exec` wrapping the real `git` CLI, GitPython, pygit2 (libgit2 bindings), and Dulwich (pure Python). Full findings: [`docs/research/2026-08-24_git_automation_library.md`](../research/2026-08-24_git_automation_library.md).

Key finding: **neither "safer-seeming" library is actually injection-risk-free.** GitPython has an open, 2026-dated config-injection CVE (CVE-2026-42215) in exactly the risk category the security model warns against, and its own maintainer has declared the project in maintenance mode, calling its design "deeply flawed and broken beyond repair." Dulwich — despite being pure Python — ships a merge-driver feature that itself internally calls `subprocess.run(..., shell=True)`, producing a real 2026 CVE (CVE-2026-42563, CVSS 8.8, CWE-78 OS Command Injection). This shows that delegating Git operations to a third-party abstraction does not automatically satisfy the security model's rule — it can silently reintroduce the exact anti-pattern being guarded against, in code that's harder to audit than AI_BRAIN's own.

## Decision

**Accepted:** Build AI_BRAIN's Git automation as a **purpose-built module wrapping the real `git` CLI via `subprocess`/`asyncio.create_subprocess_exec`** — argument lists only, never `shell=True` — for all operations, especially every mutating one (commit, push, revert, reset, merge). Specifically:

- Insert `--` (universal) and `--end-of-options` (git ≥2.24, verify per-subcommand support on the Kali/Debian git version in use) before any pathspec/ref that could originate from untrusted or dynamic input.
- Explicitly allow-list branch/tag names before they reach argv, since git does not forbid leading-dash ref names.
- Build a hand-written exit-code + stderr-text failure taxonomy (merge conflict / auth failure / network failure / nothing-to-commit).
- Implement dry-run via `git commit --dry-run`, `git push --dry-run`, and `git merge --no-commit --no-ff` where applicable, falling back to `git status --porcelain` + `git diff` for general preview.
- Adopt the standard `pre-commit` framework with **gitleaks** as the pre-commit secret scanner, invoked via subprocess with JSON output.
- Optionally use **Dulwich in `--pure` mode** as a narrowly-scoped, non-load-bearing read-side convenience (status/diff/log for logging or MCP status-tool purposes) — never for mutating operations, and never touching its `ProcessMergeDriver` surface.

The maintainer reviewed the research and comparison and accepted this ADR as proposed on 2026-08-24.

## Alternatives Considered

| Option | Verdict |
|---|---|
| GitPython | Rejected — its own maintainer has declared it in maintenance mode and called its design "broken beyond repair" as of 2026; carries an open, current, injection-class CVE (CVE-2026-42215); offers no async story (requires thread-offload); every convenience it provides (revert, dry-run, conflict detection) bottoms out in raw passthrough to git anyway, adding a dependency and CVE surface without buying real abstraction value. |
| pygit2 | Rejected as primary, viable as a future read-only option — libgit2 is healthy and actively patched, and Linux wheel bundling removes deployment concerns, but its plumbing-not-porcelain nature means merge/pull semantics diverge from what the user experiences running real `git` by hand — a meaningful risk for a vault the user also touches manually. |
| Dulwich (as primary/load-bearing) | Rejected as primary — despite being pure Python, its merge-driver feature contains a real `shell=True` command-injection vulnerability (patched in 1.2.5+, but architecturally concerning); porcelain operations (merge, pull) are independent reimplementations with documented divergence risk from real git (e.g. `porcelain.pull()`'s open overwrite-risk issue). Retained only as an optional, narrowly-scoped read-side convenience. |
| Hybrid (library-for-reads + subprocess-for-writes), wholesale adoption | Rejected as a named pattern — architecturally sound in principle but rare in practice (the one real-world example found, Wayfair's `pygitops`, actually wraps GitPython for everything, not this split); doubles the dependency and failure-mode surface to test and maintain. A narrow version (subprocess for all mutations, Dulwich only for pure inspection) is retained, not the full pattern. |

## Rationale

1. **Behavioral fidelity to real git matters most for a vault the user also touches by hand.** Only the subprocess approach is byte-identical to real git; both library alternatives reimplement porcelain-level operations (merge, pull, conflict resolution) on lower-level primitives, risking divergence exactly where correctness matters most.
2. **Async fit favors subprocess directly**: `asyncio.create_subprocess_exec` is the only native-async option among the four researched; GitPython, pygit2, and Dulwich are all confirmed purely synchronous with no maintained async wrappers.
3. **Security is best enforced in AI_BRAIN's own auditable code**, not delegated to an opaque dependency — the research's central finding (both alternative libraries have had real, recent injection-class vulnerabilities of their own) directly supports this rather than being a hypothetical concern.
4. **No library provides dry-run or failure-mode taxonomy for free anyway** — GitPython's dry-run is submodule-only passthrough, pygit2 and Dulwich have no first-class dry-run at all. AI_BRAIN would hand-build this regardless of library choice, so there's no convenience cost to choosing the subprocess approach.
5. **GitPython's own maintainer-declared maintenance mode and self-assessment** ("deeply flawed and broken beyond repair") is a direct, current, primary-source signal against adopting it as a dependency for security-sensitive, long-lived infrastructure.

## Consequences

- A purpose-built `git` subprocess wrapper module must be designed and built as part of Phase 1, following the constitution's "every significant subsystem requires purpose, responsibilities, interfaces, dependencies, failure modes, security considerations, test strategy" rule (Article 2) — this is non-trivial security-sensitive infrastructure and must be threat-modeled explicitly before implementation (Article 9, CLAUDE.md rule 21).
- All Git mutations (commit, push, revert, reset, merge) go through this module; Dulwich, if adopted at all, is restricted to read-only status/diff/log convenience and must never be relied upon for correctness-critical operations.
- The `pre-commit` framework with `gitleaks` must be set up as part of Phase 1 to satisfy the "never commit secrets" requirement — this is a required security control, not optional.
- Kali's installed git version must be verified to support `--end-of-options` for every subcommand AI_BRAIN uses (`checkout`/`reset` only gained it in git 2.43.1) before relying on it as a mitigation.
- Destructive operations (force-push, hard reset, history rewriting) require explicit safeguards and must never be triggered automatically without explicit user intent — this module must enforce that at the API level (e.g., no destructive operation exposed without an explicit confirmation parameter), not merely by convention.

## References

See [`docs/research/2026-08-24_git_automation_library.md`](../research/2026-08-24_git_automation_library.md) §11 for the full primary-source citation list, including specific CVE/advisory IDs.

## Open Questions

Resolved: the maintainer accepted this ADR as proposed on 2026-08-24, with no modifications requested.

Remaining open item, carried forward as an implementation-time decision rather than a blocking question: whether the subprocess wrapper module should be designed now or deferred until the MCP tool contract design settles the exact Git operations AI_BRAIN needs to expose. Recommend deferring to keep the module's interface driven by real call sites.

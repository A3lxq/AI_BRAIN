# AI_BRAIN — Git Workflow

## Principles

Git provides:
- source control,
- recovery,
- auditability,
- reproducibility,
- knowledge backup.

## Repository model

The AI_BRAIN software repository is separate from the Obsidian knowledge vault repository.

The vault should have its own Git repository if it is to be backed up and versioned.

Do not mix application source and personal knowledge into one repository unless explicitly designed and reviewed.

## Commit policy

Commits should be:
- small,
- meaningful,
- related to one logical change,
- free of secrets,
- testable.

## Example

`feat(retrieval): add hybrid candidate retrieval`

## Automatic Git

AI_BRAIN may automate commits and pushes, but automation must:
- be configurable,
- detect failures,
- avoid destructive commands,
- never commit secrets,
- provide clear logs,
- allow dry-run mode,
- support manual review.

## Branching

The project may use:
- main
- feature branches
- release tags

Exact policy will be finalized after the initial repository setup.

## Backup

The knowledge vault should be pushed regularly to a private Git remote.

Never assume Git is a substitute for independent backup. A future backup strategy should consider multiple remotes or other storage where appropriate.

---

## Operational Runbooks (Phase 0 exit-criteria deliverable, 2026-08-26)

The sections above state the principles and policy. These runbooks make them concrete and actionable against AI_BRAIN's accepted architecture (ADR-0002, ADR-0005, ADR-0006, ADR-0007).

### Qdrant snapshot-before-upgrade procedure

Per ADR-0006: Docker-server deployment, pinned image tags, no version-skip, **no downgrade path once migrated**. "Rollback" means *restoring a pre-upgrade snapshot into a fresh instance running the old, pre-upgrade version* — never running an old Qdrant binary against data a newer version has already written to, since that path is unsupported.

1. **Pre-flight check**: confirm the currently-running image tag and that the target is exactly one minor version ahead (Qdrant's documented policy forbids skipping minor versions — if more than one minor version behind, this whole procedure repeats once per version).
2. **Snapshot**: trigger a full collection snapshot via Qdrant's snapshot API for every collection in use, writing to a location outside the container's ephemeral storage. Record the snapshot filename, collection name, and pre-upgrade image tag together — the tag matters because a snapshot is only restorable into a same-or-compatible version.
3. **Verify the snapshot**: don't treat "the API returned success" as sufficient. Confirm the snapshot file exists on the host with non-trivial size; optionally restore it into a *second, throwaway* container running the *same current (pre-upgrade) version* and run a small known-query sanity check (point count, a known top result) before touching production.
4. **Upgrade one minor version**: stop the production container; pull the new pinned tag (never `:latest`); start against the existing persistent volume; wait for health.
5. **Verify collection health post-upgrade**: collection status green/healthy; point counts match pre-upgrade snapshot; re-run the same sanity queries; confirm hybrid fusion (RRF/DBSF) still returns sane results, since an upgrade is exactly the kind of event that could silently change fusion behavior.
6. **Repeat if multiple versions behind**: return to step 2 for the next minor version — never skip directly to a distant target, since that's outside Qdrant's supported/tested upgrade path.
7. **Rollback if verification fails**: stop the upgraded container; start a *fresh* container pinned to the pre-upgrade tag, pointed at a clean volume with the verified pre-upgrade snapshot restored into it (the safe default — don't assume the original volume is still safely readable by the old version after a failed upgrade attempt). This is a *restore*, not a downgrade: no Qdrant version is ever asked to read data a newer version wrote. Document what failed before retrying.
8. **Post-upgrade cleanup**: after a soak period (e.g. a few days of normal operation), retire the pre-upgrade snapshot per AI_BRAIN's backup retention policy — keep at least one prior snapshot generation as a safety margin, don't delete immediately after a successful upgrade.

### gitleaks pre-commit setup

Per ADR-0005: the standard `pre-commit` framework with gitleaks as the secret scanner.

- A `repos` entry in `.pre-commit-config.yaml` pointing at gitleaks' pre-commit-compatible repository, pinned to a specific tagged release — never a floating branch, consistent with pinning the Qdrant image tag for the same reason.
- Runs at the `pre-commit` stage (blocking `git commit` locally before a commit object exists) as the primary control, duplicated in CI (scanning the full PR diff/history) as defense-in-depth, since local hooks can be bypassed with `--no-verify`.
- A repository-specific `.gitleaks.toml` for any narrowly-scoped, exact-fingerprint allowlist entries for known false positives — reviewed and committed visibly, never a silent local workaround.
- **On a detected secret**: the commit is blocked outright, nothing enters local Git history — catching it before a commit object exists is far cheaper than removing it after, since a committed secret requires history rewriting to fully remove (which the constitution's caution around destructive Git operations makes deliberately hard to do casually). gitleaks' output (file, line, rule) surfaces directly in the terminal.
- **False-positive handling specific to AI_BRAIN's domain**: vault notes and test fixtures may legitimately contain secret-shaped strings (example API keys in docs, planted fake-secret test fixtures per the testing strategy's security test methodology). Handle via gitleaks' inline allowlist mechanism or tightly-scoped fixture-path exclusions, reviewed at PR time — never broad path exclusions.
- **AI_BRAIN's own automated commit path** (below) must treat a gitleaks block as a first-class, visibly-surfaced failure mode, not a silent failure to commit.

### AI_BRAIN's own automated Git commit/push policy

Grounded in ADR-0005's subprocess wrapper, ADR-0007's `git_commit` MCP tool, and the constitution's requirement that automation be configurable, safe, dry-run-capable, and never auto-trigger destructive operations.

- **Auto-commit: enabled by default, narrowly scoped.** Every successful mutating vault operation (`note_create`, `note_update`, `note_move`, `note_merge`, `note_link`, the `research_commit` write step) auto-triggers `git_commit` as its final step, with a structured, machine-generated commit message encoding operation type, affected path(s), and a short provenance tag (e.g. `feat(vault): update "Project Ideas.md" via note_update`). This gives CLAUDE.md rule 24 ("preserve provenance") a literal, queryable Git-history implementation, and rule 17 ("important project knowledge must never exist only in chat") a recovery path independent of AI_BRAIN's own DB/index state.
- **Per-operation, not batched/periodic** — each logical mutation gets its own small commit, consistent with this doc's existing "small, meaningful, one logical change" commit policy, rather than accumulating a working-tree diff on a timer (which would blur provenance and make `git log` less useful as an audit trail).
- **Push policy: manual by default, auto-push an explicit opt-in.** Push failures (auth/network) are a real, not-infrequent failure mode for a single local machine, so the safer default is: auto-commit locally on every mutation, but push either manually (user-triggered) or via a separate, coarser periodic Huey job (e.g. hourly/end-of-session) rather than on every commit — this avoids hammering the remote on rapid edit bursts and keeps push failures decoupled from the interactive vault-editing path (a failed push must never make a `note_update` MCP call appear to fail from the caller's perspective).
- **Configurability**: both auto-commit and auto-push (and auto-push cadence, if enabled) are config-file toggles. Auto-commit can reasonably default on (local-only, non-destructive); auto-push touches an external system and defaults off, per the constitution's "must be configurable" and "never auto-push unreviewed destructive changes" language — push itself, not just destructive operations, merits a conservative default.
- **Dry-run surfaced via MCP**: every mutating tool with an implicit `git_commit` supports `dry_run` (ADR-0007) such that `dry_run=true` previews both the vault mutation and the resulting commit message/diff without performing either — the MCP response should include the would-be commit message and a `git status --porcelain`/`git diff`-based preview. Standalone `git_commit`/`git_status`/`git_log` behave identically for dry-run.
- **Failure detection and logging**: every automated commit/push attempt (success or failure) is logged with a structured entry (timestamp, operation, outcome, failure-taxonomy classification per ADR-0005 if failed), queryable via `system_diagnostics`/`vault_status` — so a failed background push doesn't silently go unnoticed for days, which would defeat the point of automating it.
- **Explicit non-goals**: never auto-resolve a merge conflict; never force-push to recover from a rejected push (force-push is excluded from the MCP surface entirely per ADR-0007); never retry a failed push indefinitely in a tight loop (bounded retry with backoff, then a standing "push pending, N commits ahead" status via `vault_status`).

### Branching policy (AI_BRAIN's own software repository)

Governs the AI_BRAIN codebase repository — distinct from the vault repository's auto-commit/push policy above.

- **`main`**: default branch, always CI-passing. Direct commits avoided in favor of merged feature branches even for a solo maintainer, anchoring the constitution's "every significant decision gets an ADR / every feature gets a design doc" discipline to reviewable PR boundaries.
- **Feature branches**: `feat/<short-description>`, `fix/<...>`, `chore/<...>`, `docs/<...>` — matching this doc's existing commit-message convention. Scoped to one ADR/design-doc's worth of work where practical (e.g. `feat/git-automation-module` for ADR-0005).
- **Merge strategy**: merge commit or squash-merge, consistently applied (pick one); avoid rebasing shared/pushed branches once collaboration has started.
- **Release tags**: tag `main` at meaningful phase-boundary milestones (semantic-ish versioning, e.g. `v0.1.0` for first working Phase 1 foundation) — primarily as recoverable checkpoints per CLAUDE.md rule 25, not a public release process.
- **No long-lived parallel branches**: avoid a long-running `develop` branch alongside `main` given the solo/small-team context — the added merge overhead isn't justified at this scale, analogous to ADR-0002's rejection of Celery's clustered-deployment complexity for a single-machine tool.
- **Protection**: gate merges to `main` on CI passing wherever the hosting platform supports it, operationalizing the constitution's "a feature cannot be marked complete solely because a happy-path test passes" at the branch-protection level.

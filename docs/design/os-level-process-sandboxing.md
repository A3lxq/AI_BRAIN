# Design: OS-Level Process Sandboxing (systemd/bubblewrap hardening backstop)

- **Date:** 2026-08-27
- **Author:** Claude Code (ATHENA AI-BRAIN Phase 0)
- **Status:** Design — addresses `docs/SECURITY_MODEL.md` P1 remediation item #9 / TB-3 finding "No OS-level backstop exists"
- **Depends on / informed by:** ADR-0001 (Python/Kali), ADR-0002 (Huey job queue), ADR-0005 (Git automation, subprocess model), ADR-0006 (Qdrant Docker, 127.0.0.1), ADR-0007 (MCP tool contract, transport undecided — stdio recommended)
- **Non-goal:** This document does not decide MCP transport (a separate P1 item/ADR). It assumes stdio-only for Phase 1, per `docs/SECURITY_MODEL.md`'s recommendation, and designs for that case explicitly.
- **Research cutoff / freshness note:** All systemd directive claims below were checked against current sources on 2026-08-27. systemd's official man pages returned HTTP 403 to direct fetch during research; content was cross-verified against the Debian manpage mirror (tracks unstable/current systemd) and Lennart Poettering's own primary-source blog post on `DynamicUser=`, both cited inline.

## 1. Purpose & Scope

### What this hardening layer is for

`docs/SECURITY_MODEL.md` TB-3 names the single largest structural gap in ATHENA AI-BRAIN's Phase 0 design: the path-traversal defense (canonicalize vault root, `Path.resolve(strict=True)` + ancestor check — designed in `docs/design/vault-safety-boundary.md`) is **100% application-level Python logic**. If that logic has a bug — and CWE-22/CWE-59 bugs recur even in mature, reviewed software — there is currently nothing between a buggy `note_create`/`note_move`/`research_commit` call and the full read/write privilege of whichever Kali user account launched ATHENA AI-BRAIN: SSH keys, browser profiles, shell history, everything in `$HOME`.

This design adds an OS-enforced containment boundary around ATHENA AI-BRAIN's own processes (MCP server, Huey worker) so that **even if the path-canonicalization logic is wrong, the OS refuses the resulting filesystem operation.**

### What this explicitly does NOT protect against

Stated plainly, because `docs/SECURITY_MODEL.md` demands honesty about mitigation strength, not aspirational framing:

1. **It is not a substitute for the path-traversal fix.** It is a backstop for that fix having a bug — the correct application-level check must still be built and tested. A sandbox that "contains" a traversal bug by turning a silent corruption into a loud `EACCES`/`EPERM` failure is a containment success, not a reason to skip the vault safety boundary design.
2. **It does nothing against a fully-authorized operation that legitimately writes somewhere the user asked it to.** Sandboxing narrows the blast radius of a bug, it does not second-guess authorized intent.
3. **It does nothing against TB-2's prompt-injection risk.** If an injected instruction gets an unconfirmed `note_create`/`git_commit` to run *inside the vault*, the sandbox will happily permit it — the write is inside the allowed `ReadWritePaths=`. Sandboxing and injection defense are orthogonal; this document only closes TB-3.
4. **It does nothing against a compromised dependency that finds another way to exfiltrate data it already has legitimate access to** — the sandbox restricts *filesystem escape*, not *what the process does with data it's authorized to touch*. TB-12's supply-chain mitigations are the actual control for that risk.
5. **It is not a seccomp-hardened, minimal-attack-surface sandbox in the container-security sense** — see §3.5's fragility discussion for why a narrow syscall allowlist is deliberately not recommended.

This is explicitly "what happens if the path-canonicalization fix has a bug," not a replacement for fixing the logic itself.

## 2. Deployment Model Reasoning

### The tension, stated precisely

- **ADR-0007** leaves MCP transport undecided but `docs/SECURITY_MODEL.md` P1 item #7 recommends **stdio-only for Phase 1**. Under stdio transport, ATHENA AI-BRAIN's MCP server process is **spawned as a child process of the MCP client itself** — never started by systemd. Systemd unit hardening is architecturally inapplicable to a process whose parent is the MCP client, not `systemd --user`/PID 1.
- **The Huey worker**, by contrast, is not spawned per-request by an MCP client. Per ADR-0002/ADR-0009, it needs to run independently and continuously — a genuine "long-running background daemon," exactly what systemd is designed for.

So the two processes ADR-0002 and ADR-0007 already establish as separate have **structurally different lifecycles**, and that difference should drive the sandboxing mechanism, not be smoothed over by forcing both into one deployment model.

### Recommendation: support both, mapped to what each process actually is

| Process | How it's actually launched (Phase 1) | Sandboxing mechanism |
|---|---|---|
| **MCP server** (stdio) | Spawned as a child process of the MCP client (Claude Code/Claude Desktop), per its own `command`/`args` config entry | **bubblewrap (`bwrap`) wrapper script**, referenced as the `command` in the client's MCP server config |
| **Huey worker(s)** | Independent background daemon, started at login (or boot, if linger enabled) | **`systemd --user` unit** with full hardening directives |

This is not a compromise:

- Forcing the MCP server into a systemd unit would mean the MCP client can no longer directly manage the process it thinks it's spawning (stdout/stdin framing for the MCP protocol would have to be proxied through `systemctl`) — this doesn't match ADR-0007's transport model.
- Forgoing sandboxing of the MCP server because "it's usually launched by hand" would leave the *higher-traffic, directly LLM-facing* process completely unhardened, precisely backwards given TB-2/TB-3 both name it as the highest-exposure component.
- The bubblewrap approach is not exotic for this use case — a directly relevant 2026 write-up ("Sandboxing the Claude Code CLI on Linux," esokia labs) documents this identical pattern: wrapping an LLM-agent-adjacent CLI tool, launched as a plain subprocess (not via systemd), in a `bwrap` layer that mounts `$HOME` read-only, bind-mounts one writable project directory, and lets child-process execution (git) work transparently since `bwrap` operates via namespaces at the process tree's root, which fork/exec-descend automatically — the same mechanism this design relies on for ATHENA AI-BRAIN's `git`/`gitleaks` subprocesses (§4).

### If MCP transport ever moves to HTTP (out of scope, flagged for the future)

If a future ADR reverses the stdio recommendation and adds HTTP transport, the MCP server would then be a standalone long-running network daemon exactly like the Huey worker, and the same `systemd --user` unit hardening in §3 would apply directly — no new sandboxing mechanism needed, only a new unit file using the same directive set.

### System unit vs. user unit for the Huey worker

Recommend **`systemd --user`**, not a system-level unit, because:
- ATHENA AI-BRAIN's entire footprint (vault, SQLite files, model cache) already lives under the interactively-logged-in user's own account — no privilege-separation reason to run as a distinct system service.
- A user unit starts/stops naturally with the user's login session, matching how a solo-developer Kali workstation is actually used (CLAUDE.md's own framing: "a learning system built by one person"). If ATHENA AI-BRAIN needs to keep running after logout, `loginctl enable-linger <user>` is the documented mechanism — an operational choice, not a blocker.

## 3. Concrete Unit File Design(s)

### 3.1 Why `DynamicUser=yes` — as literally suggested — is not recommended, and what to use instead

This is a deliberate, reasoned deviation from the threat model's literal remediation text, surfaced explicitly (per CLAUDE.md rule 10 — this is a design-doc-level refinement of an unresolved P1 item, not a redesign of an accepted ADR).

`DynamicUser=yes` allocates a throwaway UID at service start and releases it at stop. It automatically implies `ProtectSystem=strict`, `ProtectHome=read-only`, `NoNewPrivileges=yes`, and more — a strong, convenient default bundle.

**The problem specific to ATHENA AI-BRAIN:** the vault ATHENA AI-BRAIN must read/write is owned by the human's own login UID, typically not group/other-writable. `ReadWritePaths=` only controls **mount-namespace visibility**, not **Unix DAC permission bits**. A dynamically-allocated UID with no group membership in common with the human user will get `EACCES` trying to write into the human's own vault directory, regardless of `ReadWritePaths=` listing it. Workarounds exist (POSIX ACLs, `SupplementaryGroups=`) but none are clean or compatible with `DynamicUser=`'s fully-synthetic identity model.

**Recommendation:** run both units as the human's own account. For a `systemd --user` unit this is automatic. This gives up `DynamicUser=`'s "no persistent identity to compromise" property, but that property was never ATHENA AI-BRAIN's actual threat model — the threat is a *path-traversal bug*, not *credential theft of a service account* — and every other hardening directive (`ProtectSystem=strict`, `ProtectHome=`, `ReadWritePaths=`, `NoNewPrivileges=`) works identically for a fixed user as for a dynamic one; `DynamicUser=` merely *implies* a subset of them as a convenience default.

### 3.2 `ProtectHome=yes`, not `ProtectHome=read-only` — a correction to the threat model's literal wording

`docs/SECURITY_MODEL.md` suggests `ProtectHome=` "scoped to read-only except the vault path." Per the systemd.exec documentation: `ProtectHome=read-only` makes `/home`, `/root`, `/run/user` **readable, not inaccessible** — it stops writes, not reads.

This matters because TB-3's threat isn't only "a bug causes an unintended *write*" — it's also "a bug in a *read* path (`note_read`, `vault_search`) resolves outside the vault and the content is returned in a tool response to the calling LLM." Under `ProtectHome=read-only`, `~/.ssh/id_rsa` could still be successfully read and exfiltrated via that tool response. Given ATHENA AI-BRAIN's read-heavy tool surface (10 of ADR-0007's tools are read-only), **`ProtectHome=yes` (full inaccessibility)** is the correct directive, with the vault path punched back open via an explicit `ReadWritePaths=` entry (which takes precedence over the broader restriction it sits inside).

### 3.3 Directive-by-directive design (shared baseline for both units)

| Directive | Value | Purpose |
|---|---|---|
| `NoNewPrivileges=` | `yes` | Process and **all its children** (git, gitleaks) can never gain privileges via `execve()` |
| `ProtectSystem=` | `strict` | Entire filesystem read-only except API pseudo-filesystems and explicit exceptions |
| `ProtectHome=` | `yes` | `/home`, `/root`, `/run/user` fully inaccessible (not merely read-only — §3.2) |
| `ReadWritePaths=` | vault path + ATHENA AI-BRAIN state dir | Explicit exception carved back through `ProtectSystem=strict`/`ProtectHome=yes` |
| `ReadOnlyPaths=` | embedding model cache dir | ATHENA AI-BRAIN reads model weights but should never need to write there once downloaded |
| `PrivateTmp=` | `yes` | Isolated `/tmp`, `/var/tmp` |
| `PrivateDevices=` | `yes` | Hides physical/hardware device nodes — caveat: relax via `DeviceAllow=` if GPU-accelerated inference is ever adopted (ADR-0008 assumes CPU-only today) |
| `ProtectKernelTunables=` | `yes` | Blocks writes to `/proc/sys`, `/sys` |
| `ProtectKernelModules=` | `yes` | Blocks kernel module load/unload |
| `ProtectKernelLogs=` | `yes` | Blocks access to the kernel log ring buffer |
| `ProtectControlGroups=` | `yes` | Blocks writes to the cgroup filesystem |
| `ProtectClock=` | `yes` | Blocks changing the system clock/RTC |
| `ProtectHostname=` | `yes` | Blocks changing the system hostname |
| `ProtectProc=` | `invisible` | Hides other users' `/proc/<pid>` entries (systemd ≥247; Kali/Trixie ships 257) |
| `RestrictSUIDSGID=` | `yes` | Process cannot create SUID/SGID files |
| `RestrictNamespaces=` | `yes` | Process (and children) cannot create new namespaces — blocks a sandbox-escape technique |
| `LockPersonality=` | `yes` | Blocks changing the process execution domain |
| `RestrictRealtime=` | `yes` | Blocks real-time scheduling — irrelevant to ATHENA AI-BRAIN's workload, zero cost |
| `RemoveIPC=` | `yes` | Cleans up leftover IPC objects on stop |
| `CapabilityBoundingSet=` | *(empty)* | ATHENA AI-BRAIN needs zero Linux capabilities |
| `RestrictAddressFamilies=` | `AF_INET AF_INET6 AF_UNIX` | Outbound HTTPS to LLM providers needs `AF_INET`/`AF_INET6`; `AF_UNIX` needed for local DNS via `systemd-resolved`'s stub socket — no `AF_NETLINK`, `AF_PACKET`, etc. |
| `UMask=` | `0077` | New files default owner-only, reinforcing P0 item #5's file-permission hardening |
| `SystemCallFilter=` | `@system-service` (§3.5) | Broad, well-tested syscall group covering ordinary daemon behavior |
| `SystemCallErrorNumber=` | `EPERM` (during rollout) | Returns a catchable Python `PermissionError`/`OSError` instead of `SIGSYS`-killing the process — critical for diagnosability (§5) |
| `MemoryDenyWriteExecute=` | *test before enabling — §3.5* | Blocks W^X-violating memory mappings — risk: may break JIT-compiling ML runtimes |

### 3.4 Per-unit differences

Both processes need nearly identical filesystem access because both call into the same business-logic layer (`docs/ARCHITECTURE.md` §2: every MCP tool wraps an internal function, and that same function is reachable from Huey jobs). Differences are narrow:

- **MCP server**: primarily a request/response process, no periodic-job concerns.
- **Huey worker**: needs long-run stability (`Restart=on-failure`), and per ADR-0002's periodic Git-backup job, needs the same `git` subprocess access as the MCP server.

In practice these directive blocks are ~90% identical and would typically be factored into a systemd template unit (`ai-brain@.service`) during implementation. Two full files are shown here for clarity of review.

**`ai-brain-huey-worker.service`** (installed as `~/.config/systemd/user/ai-brain-huey-worker.service`):

```ini
[Unit]
Description=ATHENA AI-BRAIN Huey background worker
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/ai-brain/.venv/bin/python -m ai_brain.worker
Restart=on-failure
RestartSec=5

# --- Filesystem sandboxing ---
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=%h/ObsidianVault %h/.local/state/ai-brain
ReadOnlyPaths=%h/.cache/huggingface
PrivateTmp=yes
PrivateDevices=yes
StateDirectory=ai-brain
LogsDirectory=ai-brain

# --- Privilege / capability restriction ---
NoNewPrivileges=yes
CapabilityBoundingSet=
RestrictSUIDSGID=yes
RestrictNamespaces=yes
LockPersonality=yes
RestrictRealtime=yes
RemoveIPC=yes
UMask=0077

# --- Kernel/system surface restriction ---
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
ProtectProc=invisible

# --- Network restriction ---
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

# --- Syscall filtering (see §3.5 — verify empirically before enforcing) ---
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM
# MemoryDenyWriteExecute=yes   # enable only after §7 test confirms embedding pipeline survives

[Install]
WantedBy=default.target
```

**MCP server** — *not* a systemd unit per §2's reasoning; a `bwrap` wrapper script referenced from the MCP client's config:

```bash
#!/usr/bin/env bash
# ai-brain-mcp-launch.sh — sandboxed launcher for ATHENA AI-BRAIN's stdio MCP server.
set -euo pipefail

VAULT_DIR="${AI_BRAIN_VAULT_DIR:?set AI_BRAIN_VAULT_DIR}"
STATE_DIR="${HOME}/.local/state/ai-brain"
MODEL_CACHE="${HOME}/.cache/huggingface"
VENV="${HOME}/ai-brain/.venv"

exec bwrap \
  --clearenv \
  --setenv PATH "/usr/bin:/bin" \
  --setenv AI_BRAIN_VAULT_DIR "$VAULT_DIR" \
  --unshare-pid \
  --unshare-uts \
  --unshare-cgroup \
  --die-with-parent \
  --ro-bind /usr /usr \
  --ro-bind /bin /bin \
  --ro-bind /lib /lib \
  --ro-bind-try /lib64 /lib64 \
  --ro-bind /etc/resolv.conf /etc/resolv.conf \
  --ro-bind /etc/ssl /etc/ssl \
  --dir /home/"$USER" \
  --perms 0000 --dir "$HOME"/.ssh \
  --perms 0000 --dir "$HOME"/.aws \
  --perms 0000 --dir "$HOME"/.gnupg \
  --bind "$VAULT_DIR" "$VAULT_DIR" \
  --bind "$STATE_DIR" "$STATE_DIR" \
  --ro-bind "$MODEL_CACHE" "$MODEL_CACHE" \
  --ro-bind "$VENV" "$VENV" \
  --proc /proc \
  --dev /dev \
  --tmpfs /tmp \
  --share-net \
  -- "$VENV/bin/python" -m ai_brain.mcp_server
```

Purpose of the non-obvious flags:
- `--unshare-pid`/`--unshare-uts`/`--unshare-cgroup` without `--unshare-net`: preserves the host network namespace entirely (needed for outbound HTTPS to LLM providers and loopback access to Qdrant on `127.0.0.1:6333`), while isolating PID/hostname/cgroup view.
- `--die-with-parent`: if the MCP client dies, the sandboxed ATHENA AI-BRAIN process dies with it — no orphaned process holding the vault open.
- Explicit `--perms 0000 --dir` over `.ssh`/`.aws`/`.gnupg` rather than relying only on a blanket read-only `$HOME`: belt-and-suspenders, matching the concrete pattern used for exactly this purpose in the cited Claude Code CLI bubblewrap write-up.
- `--clearenv` + explicit `--setenv`: prevents environment-variable leakage (inherited API keys, SSH agent socket paths) into the sandboxed process.

### 3.5 `SystemCallFilter=` — is seccomp filtering worth it here?

**Recommendation: yes, but only the broad `@system-service` group — not a hand-built per-syscall allowlist.**

- systemd's `@system-service` group is explicitly documented as "a broad set covering what well-behaved daemons typically need" — appropriate for a Python asyncio process doing filesystem watching, SQLite access, network I/O, and subprocess spawning.
- A **narrow, hand-picked allowlist is the wrong tool here**: real-world reports show even simple, common tools breaking under moderately restrictive filters, and Python's own asyncio subprocess-management code has known interactions with restrictive process-signal/syscall handling. ATHENA AI-BRAIN's syscall surface is the union of Python's asyncio runtime, `aiosqlite`, PyTorch/`sentence-transformers` (which may use `mbind`/`madvise`/`prctl`), `qdrant-client`'s networking, **and** whatever `git`/`gitleaks` need as inherited children (§4) — hand-building and maintaining a minimal correct allowlist across dependency upgrades is a high-maintenance, high-false-positive-risk undertaking for a solo-maintained project. This is over-engineering for how ATHENA AI-BRAIN is actually run.
- `SystemCallErrorNumber=EPERM` (rather than the default seccomp "kill" action) is specifically recommended during rollout: a filtered syscall then raises a normal Python `OSError`/`PermissionError` rather than the process being silently `SIGSYS`-terminated with no application-level trace.

### 3.6 Kali/systemd version check

Kali tracks Debian's rolling/testing base; Debian 13 "Trixie" (released 2025-08-09) ships **systemd 257** (vs. Bookworm's 252). All directives used above are available in systemd 257 — no version gate needed for a current Kali install. Should still be verified against the actual installed `systemd --version` at deployment time, consistent with ADR-0005's own precedent of verifying the installed `git` version before relying on `--end-of-options`.

## 4. Interaction with Subprocess Spawning (git, gitleaks)

**Finding: yes, systemd's (and bubblewrap's) sandboxing of the parent automatically constrains child processes — this is not opt-in per-child, it's a structural property of Linux namespaces and seccomp filters**, confirmed directly against the systemd.exec documentation's own wording for `NoNewPrivileges=`: "ensures that the service process **and all its children** can never gain new privileges through `execve()`."

The mechanism, stated precisely:

- **Mount namespace** (`ProtectSystem=`, `ProtectHome=`, `ReadWritePaths=`, `PrivateTmp=`): systemd constructs the private mount namespace *before* `execve()`-ing the unit's main process. Every process that main process subsequently `fork()`s/`exec()`s (`asyncio.create_subprocess_exec("git", ...)`) inherits that same mount namespace by default — namespace membership persists across `fork()`/`exec()` unless a process explicitly calls `unshare()`/`setns()`, and both require `CAP_SYS_ADMIN`, already excluded from `CapabilityBoundingSet=` and further blocked by `RestrictNamespaces=yes`. Practically: `git commit`, `git push`, and `gitleaks` running as children see exactly the same filesystem view as the parent.
- **`NoNewPrivileges=`**: inherited across `execve()` by children forever.
- **Seccomp filter**: a BPF program attached to the process, inherited by every child and only narrowable further, never widened. This is precisely why §3.5 recommends the broad group rather than a narrow allowlist: `git`/`gitleaks` must also operate within whatever filter is set on the parent, and a filter tuned only for ATHENA AI-BRAIN's own Python code would cause `git` subprocesses to be killed mid-operation — hard to debug if `SystemCallErrorNumber=EPERM` weren't set.
- **Bubblewrap** works identically in spirit: the sandbox is established once, at the root of the process tree, and child processes remain inside the same mount/network namespace configuration as the wrapped parent.

**Implication for ADR-0005's Git Automation Module:** no code changes required — it was already designed around argument-list-only subprocess invocations, exactly the pattern that inherits sandboxing cleanly. The one operational risk is **scope, not mechanism**: `ReadWritePaths=`/`ReadOnlyPaths=` must include everything `git` itself needs — the vault's `.git` directory (already covered) and global git config at `~/.gitconfig` if used (**not** covered by default under `ProtectHome=yes`, must be added as a `ReadOnlyPaths=` exception or replaced with `GIT_CONFIG_GLOBAL=/dev/null` + explicit `-c` flags, consistent with ADR-0005's "argv-only, nothing implicit" philosophy). **This is a concrete Phase 1 implementation checklist item**: verify empirically (§7) that `git commit` succeeds inside the sandbox without `~/.gitconfig`, and if it does need it, add a narrowly-scoped exception rather than widening `ProtectHome=`.

## 5. Failure Modes

| Failure scenario | Symptom | Diagnosis path |
|---|---|---|
| `ReadWritePaths=` doesn't cover a path ATHENA AI-BRAIN legitimately needs | `PermissionError`/`OSError` from Python; `git` subprocess exits nonzero with an opaque error | `journalctl --user -u ai-brain-huey-worker.service -e`; `systemd-analyze security` re-confirms which directive is active; extend `system_diagnostics` (ADR-0007) with a "sandbox self-test" — canary read/write against each configured path at startup |
| `SystemCallFilter=@system-service` is missing a syscall a dependency update starts using | With `SystemCallErrorNumber=EPERM`: a catchable `OSError`. Without it: silent `SIGSYS`-termination, no application-level log line | This is exactly why `SystemCallErrorNumber=EPERM` is specified — always keep it during any dependency upgrade window |
| `MemoryDenyWriteExecute=yes` breaks a JIT-compiling ML backend | Crash or `SIGSEGV`/mmap failure specifically during embedding-model load or first inference | Bisect by toggling the directive off; if confirmed, omit rather than degrading the embedding pipeline |
| `ProtectHome=yes` blocks something outside `ReadWritePaths=`/`ReadOnlyPaths=` (locale files, fontconfig cache) | Opaque import-time or first-use failure | `systemd-analyze security` plus a first-run smoke test covering cold-start of every dependency |
| bubblewrap wrapper script bug (MCP-server path) | MCP client reports the server exited immediately or failed the handshake — harder to diagnose, no `journalctl` equivalent | Run the `bwrap` command manually from a terminal to see stderr directly; add a debug flag with `set -x` |

**General diagnostic principle:** every failure mode above degrades to a **loud, immediate error**, never a silent bypass — the property that makes the sandbox worth having. Phase 1 implementation should make ATHENA AI-BRAIN's own error handling surface *which* directive blocked *what path* as clearly as systemd's own logs do.

## 6. Security Considerations (residual risk)

1. **The path-canonicalization bug still needs fixing.** This design is worthless if read as "we don't need to get the vault-safety-boundary design exactly right because the sandbox will catch it" — the sandbox catches *filesystem escapes*, not logic errors that stay within the vault (e.g., a bug letting one vault note overwrite another arbitrary vault note is invisible to this sandbox entirely).
2. **An attacker who can influence ATHENA AI-BRAIN's own configuration** (the vault path, unit file, wrapper script) can simply widen `ReadWritePaths=` — this design assumes the attacker's foothold is *through a tool call*, not through direct control of deployment configuration.
3. **`AF_INET`/`AF_INET6` being permitted at all means the sandbox does nothing against TB-9's exfiltration risk** — it can't distinguish "ATHENA AI-BRAIN's own legitimate LLM call" from "an injection-triggered exfiltration call" using the same code path. That's TB-2/TB-9's territory, not something OS sandboxing can close.
4. **`systemd-analyze security`'s score is a heuristic, not a proof** — useful to guide hardening and as a regression gate, not a claim of "safe."
5. **The bubblewrap path has a materially different maturity/audit profile than the systemd path.** systemd's directives are extensively used across the ecosystem; ATHENA AI-BRAIN's specific `bwrap` invocation is bespoke and needs the same careful review/testing as any other security-sensitive module.
6. **`DynamicUser=`'s rejection (§3.1) means ATHENA AI-BRAIN's processes run under the human's own identity.** If a different, unrelated local process under the same user is compromised, it already shares that UID and filesystem permissions — this sandbox protects against ATHENA AI-BRAIN's own bugs, not against a sibling process under the same account, consistent with `docs/SECURITY_MODEL.md`'s own TB-3 framing.

## 7. Test Strategy

### 7.1 Positive-path tests (does it still work at all)

1. **Cold-start smoke test**: start the sandboxed Huey worker unit and MCP server wrapper from a clean environment; confirm both reach a ready state.
2. **Full legitimate-operation pass**: run each of ADR-0007's mutating tools against a real (throwaway/test) vault under the sandbox, confirming success — exercises `ReadWritePaths=`, the git-subprocess inheritance path (§4), and embedding inference (exercising the `MemoryDenyWriteExecute=` risk) in one pass.
3. **`systemd-analyze security ai-brain-huey-worker.service`** run as a regression check pre-deployment — track the exposure score over time; a regression should require justification.

### 7.2 Negative-path tests (does it actually contain a bypass)

4. **Direct bypass simulation.** A standalone test harness, running **inside** the sandboxed process, simulating "the path-canonicalization check having already failed" (not testing that check itself — that's the vault-safety-boundary design's own test suite):
   - `Path("/home/<user>/.ssh/id_rsa").read_bytes()` → expect `PermissionError` (confirms `ProtectHome=yes`, not merely `read-only`).
   - `Path("/home/<user>/.ssh/id_rsa").write_bytes(b"pwned")` → expect `PermissionError`.
   - `Path("/etc/shadow").read_bytes()` → expect `PermissionError` (confirms `ProtectSystem=strict`).
   - A symlink planted inside the vault pointing to `~/.bashrc`, resolved and written to via the *same code path* `note_update` would use → expect the sandbox to block the write even though this bypasses the application check entirely.
5. **Differential test**: run test #4 against both a sandboxed and an unsandboxed instance (directives stripped), asserting the sandboxed run fails closed while the unsandboxed run "succeeds" — proving the sandbox, not some other accidental permission, provides containment.
6. **Subprocess-inheritance test**: from inside the sandbox, invoke `git -C /tmp/outside-vault-repo status` (a path outside `ReadWritePaths=`) via the same `asyncio.create_subprocess_exec` call the Git Automation Module uses, confirm `git` itself fails to access that path — proving §4's inheritance claim empirically.
7. **Syscall-filter regression test**: after any dependency version bump, re-run the full positive-path suite with `SystemCallErrorNumber=EPERM` active, watching for new `OSError`s from previously-unexercised code paths.

### 7.3 Ownership

These tests belong in ATHENA AI-BRAIN's own test suite, not a manual runbook — Constitution Article 9 requires threat-modeled, security-sensitive functionality to have tests, not just a design document describing what tests should exist.

## References

- systemd.exec(5) — official (returned HTTP 403 to direct fetch 2026-08-27; cross-verified via Debian manpage mirror, which tracks the same upstream source)
- [systemd.exec(5) — Debian manpage mirror](https://manpages.debian.org/unstable/systemd/systemd.exec.5.en.html), checked 2026-08-27
- [systemd-analyze(1), `security` verb](https://www.freedesktop.org/software/systemd/man/latest/systemd-analyze.html), checked 2026-08-27
- [Debian Wiki, "ServiceSandboxing"](https://wiki.debian.org/ServiceSandboxing), checked 2026-08-27
- [Lennart Poettering, "Dynamic Users with systemd"](https://0pointer.net/blog/dynamic-users-with-systemd.html), checked 2026-08-27
- [RestrictAddressFamilies= setting reference](https://linux-audit.com/systemd/settings/units/restrictaddressfamilies/), checked 2026-08-27
- [Debian package tracker, systemd version in Trixie (257)](https://packages.debian.org/trixie/systemd), checked 2026-08-27
- [Bubblewrap (bwrap) project](https://github.com/containers/bubblewrap), checked 2026-08-27
- ["Sandboxing the Claude Code CLI on Linux: a two-layer approach with bubblewrap"](https://labs.esokia.com/post/sandboxing-claude-code-cli-linux-bubblewrap/), checked 2026-08-27
- `docs/SECURITY_MODEL.md` (TB-3, P1 item #9), `docs/ARCHITECTURE.md`, ADR-0001, ADR-0002, ADR-0005, ADR-0006, ADR-0007

## Open Questions Carried Forward

- Exact vault path templating mechanism for `ReadWritePaths=` (an install-time unit-generation step is implied but not designed here).
- Whether `~/.gitconfig` access is actually needed by the Git Automation Module once built (§4) — an empirical Phase 1 check, not a design-time decision.
- Whether `MemoryDenyWriteExecute=yes` survives contact with the actual embedding pipeline (§3.3/§5) — empirical, gated behind §7.1 test #2.
- Whether the systemd unit duplication (§3.4) should be refactored into a template unit during Phase 1 implementation — a code-organization choice, not a security-relevant one.

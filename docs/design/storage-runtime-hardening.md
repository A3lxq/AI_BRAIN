# Design: Storage & Runtime Hardening (Serializer Assertion + File Permissions)

- **Date:** 2026-08-27
- **Author:** Claude Code (ATHENA AI-BRAIN Phase 0)
- **Status:** Design — addresses `docs/SECURITY_MODEL.md` Prioritized Remediation Checklist, P0 items #4 and #5, and threat-model findings TB-7 (SQLite/Huey job store) and TB-8 (Qdrant vector store)
- **Depends on:** ADR-0002 (job queue/serializer decision), ADR-0004 (SQLite access layer, separate Huey file, migration runner), ADR-0006 (Qdrant Docker deployment), `docs/DATA_MODEL.md` (concrete schema and stored content)
- **Not in scope:** implementation code. This document specifies interfaces, mechanisms, and test cases; actual Python/SQL/Docker artifacts are a subsequent implementation task once this design is reviewed.

## Part 1 — P0 #4: Startup Assertion Against Huey's Pickle Default

### 1.1 Purpose & scope

ADR-0002 *decided* to replace Huey's default pickle-based serializer with `SignedSerializer` (HMAC-authenticated) or a JSON-based serializer, specifically to close CWE-502 (deserialization of untrusted data) on TB-7's job-store boundary. `docs/SECURITY_MODEL.md` correctly identifies that this decision currently has **no enforcement mechanism** — it is a convention that a future code change, a config-loading bug, a dependency upgrade that resets a default, or a careless refactor could silently revert. The scope of this item is narrow and deliberately so: one small, auditable guard function that converts "we decided not to use pickle" into "the process will not start if it would use pickle."

This does not replace the need to source the `SignedSerializer` secret correctly (TB-11, out of scope here) — it only guards the *serializer class/configuration*, not the secret's provenance or strength.

### 1.2 Responsibilities

The guard has exactly one job: given a live, already-constructed Huey instance, determine whether its configured serializer is safe, and if not, prevent the process from proceeding to accept or execute jobs.

**What "safe" means, precisely** (grounded in Huey's actual source, not assumed API — see §1.4/References): Huey's `BaseHuey.__init__` stores whatever serializer it's given as a plain instance attribute, `self.serializer`, defaulting to `Serializer()` — the base class — if none is passed. Critically, Huey does **not** have a separate `PickleSerializer` class to check against: the base `Serializer` class's own `_serialize`/`_deserialize` methods call `pickle.dumps`/`pickle.loads` directly and unconditionally. `SignedSerializer` is a *subclass* of `Serializer` that overrides `_serialize`/`_deserialize` to HMAC-SHA1-sign the pickled payload on write and verify the signature *before* calling into the parent's unpickling path on read — so it still uses pickle as its wire format internally, but an attacker who cannot produce a valid HMAC (i.e., doesn't know the secret) cannot get their payload as far as `pickle.loads` at all. This distinction matters for the check design: the unsafe condition is not "any serializer that internally uses pickle," it's specifically **the unauthenticated base `Serializer` class with no integrity check in front of it**.

So the responsibilities are:
1. Read `huey.serializer` (the confirmed public attribute).
2. Reject if it is an instance of the literal base `Serializer` class (not a subclass) — this is Huey's true, unauthenticated-pickle default.
3. Accept only if it is an instance of an explicit allowlist ATHENA AI-BRAIN itself defines in its own config module: `SignedSerializer` (constructed with a real, non-empty secret) or ATHENA AI-BRAIN's own JSON-based `Serializer` subclass, if that path is chosen instead.
4. Reject anything else not on the allowlist (fail closed on unrecognized configuration, not just on the literally-known-bad one) — this is deliberately an allowlist, not a denylist of "just not `Serializer`," because a denylist would silently accept some future third, unvetted serializer class.

### 1.3 Interfaces

A single function, called once per process at bootstrap, before that process does anything that would read or write job payloads:

```python
def assert_safe_job_serializer(huey: BaseHuey) -> None:
    """Raise SerializerMisconfigured if huey.serializer is Huey's
    unauthenticated pickle default, or is not one of ATHENA AI-BRAIN's
    explicitly allow-listed serializer types. No return value on success."""
```

`SerializerMisconfigured` is a small, dedicated exception (not a generic `ValueError`), so it is unambiguous in logs/tracebacks and can't be confused with an unrelated validation error elsewhere in bootstrap.

**Where it's called from — both processes that construct a Huey instance, not just one:**

- **The Huey consumer/worker process**, immediately after `SqliteHuey(...)` is constructed from ATHENA AI-BRAIN's config, and *before* `huey.create_consumer()` is invoked to start pulling and executing jobs. This is the higher-stakes call site: the consumer is the process that actually deserializes job payloads.
- **The MCP server process**, at its own bootstrap, immediately after it constructs (or imports) the same `SqliteHuey` instance used to `enqueue()` jobs. This process only *serializes* (writes) job payloads, but since ATHENA AI-BRAIN's config could drift independently between the two entry points (e.g., an environment variable override present in one process's environment but not the other's), both must check independently rather than trusting that "the consumer checked, so the server is fine too."

Both call sites use the same shared config-construction function, so in practice there is exactly one place the `SqliteHuey(...)` object gets built, and both entry points call `assert_safe_job_serializer()` immediately after obtaining that shared instance — but the assertion itself must not assume it's only ever called once process-wide.

### 1.4 Dependencies

- Huey's public API: `huey.api.BaseHuey` (or `SqliteHuey`, its concrete subclass per ADR-0002) exposing `.serializer` as a plain attribute — confirmed directly against Huey's source (`huey/api.py`, `self.serializer = serializer`, defaulting to `Serializer(...)` when `serializer is None`).
- Huey's `huey.serializer` module: `Serializer` (base class) and `SignedSerializer` (subclass) — confirmed via `huey/serializer.py`.
- Python stdlib only beyond that: a custom exception class, and `sys.exit`/uncaught-exception propagation for the hard-fail (no new third-party dependency).

No new package is introduced by this item — it only requires import access to classes Huey already exposes publicly.

### 1.5 Failure modes

**Primary failure mode (the one this item exists to create): the check fails.** This must be a **hard crash on startup**, not a warning:

- The downside of a false negative here — silently continuing with unauthenticated pickle deserialization — is unbounded: any process able to write to the job-store SQLite file (the exact TB-7 untrusted-write surface) gains arbitrary code execution in the Huey worker process, which per the architecture runs with the full privilege of the user's own account (no OS-level sandboxing exists yet, per the threat model's own P1 item #9). This is categorically worse than "an inconvenient blocked startup."
- The cost of a false positive (blocking startup when the config is actually fine) is low and immediately visible: the process won't start, the error names exactly what's wrong, and the fix is a one-line config change. There is no legitimate operational scenario where ATHENA AI-BRAIN *should* run with the unauthenticated default.
- Fail-open here would also directly contradict CLAUDE.md's non-negotiable rule that important project decisions (ADR-0002) must not exist only as a convention a future change can quietly bypass — the entire point of this item is to make the invariant self-enforcing rather than convention-enforced.

**Secondary failure mode: a bug in the check itself causes a false positive.** Mitigations, by design rather than by process:
- Keep the function pure and small: no I/O, no network calls, no reflection over private/underscored Huey internals — only a public-attribute read and `isinstance`/`type() is` checks against a short, explicit, project-owned allowlist (2–3 classes). A reviewer should be able to read the entire function and its allowlist in under a minute and confirm correctness by inspection.
- Exhaustive unit coverage of every serializer configuration ATHENA AI-BRAIN actually ships (see §1.7) run in CI on every change to this function or to the allowlist.
- Changes to the allowlist itself should require the same review discipline as any other security-relevant change — a one-line diff to a two-class list is easy to scrutinize, which is exactly why the allowlist is kept in ATHENA AI-BRAIN's own config module rather than derived dynamically from Huey's class hierarchy.

### 1.6 Security considerations

**Closes:** TB-7's residual gap ("no enforcement mechanism stops a future code change or misconfiguration from silently reverting to pickle") by converting ADR-0002's decision from a documentation-level convention into a structural invariant checked on every process start, in both processes that touch the job store.

**Residual risk, named honestly:**
- This guard only validates *which serializer class* is configured — it does not validate the *strength or provenance* of the `SignedSerializer` secret. A weak, hardcoded, or leaked secret defeats the HMAC protection just as completely as reverting to the pickle default, and secret provenance is TB-11's scope, not this item's. This design assumes a competent secret exists; it does not create one.
- `SignedSerializer` authenticates payloads; it does **not** encrypt them. Job payloads (which per `DATA_MODEL.md`/`SECURITY_MODEL.md` may carry note content or research queries) remain plaintext-readable to anything with file-level read access to the job-store `.db` file. Confidentiality of job payloads at rest is P0 #5's responsibility, not this one — the two P0 items are complementary controls (integrity vs. confidentiality), and neither substitutes for the other.
- This guard does not protect against compromise of the ATHENA AI-BRAIN process itself. A process that has already been compromised (e.g., via a TB-12 supply-chain attack) has the secret in memory and can forge validly-signed malicious jobs; no serializer-class check can distinguish a legitimate job from one enqueued by an attacker who has already achieved code execution inside a trusted process.

### 1.7 Test strategy

1. **Unsafe-default detection:** construct `SqliteHuey(serializer=Serializer())` (the literal unauthenticated default) and assert `assert_safe_job_serializer` raises `SerializerMisconfigured`.
2. **Implicit-default detection:** construct a Huey instance with no `serializer=` kwarg at all (Huey's own true default) and assert the same failure — the regression test for "someone deletes the `serializer=` line from config."
3. **Accepted configuration — SignedSerializer:** construct with `SignedSerializer(secret="test-secret")` and assert no exception.
4. **Accepted configuration — JSON serializer** (if ATHENA AI-BRAIN implements this alternative per ADR-0002's "or JSON" clause): assert no exception.
5. **Unrecognized-but-not-base-class serializer:** construct with some other `Serializer` subclass not on ATHENA AI-BRAIN's allowlist, assert the guard still raises — proves the check is an allowlist, not merely "not exactly `Serializer`."
6. **Process-level integration test:** launch the actual Huey consumer entry point as a subprocess with config forced to the pickle default; assert the subprocess exits non-zero within a bounded timeout and its stderr/log output names the misconfiguration clearly.
7. **Process-level integration test, MCP server path:** repeat #6 against the MCP server's own bootstrap entry point, confirming both call sites are actually wired up, not just the consumer.
8. **Drift regression test:** import ATHENA AI-BRAIN's real, production Huey-construction function (not a test fixture) and run the guard against its actual output — catches a real accidental reversion in shipped config, distinct from tests #1–5 which only prove the guard function works against synthetic inputs.

## Part 2 — P0 #5: File-Permission Hardening for Metadata SQLite, Huey Job Store, and Qdrant Data Directory

### 2.1 Purpose & scope

Three on-disk artifacts hold plaintext copies of the user's real vault content or job payloads derived from it:

| Artifact | Contains (per `DATA_MODEL.md`) | Owning ADR |
|---|---|---|
| `ai_brain.db` | `chunks.chunk_text` (full chunk bodies, FTS5-indexed), `notes.title`, provenance/lifecycle metadata | ADR-0004 |
| Huey's separate job-store `.db` | Job payloads for `research_start`/`ingestion`/etc. — may embed note content or research queries | ADR-0002 / ADR-0004 |
| Qdrant data directory (Docker bind mount) | Dense+sparse vectors, which TB-8 establishes are practically invertible back to source text given BGE-M3's open weights | ADR-0006 |

None of these has a stated required permission mode today. Scope here is: file mode, directory mode, when/how each gets set, and — specifically for Qdrant — how a host-side permission change interacts with a containerized process, which is not a simple `chmod` given ADR-0006's Docker deployment choice.

### 2.2 Responsibilities — target modes

- **Files (`ai_brain.db`, Huey's job-store `.db`, and their WAL-mode auxiliary files):** `0600` (owner read/write only). SQLite's own default file-creation permission, absent explicit hardening, is `0644` on POSIX — world-readable — which multiple independent sources flag as the wrong default for anything sensitive. **WAL-mode auxiliaries (`-wal`, `-shm`) require the same treatment, not separate handling**: per direct discussion on SQLite's own user forum, these sidecar files are created "with file modes equal to the DB file itself" via SQLite's internal `robust_open()` (in `os_unix.c`) — the forum thread shows some disagreement about whether umask is applied a second time on top of that inherited mode, which is precisely why this design does not rely on that inheritance alone (see §2.3) and instead verifies the resulting mode empirically in tests (§2.7).
- **Containing directory** (wherever ADR-0004 places `ai_brain.db` and the Huey file, e.g. an XDG data directory): `0700` (owner rwx only). A world-readable-but-not-listable or otherwise loosely-permissioned directory can leak filenames and existence even when file *contents* are protected — e.g., the mere presence of `ai_brain.db-wal` or a dated snapshot filename discloses activity/state that should stay private. This directly matches `SECURITY_MODEL.md`'s own TB-3 framing of this exact risk.
- **Qdrant's host-side bind-mount data directory:** `0700`, but — critically — this mode alone does **not** constrain the containerized Qdrant process (see §2.3); it only constrains *other host-side* processes/users. Achieving actual protection against the Qdrant process's own on-disk footprint requires a UID-matching step described below, which is a deployment concern, not something ATHENA AI-BRAIN's Python code can enforce.

### 2.3 Interfaces — how/when permissions get applied

**For the two SQLite files (application-level, at creation time — not a separate deployment step):** ADR-0004 already establishes a single choke point for first-run initialization — the migration runner (`PRAGMA user_version` + numbered `.sql` files). Permission-setting belongs there, because it is the one place ATHENA AI-BRAIN itself creates these files, rather than a separate setup script that could be skipped, forgotten, or drift out of sync with the schema runner.

```python
def ensure_private_file(path: Path, mode: int = 0o600) -> None:
    """Create path if it doesn't exist, with exactly `mode` regardless of
    the process umask. No-op (verify-only) if it already exists."""

def ensure_private_dir(path: Path, mode: int = 0o700) -> None:
    """Same, for the containing directory."""
```

**Two concrete correctness details that matter here, not just "call chmod":**

1. **Ordering relative to the SQLite driver.** `aiosqlite`/`sqlite3`'s own C-level `open()` will happily *reuse* an already-existing file's permissions rather than re-asserting a mode if the file is already present at the target path — but if the file does **not** yet exist, SQLite's own file-creation path applies its own default mode (masked by the process umask), which is commonly `0644`. So `ensure_private_file()` must run and successfully pre-create the empty file with the correct mode **before** the migration runner ever opens its first connection to that path — not after. If the file already exists (e.g., from an OS-default-permissioned earlier run before this hardening was implemented), the helper must additionally verify and correct the mode of the pre-existing file, not just skip creation.
2. **Umask is a mask, never an additive grant** — `os.open(path, ..., mode=0o600)` and `os.makedirs(path, mode=0o700)` are both still ANDed against the process's active umask by the kernel, per POSIX `open(2)`/`mkdir(2)` semantics. Because a request of `0o600`/`0o700` has no group/other bits set to begin with, a typical umask (`022` or stricter) cannot *loosen* them further — umask only removes bits, never adds — so requesting the correct restrictive mode directly is sufficient and does not depend on the ambient umask being configured correctly. The actual historical failure mode this design guards against is the *inverse*: code paths (including SQLite's own driver-level default, or a future refactor) that request a permissive mode like `0o666`, which the umask then only partially restricts (typically down to `0o644`, still world-readable). The fix is therefore to never let any code path request anything other than the explicit restrictive mode — not to tune the umask and hope. As defense-in-depth against a sibling file being created by some other code path without going through this helper (e.g., a WAL/SHM file created by a connection opened before the helper ran), the bootstrap sequence should also bracket its own file-creation calls with an explicit `os.umask(0o077)` / restore, precisely because the forum research above shows some disagreement about whether WAL/SHM inheritance is itself umask-independent — don't stake correctness on an ambiguous behavior when an explicit umask bracket costs nothing.

**Called from:** ADR-0004's migration runner, once per file, before the first connection is opened to `ai_brain.db`, and before ATHENA AI-BRAIN's bootstrap constructs `SqliteHuey(filename=...)` (since Huey's own constructor is what causes its `.db` file to be created if absent — the helper must run first, or Huey must be pointed at a path this helper has already pre-created).

**For the Qdrant data directory — this is necessarily a deployment/setup-documentation item, not application code**, because ATHENA AI-BRAIN's Python process has no reason to run with privileges over an arbitrary Docker container's UID, and because — researched specifically, not assumed — **a host-side `chmod`/`chown` does not straightforwardly constrain what the containerized Qdrant process itself can do:**

- Docker bind mounts preserve host UID/GID numerically; a process inside the container is checked against the host's permission bits using whatever numeric UID it runs as *inside* the container — Docker does not remap container UIDs to different host UIDs unless `--userns-remap` is explicitly configured (not part of ADR-0006's accepted decision).
- Qdrant's standard Docker image has historically run its process as **root (UID 0)** inside the container by default. Because container-root maps directly to host-root under Docker's default (non-remapped) configuration, a host-side `chmod 0700`/`chown <some-user>` on the bind-mounted directory restricts *other host users and processes* from reading it, but does **not** restrict the Qdrant container process itself — root inside the container can read/write the bind mount regardless of the host-side owner or mode bits.
- Qdrant also publishes non-root image variants (a documented `-unprivileged` tag, and Docker Hub's separately-published "hardened images" for Qdrant) that run as a fixed, documented numeric UID — **UID 1000** for the standard non-root convention, **UID 65532** for the hardened-images nonroot convention. Only when ATHENA AI-BRAIN pins one of these variants does a host-side permission change become meaningful: the bind-mount directory must then be `chown`'d to that exact numeric UID:GID *before* the container's first start (a functional requirement — Qdrant will fail to write to a directory it doesn't own, not just a hardening nicety), after which `chmod 0700` on that directory does genuinely restrict every *other* host user/process, since the container's own process is now confirmed to run as that specific unprivileged UID rather than root.

**What the deployment/setup documentation must therefore state**, alongside ADR-0006's existing snapshot-before-upgrade runbook:
1. Pin a specific non-root/unprivileged Qdrant image tag (consistent with ADR-0006's existing "never `:latest`, pin a version" decision) — a prerequisite for host-side permission hardening to mean anything, not an independent nice-to-have.
2. Before the container's first start, `chown <uid>:<gid> <qdrant_data_dir>` on the host to match that image variant's documented process UID, then `chmod 0700 <qdrant_data_dir>`.
3. State explicitly that this step is a one-time manual (or Compose/systemd-scripted) *deployment* action, not something ATHENA AI-BRAIN's own Python bootstrap can or should perform — ATHENA AI-BRAIN's process has no legitimate reason to hold privileges to `chown` an arbitrary directory to an arbitrary container UID, and this ownership decision is properly part of Qdrant's own container lifecycle.
4. State that any snapshot copied out of the Qdrant data directory for backup (per ADR-0006's snapshot-before-upgrade requirement and CLAUDE.md's recoverability rule) must be re-verified/re-hardened (`chmod 0600`) by whichever backup mechanism performs the copy — plain `cp -p`/`rsync -p` preserve mode bits on Linux by default, but this must not be assumed of every tool in a future backup pipeline, so the backup script itself must assert the resulting mode rather than trust inheritance.

### 2.4 Dependencies

- Python stdlib only for the SQLite-file side: `os` (`os.open`, `os.chmod`, `os.makedirs`, `os.umask`, `os.stat`), `pathlib.Path`. No new third-party package.
- For the Qdrant side: no Python dependency at all — this is Docker/Compose configuration and operator action (`chown`, `chmod`, image-tag selection), consistent with ADR-0006's existing framing that container lifecycle/Compose authoring is deployment tooling, not an application-code or ADR-level concern.

### 2.5 Failure modes

Unlike P0 #4, this item should **not** uniformly hard-fail on every permission problem — the two items differ in kind, and the difference should be a deliberate, stated design choice rather than an oversight:

- P0 #4 guards a single, cheap-to-verify, binary condition whose failure mode (unauthenticated pickle deserialization) is RCE-equivalent, with essentially zero legitimate reason the check should ever need to be bypassed, and a one-line fix when it does fail.
- P0 #5 is defense-in-depth against a **Medium**-severity risk (per `SECURITY_MODEL.md`'s own calibration of TB-7's file-permission finding) — "another local process on this single-user machine could read plaintext content it had no legitimate reason to access" — not a catastrophic, RCE-class failure. There are also legitimate environments (unusual filesystems, or a future containerized deployment of ATHENA AI-BRAIN itself with a different runtime UID) where a chmod call could fail for reasons unrelated to a real security regression. Hard-failing ATHENA AI-BRAIN's entire startup on every such environmental hiccup would convert a defense-in-depth control into an availability risk disproportionate to the risk it defends against.

The design therefore uses a **two-tier policy**, distinguishing "our own bootstrap code produced a confirmed-bad result" from "the environment wouldn't let us set the mode at all":

- **Tier A — hard fail:** after the creation-and-chmod sequence completes *without raising an OS error*, a post-creation verification (`path.stat().st_mode`) shows the file or directory is nonetheless group- or world-readable/writable. This means ATHENA AI-BRAIN's own bootstrap logic is wrong (bad mode argument, missed umask bracket, wrong call order relative to the SQLite driver) — exactly the class of "our own code is definitely broken" condition that should not be tolerated silently.
- **Tier B — logged warning, continue startup:** the `os.chmod`/`os.open(mode=...)` call itself raises (`PermissionError`, `OSError`, or a filesystem that doesn't support POSIX permission bits at all). Log at CRITICAL level, name the exact path, the requested mode, the actual resulting state, and the manual remediation (`chmod 600 <path>`), then continue — this is an environmental constraint outside ATHENA AI-BRAIN's control, and for the local-first single-user Kali deployment target this is expected to be a rare edge case, not the primary supported path.

### 2.6 Security considerations

**Closes:** TB-7's "Information Disclosure (file permissions)" finding for both SQLite files, and materially reduces TB-8's embedding-inversion finding's practical exposure by ensuring the Qdrant data directory receives protection consistent with `docs/SECURITY_MODEL.md`'s own stated position that it "should be governed by the same backup-encryption and file-permission posture as the vault itself."

**Residual risk, named honestly:**
- File permissions defend only against *other, unrelated* local processes or users. They do nothing against compromise of the ATHENA AI-BRAIN process, the Huey worker process, or the Qdrant container process itself, each of which needs — and legitimately has — read/write access to its own data by design. A supply-chain compromise (TB-12) inheriting the running process's own file descriptors bypasses OS permissions entirely; this is the same honest limitation named for P0 #4's residual risk.
- Permission hardening does not address the *substance* of TB-8's embedding-inversion finding — an attacker who legitimately obtains a copy of the Qdrant data directory (e.g., an improperly-secured backup, rather than a live-filesystem read) can still invert vectors back toward source text using BGE-M3's open weights. Hardening raises the bar on *how* that copy could be obtained; it does not change what's possible once it is. Backup encryption — named in TB-8 but explicitly out of this item's scope — is the complementary, not-yet-designed control for that residual.

### 2.7 Test strategy

1. **Fresh-creation mode assertion:** run the migration runner against a clean temp directory; assert `ai_brain.db`'s resulting `stat().st_mode & 0o777 == 0o600`, not the OS/umask default (`0o644`).
2. **Same assertion for Huey's job-store `.db` file.**
3. **WAL/SHM inheritance verification (don't trust the forum thread, verify empirically):** open a WAL-mode connection, write a row so `-wal`/`-shm` files are actually created, and assert both auxiliary files also carry `0o600` — this test exists specifically because the SQLite forum research surfaced some disagreement about umask interaction with WAL/SHM creation, so ATHENA AI-BRAIN verifies its own CI environment's actual behavior rather than assuming the cited claim.
4. **Directory mode assertion:** assert the containing data directory has `0o700`.
5. **Umask-independence regression test:** run the creation helper under an artificially permissive process umask (`os.umask(0o000)`) and assert the resulting file mode is *still* `0o600` — catches the "relying on umask alone is insufficient" failure mode.
6. **Idempotency test:** run the migration runner twice (simulating an app restart against an already-initialized data directory) and assert the second run does not loosen or reset permissions on the already-existing file.
7. **Tier A failure-mode test:** monkeypatch the helper to pass an incorrect mode internally (simulating a bootstrap bug), assert the post-creation stat check detects the bad state and the process raises/exits rather than continuing.
8. **Tier B failure-mode test:** monkeypatch `os.chmod`/`os.open` to raise `PermissionError`, assert ATHENA AI-BRAIN logs a CRITICAL-level message naming the exact path and remediation, and that startup continues rather than crashing.
9. **Qdrant directory — deployment-runbook check (not unit-testable across the Docker boundary):** a documented smoke-test step for the deployment runbook — after `docker compose up`, from the host run `stat -c '%a %U' <qdrant_data_dir>` and confirm the mode is `700` and the owning UID matches the pinned image's documented process UID; optionally an integration check using `docker exec qdrant id -u` to confirm the actual UID Qdrant is running as inside the container matches the host directory's owner, catching the exact root-vs-unprivileged-image mismatch this design's research surfaced.

## Cross-cutting notes

- **ADR follow-through (CLAUDE.md rule 7):** once this design is reviewed and implemented, the two mechanisms should be recorded either as amendments to ADR-0002/ADR-0004 (P0 #4) and ADR-0004/ADR-0006 (P0 #5), or as a new small ADR ("Startup Security Invariants and Data-at-Rest Permission Policy") — this document is the design input for that ADR, not a substitute for it.
- **Ordering dependency between the two items:** P0 #5's file-permission hardening and P0 #4's serializer guard are complementary, not sequential — P0 #4 protects payload *integrity* in transit through deserialization; P0 #5 protects payload and content *confidentiality* at rest. Implementing one does not reduce the priority of the other, and both should land before, per the checklist's own framing, "any real vault is pointed at ATHENA AI-BRAIN."

## References

- Huey source (confirmed 2026-08-27): [`huey/api.py`](https://github.com/coleifer/huey/blob/master/huey/api.py) — `BaseHuey.__init__`, `self.serializer` attribute and default `Serializer(...)` construction; [`huey/serializer.py`](https://github.com/coleifer/huey/blob/master/huey/serializer.py) — `Serializer` base class (`pickle.dumps`/`pickle.loads`), `SignedSerializer` subclass (HMAC-SHA1 sign/verify wrapping the same pickle format).
- [Huey's API documentation](https://huey.readthedocs.io/en/latest/api.html) (checked 2026-08-27).
- SQLite User Forum: ["Setting Permissions for SQLite WAL and SHM Files in shared Docker Compose volumes"](https://sqlite.org/forum/info/87824f1ed837cdbb) (checked 2026-08-27) — WAL/SHM files created with modes derived from the main DB file via `robust_open()` in `os_unix.c`; some forum disagreement on exact umask interaction, hence this design's empirical-verification test (§2.7 item 3).
- ["Best practices for securing SQLite"](https://blackhawk.sh/en/blog/best-practices-for-securing-sqlite/) (checked 2026-08-27) — `0600` file mode recommendation, WAL/SHM sidecar treatment.
- Qdrant Docker deployment research (checked 2026-08-27): [Qdrant Docker Hub image](https://hub.docker.com/r/qdrant/qdrant); [Qdrant hardened-images guide](https://hub.docker.com/hardened-images/catalog/dhi/qdrant/guides) (nonroot UID 65532 convention); general Docker bind-mount UID/GID-preservation behavior (no UID remapping without `--userns-remap`).
- `docs/SECURITY_MODEL.md` TB-7, TB-8, and Prioritized Remediation Checklist items #4–#5.
- ADR-0002, ADR-0004, ADR-0006, `docs/DATA_MODEL.md`.

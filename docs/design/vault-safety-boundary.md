# Design: Vault Safety Boundary (Path & Content Safety)

- **Date:** 2026-08-27
- **Author:** Claude Code (AI_BRAIN Phase 0)
- **Status:** Design — addresses `docs/SECURITY_MODEL.md` TB-3, P0 items #1 and #3
- **Depends on / informs:** ADR-0004 (SQLite access layer), ADR-0005 (Git automation library), ADR-0007 (MCP tool contract), ADR-0009 (filesystem event architecture), `docs/DATA_MODEL.md` §0

## 1. Purpose & Scope

The **Vault Safety Boundary** is a single, shared, dependency-injected module that answers exactly two questions, and nothing else:

1. *"Is this filesystem path safe to touch, given the operation the caller intends?"* — the **path safety** concern.
2. *"Can this note's raw text be parsed into metadata + body without ever executing untrusted content or crashing on adversarial/malformed input?"* — the **content safety** concern.

It lives as its own package (`ai_brain/safety/`, submodules `paths.py` and `content.py`), architecturally beneath the business-logic layer (`ARCHITECTURE.md` layer 2) and importable by every layer that touches vault paths or vault text: the MCP tool wrappers, the internal business-logic functions they call, the Git Automation Module (ADR-0005), the Vault Watcher/Event Debouncer (ADR-0009), and the reconciliation job.

**Explicitly NOT this module's job** (so scope doesn't creep):

- **Business/authorization logic.** It does not decide *whether* a note should be created, moved, or deleted, whether a user is allowed to call a tool, or whether a destructive operation needs MRTR confirmation (ADR-0007's concern). It only decides whether the *path itself* is safe to operate on at all — a `note_delete` call that passes this module's check can still be rejected two layers up because the confirmation echo didn't match.
- **Argument-injection defense for `git` argv** (allow-listing branch names, the `--`/`--end-of-options` separator). That's ADR-0005's Git module's own job. This module only tells the Git module "this pathspec, once resolved, stays inside the vault" — it doesn't know or care about git's argv-injection surface.
- **Secret scanning, prompt-injection detection, or business-level YAML *schema* validation** (e.g., "does this frontmatter have a valid `tags` field"). Those are separate, later-stage concerns (the pre-ingestion secret scanner design and whatever schema validation `note_create`/`note_update` do).
- **OS-level sandboxing.** `docs/SECURITY_MODEL.md` P1 item 9 (systemd hardening) is an explicit, separate backstop for the case where *this module itself* has a bug. This design does not substitute for it.

## 2. Responsibilities

Concretely, the module performs these checks, and only these:

**Path safety (`ai_brain.safety.paths`):**
- Canonicalizes the configured vault root exactly once, at process startup, to an absolute, fully-resolved `Path`.
- For every incoming path-shaped input from any entry point, resolves it and verifies the vault root is an ancestor of the result (never a string-prefix comparison).
- Detects and rejects any path traversal (`../`) that would escape the vault root.
- Detects and rejects any symlink — whether it resolves outside the vault root or to another location *inside* the vault root (see §5 for the reasoning).
- Handles the existence/non-existence semantics correctly per operation type (CREATE vs. READ/UPDATE/DELETE — see §5).
- Rejects structurally invalid path input (embedded NUL bytes, empty strings, the vault root itself as a target).

**Content safety (`ai_brain.safety.content`):**
- Never calls `yaml.load()` unguarded, directly or indirectly, on vault content.
- Bounds the size of any YAML frontmatter block *before* it reaches a YAML parser at all (a gap `python-frontmatter` itself does not close — see §4).
- Classifies incoming note text into one of three shapes per `DATA_MODEL.md` §0 — real frontmatter, legacy chat-export, or plain/reference material — and parses each safely without assuming any of the other two shapes.
- Bounds and sanitizes the legacy `> From: <url>` extraction against oversized or control-character-laden input.
- Never raises an unhandled exception for any of the three shapes, or for malformed input within any shape — degrades to a documented, typed error/flag instead.

## 3. Interfaces

### 3.1 Path safety

```
class VaultRoot:
    """Canonicalized once at process startup. Immutable. Passed by dependency
    injection into every consumer — never re-read from global config mid-run."""

    @classmethod
    def initialize(cls, configured_path: str | Path) -> "VaultRoot":
        """
        Path(configured_path).resolve(strict=True).
        Raises VaultRootConfigError if the configured vault path does not exist,
        is not a directory, or is itself a symlink (the root must be a real
        directory — a symlinked vault root is refused for the same reason
        in-vault symlinks are refused, see §5).
        """

class PathMode(Enum):
    EXISTING = "existing"              # note_read, note_update, note_delete, move-source
    CREATE = "create"                  # note_create, move-destination when absent
    MAYBE_EXISTING = "maybe_existing"  # git pathspecs, reconciliation enumeration,
                                        # move-destination in the general case

class SafeVaultPath:
    """
    Opaque wrapper around a verified Path. Constructible ONLY by this module
    (module-private constructor / frozen dataclass with a factory function) —
    no other code can mint one, which is the structural half of "one shared
    implementation" (see §3.3).
    """

def resolve_vault_path(
    raw_path: str | os.PathLike,
    vault_root: VaultRoot,
    mode: PathMode,
) -> SafeVaultPath:
    """
    Raises:
      PathEscapesVaultError   — resolved path is not a descendant of vault_root
                                (covers `../` traversal AND symlink-escape
                                uniformly, since resolution happens before
                                the ancestor check)
      SymlinkNotAllowedError  — a symlink was encountered anywhere along the
                                path, whether it escapes the vault or not
      PathNotFoundError       — mode=EXISTING and the target does not exist
      InvalidPathError        — embedded NUL, empty string, or path equal to
                                vault_root itself
    """

def resolve_vault_pathspec(raw_pathspec: str, vault_root: VaultRoot) -> str:
    """
    Thin wrapper for the Git Automation Module (ADR-0005): calls
    resolve_vault_path(..., mode=MAYBE_EXISTING) and returns the vault-relative
    POSIX-style string form ADR-0005's argv builder expects — never the
    resolved absolute path. Raises the same exceptions as above.
    """
```

### 3.2 Content safety

```
class NoteShape(Enum):
    FRONTMATTER = "frontmatter"              # real YAML frontmatter present
    LEGACY_CHAT_EXPORT = "legacy_chat_export" # "> From:" line and/or turn headers
    PLAIN = "plain"                            # neither — reference material

@dataclass(frozen=True)
class ParsedNote:
    metadata: dict[str, Any]     # {} unless shape == FRONTMATTER
    body: str
    shape: NoteShape
    source_url: str | None       # sanitized, length-capped; None if absent/unrecognized
    provider_hint: str | None    # from the folder-name mapping table; never from content
    parse_warning: str | None    # set when a legacy field looked malformed but was
                                  # tolerated (never blocks ingestion)

def parse_note_safely(
    raw_text: str,
    *,
    folder_name: str,
    max_frontmatter_bytes: int = 8192,
    max_source_url_len: int = 2048,
) -> ParsedNote:
    """
    Never calls yaml.load directly. Delegates real-frontmatter parsing to
    python-frontmatter, whose default loader is yaml.SafeLoader (verified —
    see §4). Pre-checks the raw frontmatter block's byte length BEFORE handing
    it to python-frontmatter at all (python-frontmatter itself applies no such
    bound — see §4) and raises FrontmatterTooLargeError rather than parsing an
    oversized block.

    Raises (both caught by the ingestion job and treated as "index as plain
    body, flag for review" — never crashes a reindex run):
      FrontmatterTooLargeError
      FrontmatterParseError   — wraps yaml.YAMLError / python-frontmatter's own errors

    Never raises for LEGACY_CHAT_EXPORT or PLAIN shapes — malformed legacy
    fields degrade to None + parse_warning, not an exception.
    """
```

### 3.3 Enforcing "one shared implementation" structurally

A design doc that only states the interface and trusts every future call site to use it correctly will drift. Four concrete mechanisms, in decreasing order of strength:

1. **Type-level friction.** `SafeVaultPath` is constructible only inside `ai_brain/safety/paths.py` (a private constructor, not a public `NewType`/dataclass anyone can instantiate). Every business-logic function that touches the filesystem declares its path parameter as `SafeVaultPath`, not `str`/`Path` — a caller that skipped `resolve_vault_path()` gets a type error, not a runtime surprise, wherever mypy/pyright actually runs.
2. **Structural CI check (grep/AST-based)**, mirroring the precedent `docs/TESTING_STRATEGY.md` already sets for `shell=True` and the pickle-serializer check: fail the build if any module outside `ai_brain/safety/` calls `Path.resolve(`, `os.path.realpath(`, or opens/writes a file via a raw path that didn't originate from a `SafeVaultPath`. This is the same "grep-based CI check" pattern already accepted for ADR-0005's argument-injection defenses — no new tooling class is introduced.
3. **An enumeration tripwire test.** A test walks the MCP tool registry (ADR-0007) and the Git module's exported function list and asserts every function whose signature accepts a path-shaped parameter appears in an explicit allow-list file that documents which `PathMode` it uses and where it calls `resolve_vault_path`. Adding a new path-accepting tool without updating that file fails CI — this is a deliberate "you must touch this file" trip-wire, not a one-time check.
4. **Code-review checklist item** (documented here, referenced from `CLAUDE.md`/`ARCHITECTURE.md`): *"Any new function accepting a path parameter representing vault content must accept `SafeVaultPath`, not `str`/`Path`, and must not call `Path.resolve()`/`os.path.realpath()` itself."*

None of these four is airtight alone; together they make reimplementation a visible, reviewed decision rather than a silent one.

## 4. Dependencies

- **`pathlib` (stdlib).** `Path.resolve(strict=True)`, `.parents`, `.is_symlink()`, `Path.lstat()` for the pre-creation leaf check.
- **`os` (stdlib).** `os.open(path, O_CREAT | O_EXCL | O_NOFOLLOW, …)` as the final-mile TOCTOU defense for CREATE-mode operations (see §5); `os.walk(vault_root, followlinks=False)` (or `Path.iterdir()` + explicit `is_symlink()` filtering) for the watcher's exclusion list and the reconciliation job's directory walk.
- **`python-frontmatter`** (PyPI, `eyeseast/python-frontmatter`). **Verified directly against upstream source, not assumed:**
  - `frontmatter/default_handlers.py` (`main` branch) shows `YAMLHandler.load()` calling `yaml.load(fm, **kwargs)` with `kwargs.setdefault("Loader", SafeLoader)`, where `SafeLoader` is `CSafeLoader` if available, else PyYAML's `SafeLoader`. `YAMLHandler.export()` correspondingly defaults `Dumper` to `SafeDumper`/`CSafeDumper`.
  - Both methods accept `**kwargs`, meaning a caller *can* override `Loader=yaml.Loader` (the unsafe loader) — this must never happen in AI_BRAIN code; enforced by the same grep-based CI check described in §3.3, extended to flag any `Loader=` kwarg passed into `frontmatter.load`/`frontmatter.loads` that isn't `SafeLoader`/`CSafeLoader`.
  - When no frontmatter delimiter is present, `frontmatter.loads()` returns a `Post` with empty metadata and the entire input as body — it does not raise. This is the mechanism `parse_note_safely` relies on for shapes (b)/(c) to fall through cleanly.
  - **Confirmed gap:** no size guard exists anywhere in this code path between splitting out the frontmatter block and handing it to `yaml.load`. This is why `parse_note_safely` adds its own `max_frontmatter_bytes` pre-check — it is an *additive* control, not a replacement for python-frontmatter, since no unsafe-loading problem was found.
  - **Sources:** [`frontmatter/default_handlers.py`](https://github.com/eyeseast/python-frontmatter/blob/main/frontmatter/default_handlers.py) and [`frontmatter/__init__.py`](https://raw.githubusercontent.com/eyeseast/python-frontmatter/main/frontmatter/__init__.py) on the `main` branch — **checked 2026-08-27**. Given how fast-moving supply-chain compromises have been in 2026 per `SECURITY_MODEL.md`'s own findings, this verification should be re-run (or added as a standing regression test, §7) on every version bump of `python-frontmatter`/`PyYAML`, not treated as a one-time fact.
- **`PyYAML`** (transitive via python-frontmatter). AI_BRAIN code never calls it directly. If a future feature needs to parse YAML outside python-frontmatter, it must use `yaml.safe_load` exclusively — already `docs/TESTING_STRATEGY.md`'s stated grep-based CI check, cross-referenced here rather than duplicated.
- **`re` (stdlib)**, for the legacy `> From: <url>` extraction — a bounded, non-backtracking-prone pattern applied only after length-truncating the candidate line, which neutralizes ReDoS risk independent of pattern complexity.
- **Alternative considered and rejected:** replacing `python-frontmatter` with a hand-rolled YAML-frontmatter splitter. Rejected — the verification above found no unsafe-loading defect in the current library; only an *additive* size-cap wrapper is warranted, not a replacement of an already-accepted ADR-0003 dependency.

## 5. Failure Modes

| Scenario | Mechanism | Result |
|---|---|---|
| Path escapes vault root (`../../etc/passwd`, absolute `/etc/passwd`, sibling-prefix `/vault-backup/...`) | `resolve(strict=True)` computes the true target; `vault_root in resolved.parents` is `False` | `PathEscapesVaultError` — reject, log at WARN with the raw offending input (feeds `SECURITY_MODEL.md`'s repudiation gap, P2 item 19), never silently clamp to the vault root (see rationale below) |
| Symlink escaping the vault root | Same mechanism — `resolve(strict=True)` follows the symlink chain fully before the ancestor check runs, so escape-via-symlink and escape-via-`../` are caught by the identical code path | `PathEscapesVaultError` (optionally surfaced as a more specific `SymlinkEscapesVaultError` subtype for diagnostics) |
| Symlink *within* the vault, pointing to another in-vault location | Deliberately rejected, not permitted — see rationale below | `SymlinkNotAllowedError` |
| Non-existent path, **mode=EXISTING** (`note_read`/`update`/`delete`, move-source) | `Path.resolve(strict=True)` raises `FileNotFoundError` | Wrapped as `PathNotFoundError` — an ordinary business-logic "not found," not necessarily a security event on its own |
| Non-existent path, **mode=CREATE** (`note_create`, move-destination when absent) | See the CREATE-mode design below — this is the real wrinkle `strict=True` cannot resolve on its own | See below |
| Non-existent path, **mode=MAYBE_EXISTING** (git pathspecs referencing a deletion, reconciliation noticing a vanished path) | Try strict resolve; on `FileNotFoundError`, fall back to the CREATE-mode ancestor-plus-lexical-validation logic | Succeeds if lexically safe, without requiring the target to exist |
| Malformed YAML frontmatter | `SafeLoader` still raises `yaml.YAMLError` on genuinely broken syntax (safe ≠ error-free) | Caught, wrapped as `FrontmatterParseError`; the note is indexed with `metadata={}`, `body=raw_text`, flagged for review — one bad note must never abort a reindex job (consistent with ADR-0009's idempotent-job philosophy) |
| Oversized frontmatter block | `parse_note_safely`'s own byte-length pre-check, run *before* python-frontmatter/PyYAML ever sees it | `FrontmatterTooLargeError` — same graceful-degrade handling as malformed YAML |
| Oversized or control-character-laden `> From: <url>` line | Line is length-truncated *before* any regex runs (neutralizes ReDoS regardless of pattern); captured group has control characters (`\x00`–`\x1f`, ANSI escapes, `\r`) stripped and is length-capped again | Never raises — degrades to `source_url=None` if unrecognizable after sanitization, with `parse_warning` set so provenance-completeness auditing can distinguish "no URL was ever present" from "we couldn't parse one" |

**Reject vs. silently clamp.** The threat model specifies reject; this design agrees for two independent reasons. First, clamping (e.g., silently rewriting an escaping path to something inside the vault) means the caller's stated intent and the actual operation diverge — for a mutating tool like `note_move`, silently redirecting a write is arguably *worse* than refusing it, since it can put content somewhere the caller never asked for and never notices. Second, the master specification's decoupling requirement (§4, ADR-0007) means this validator is reachable from contexts with no human in the loop (Huey jobs, the reconciliation job) — clamping in those contexts produces silent, hard-to-audit index drift, exactly the failure mode ADR-0009's own reconciliation design exists to prevent.

**The CREATE-mode wrinkle, resolved.** `Path.resolve(strict=True)` cannot be used on a path that legitimately doesn't exist yet — `note_create`'s entire point is to write to a path that isn't there. The design splits the check into two phases:

1. **Validation phase (this module).** Walk upward from the target until an existing ancestor directory is found; resolve *that* ancestor with `strict=True` and ancestor-check it against `vault_root`. Then lexically validate the remaining, not-yet-existing path components: reject any `..` segment, reject empty segments, and reject the exact leaf name if `os.path.lstat()` shows something already occupies it as a symlink (a symlink squatting on the target name before creation — a legitimate TOCTOU-relevant check even though the *file* doesn't exist, the symlink itself might). Return a `SafeVaultPath` built from the verified ancestor plus the validated remainder — this path is **not** filesystem-resolved, since it can't be.
2. **Creation phase (the caller, e.g. `note_create`'s actual write step).** The file must be opened with `O_CREAT | O_EXCL | O_NOFOLLOW` (or the platform equivalent). This is the actual TOCTOU closure: if an attacker plants a symlink at the exact target path in the gap between phase 1's validation and phase 2's write, `O_EXCL` makes the open atomically fail rather than silently following or overwriting whatever now sits there. This obligation is *specified* by this design but enforced by the calling code, not by the validator's return type alone — flagged explicitly in §6 as a residual risk requiring a review-checklist item, not a fully closed guarantee from this module in isolation.

**Symlinks within the vault — reasoned recommendation: refuse, don't follow.** Two distinct in-vault symlink scenarios exist: a symlink escaping the vault (already covered above) and a symlink whose target resolves to a *different but still in-vault* location. The second case passes a naive ancestor check (the resolved path is inside `vault_root`) but should still be rejected, for reasons specific to this system's own data model rather than abstract caution:

- `notes.path` is the canonical, `UNIQUE` identifier in `ai_brain.db` (`DATA_MODEL.md` §2.2). If a symlink lets two distinct vault-relative paths resolve to the same underlying content, the same file could be indexed twice under two different `notes.path` values — directly undermining the duplicate-detection subsystem's own assumptions and doubling embedding cost for no benefit.
- The Git Automation Module (ADR-0005) operates on the *literal* pathspec it's given, not the symlink-resolved target. If the indexer silently follows a symlink to its real target while Git commits the symlink path, SQLite/Qdrant's notion of "what changed" and Git's commit history diverge — a provenance-integrity break that directly violates CLAUDE.md rule 24.
- `DATA_MODEL.md` §0's folder-name-based provenance inference (`CHAT_GPT` → `openai`, etc.) assumes a note's folder location *is* its provenance signal. A symlink from one AI-origin folder into another (or from a private note into an AI-origin folder) would silently misclassify that note's `origin`/`provider` fields with no content-level indication anything unusual happened.
- Both the watcher's `recursive=True` observer and the reconciliation job's directory walk risk infinite loops on a symlink cycle if links are followed — a concrete, self-inflicted DoS vector, not merely a security abstraction.

Given these concrete, this-project-specific reasons (not just general "symlinks are scary" caution — current best practice for vault/backup/static-site tools generally does default to not following symlinks unless explicitly opted in, which corroborates but doesn't solely justify this choice), the recommendation is: **any symlink encountered anywhere in a resolved path — escaping or not — is a hard rejection**, surfaced as `SymlinkNotAllowedError`, distinct from `PathEscapesVaultError` so logs and tests can tell "an attacker is trying to climb out" apart from "someone placed an in-vault symlink we refuse to follow." The watcher and reconciliation job additionally use `followlinks=False` in their directory traversal so they never descend into a symlinked subtree at all, closing the cycle-DoS vector structurally rather than relying on the per-file check alone.

## 6. Security Considerations

**What this closes.** TB-3 P0 #1 is closed structurally, not just procedurally: every one of the four real entry-point classes (MCP tool handlers for `note_create`/`update`/`move`/`read`/`delete`; the Git Automation Module's pathspec arguments; the Vault Watcher/Event Debouncer's own path normalization; the reconciliation job's path enumeration) is required, by the type-level and CI-level mechanisms in §3.3, to route through one implementation rather than reimplement the check. TB-3 P0 #3 is closed by direct verification against upstream source (§4) rather than assumption, plus one additive control (the frontmatter size bound) for the one gap that verification actually found.

**Residual risk — stated honestly, not oversold:**

- **No OS-level backstop.** This is application-level Python logic. `docs/SECURITY_MODEL.md`'s own P1 item 9 (systemd `DynamicUser`/`ProtectHome`/`ReadWritePaths` hardening) is explicitly a separate, not-yet-committed mitigation for "this logic has a bug anyway" — this design does not substitute for it, and a defect here still runs with the full privilege of the user's login until that hardening lands.
- **CREATE-mode TOCTOU is narrowed, not fully closed by this module alone.** The validator can guarantee the path was safe *at validation time*; true closure depends on the downstream `open()` call correctly using `O_EXCL | O_NOFOLLOW`, which is a specified obligation on the caller, not something this module's return type can force. This is flagged as a required code-review checklist item, not a solved problem.
- **The frontmatter size cap is a heuristic constant**, not a hard guarantee against all parse-cost DoS. `SafeLoader` still permits ordinary YAML anchor/alias reuse within the size bound, which can produce some multiplicative expansion short of the size limit — bounded, not eliminated.
- **Unicode/normalization edge cases** (e.g. NFC/NFD differences between a resolved symlink target and the canonicalized vault root) are not exhaustively proven safe here — flagged as needing dedicated test coverage (§7), not claimed as closed.
- **This module says nothing about authorization or confirmation.** A path can pass every check here and still need to be rejected two layers up for lack of MRTR confirmation (TB-2/ADR-0007's concern) — conflating the two would be scope creep this design deliberately avoids, but a reader should not assume "path safety passed" means "operation authorized."
- **Legacy metadata extraction (`source_url`) is best-effort, not a security gate.** A malformed `From:` line degrades to `None` with a flag rather than blocking ingestion — appropriate for data-quality reasons, but it means adversarial legacy content that merely *looks* garbled (rather than being a real attack) is tolerated by design, and any future consumer that renders `source_url` in an HTML/terminal context is responsible for its own output-encoding; this module does not certify the value cross-context-safe, only bounded-length and control-character-free.

## 7. Test Strategy

This extends, rather than duplicates, `docs/TESTING_STRATEGY.md`'s existing "shared, parametrized fixture of malicious path inputs" concept, and its grep-based structural-check precedent (already used for `shell=True` and the pickle-serializer check).

**Path fixtures — run against every entry point** (`note_create`, `note_read`, `note_update`, `note_move` for both source and destination arguments, `note_delete`, the Git module's pathspec arguments, the watcher's path normalization, reconciliation enumeration):

- `../../etc/passwd`, `../../../root/.ssh/id_rsa`, deeply nested `../` chains
- absolute paths outside the vault: `/etc/passwd`, `/home/user/.ssh/id_rsa`
- sibling-directory prefix confusion: `vault_root=/vault` vs. a resolved target under `/vault-backup/...` — explicitly proves the ancestor check over a string-prefix check
- embedded NUL byte in the path string
- a symlink inside the vault pointing outside it
- a symlink inside the vault pointing to another in-vault location — assert rejection per §5's policy
- a symlink cycle inside the vault — assert the watcher and reconciliation walk terminate without hanging or crashing
- the vault root path itself as a target — assert rejection
- Windows-style separators/drive letters on the Linux deployment — assert treated as literal filename characters, not traversal
- Unicode normalization/homoglyph tricks attempting to spoof `..` or a safe-looking segment
- CREATE-mode: a target whose immediate parent directory doesn't exist yet — assert a clear, distinct error, not a false-positive traversal rejection
- CREATE-mode TOCTOU (integration-level, not unit-level): plant a symlink at the target path *after* `resolve_vault_path` validation succeeds but *before* the actual creation call — assert the `O_EXCL|O_NOFOLLOW` write fails rather than following it
- git pathspec fixtures: a pathspec beginning with `-` (defense-in-depth alongside ADR-0005's own allow-list); a pathspec for a file already deleted from the working tree (MAYBE_EXISTING mode must succeed lexically without requiring existence)
- reconciliation enumeration against a fixture vault containing one symlink among many real files — assert exactly that entry is skipped and nothing else is affected

**Content/frontmatter fixtures:**

- valid YAML frontmatter — happy path
- frontmatter containing a `!!python/object`-style tag or other construct only the unsafe loader would execute — assert `SafeLoader` refuses it inertly; recommend this become a **standing regression test re-run on every `python-frontmatter`/`PyYAML` version bump**, not a one-time design-time check, consistent with `SECURITY_MODEL.md`'s supply-chain discipline
- frontmatter at/near `max_frontmatter_bytes` with YAML anchor/alias reuse — coarse bounded-time assertion
- frontmatter exceeding `max_frontmatter_bytes` — assert `FrontmatterTooLargeError`, note still ingested as plain body, pipeline does not abort
- malformed/truncated YAML — assert `FrontmatterParseError`, graceful degrade
- the three real `DATA_MODEL.md` §0 shapes as literal fixtures: a `CHAT_GPT/`-style note (`> From: <url>` + `# you asked`/`# chatgpt response`), a `QWEN/`-style note (`### USER`/`### ASSISTANT`, no `From:` line), and an `OWASP-...`-style Setext-header plain reference note — assert each yields the correct `shape`/`provider_hint`/`source_url` and never raises
- a `> From:` line 10MB long with no newline — assert bounded-time handling via pre-truncation, no ReDoS-scale hang
- a `> From: <url>` line containing NUL bytes, ANSI escape sequences, and `\r` (log-injection attempt) — assert sanitized before storage and before any logging of the value
- an AI-origin folder name not present in the known mapping table — assert graceful `provider_hint=None`, never a `KeyError`
- empty file, whitespace-only file, single-word file — assert `PLAIN` shape, no crash

**Structural/CI checks** (extending the precedent already set for `shell=True`/pickle-serializer checks):

- fail CI if any module outside `ai_brain/safety/` calls `Path.resolve(` or `os.path.realpath(` directly
- fail CI if any `frontmatter.load`/`frontmatter.loads` call site passes a `Loader=` kwarg other than `SafeLoader`/`CSafeLoader`
- fail CI if any `yaml.load(` call anywhere lacks `Loader=yaml.SafeLoader` (cross-referencing `docs/TESTING_STRATEGY.md`'s existing stated check, not duplicating it)
- the enumeration tripwire test described in §3.3: every path-accepting MCP tool and Git-module function must appear in the allow-list file documenting its `PathMode` and call site — a new path-accepting entry point that skips this file fails CI

## Sources Cited

- [`python-frontmatter/frontmatter/default_handlers.py`](https://github.com/eyeseast/python-frontmatter/blob/main/frontmatter/default_handlers.py) (GitHub, `main` branch) — checked 2026-08-27
- [`python-frontmatter/frontmatter/__init__.py`](https://raw.githubusercontent.com/eyeseast/python-frontmatter/main/frontmatter/__init__.py) (GitHub, `main` branch) — checked 2026-08-27
- `docs/SECURITY_MODEL.md` TB-3, P0 items #1 and #3
- `docs/DATA_MODEL.md` §0
- `docs/adr/0005-git-automation-library.md`, `docs/adr/0007-mcp-tool-contract.md`, `docs/adr/0009-filesystem-event-architecture.md`
- `docs/TESTING_STRATEGY.md` — existing path-traversal fixture-sweep and structural-CI-check precedents, extended rather than duplicated above

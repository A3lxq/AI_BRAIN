# Design: Pre-Ingestion Secret Scanner for Vault Content

- **Date:** 2026-08-27
- **Author:** Claude Code (AI_BRAIN Phase 0)
- **Status:** Design — addresses `docs/SECURITY_MODEL.md` P0 remediation item #6 ("Design and implement the pre-ingestion secret scanner... pre-commit gitleaks alone fires too late")
- **Depends on (accepted):** ADR-0003 (RAG orchestration / indexing pipeline), ADR-0005 (Git automation — pre-commit gitleaks, *not* reused here), ADR-0009 (filesystem event architecture)
- **Requires before implementation:** a new ADR for the schema addition proposed in §6/§9 (a `notes.secret_scan_status` column + a `note_secret_findings` table), analogous to how `docs/EVENT_MODEL.md` flagged its own new `events` table as needing ADR-0010. This document is written to serve as that future ADR's Context/Decision source material.
- **Research cutoff:** all tool-capability claims below were verified against current official documentation and primary repositories on 2026-08-27 (cited inline; see §7 for the full source list).

## 1. Purpose & Scope

**This is not ADR-0005's problem, restated.** ADR-0005 accepted `gitleaks` run through the standard `pre-commit` framework, installed as a Git **pre-commit hook** on **AI_BRAIN's own software repository** — it fires when a developer commits changes to AI_BRAIN's Python source, configs, and docs. It has never seen, and structurally cannot see, a single byte of vault content: per CLAUDE.md rule 13 ("AI_BRAIN must remain separate from the Obsidian vault"), the vault is a distinct filesystem tree (and, per ADR-0005's own scope, the target of a *separate* Git automation workflow) from the AI_BRAIN codebase repo the pre-commit hook is installed against.

**This document's problem is different in kind, not just in timing.** AI_BRAIN's ingestion/indexing pipeline (ADR-0003, `docs/EVENT_MODEL.md` §3) reads real vault Markdown files — per `docs/DATA_MODEL.md` §0, this is concretely: ChatGPT/Claude/Grok/Qwen chat-export text (which may contain pasted code snippets, including credential-shaped strings the user was debugging with an AI assistant) and OWASP-style cybersecurity training reference material (which *legitimately, intentionally* contains example API keys, example passwords, example PEM headers, and CVE identifiers as **teaching content**, not real secrets) — and writes derived copies of that text into two durable stores: SQLite (`chunks.chunk_text`, full text, indexed by FTS5 for keyword search) and Qdrant (dense+sparse vectors that, per the threat model's TB-8 finding, are an *invertible* copy of the source text given BGE-M3 is an open-weight model). Once content lands in either store, it becomes searchable (FTS5) and reconstructable (Qdrant inversion), which `docs/DATA_MODEL.md` §4 already flags as *raising*, not lowering, the stakes for exactly this scanner.

**Scope of this document:** the scan gate that sits between "a note's content changed on disk" and "that content is chunked, embedded, and written into SQLite/Qdrant," as part of the indexing job traced in `docs/EVENT_MODEL.md` §3.4. Out of scope: ADR-0005's pre-commit hook (unchanged), Phase 2 web-ingestion content scanning (TB-4 — a future extension point, §11), and any change to how the vault's own Git history is managed.

## 2. Tool/Mechanism Choice

### 2.1 Research findings (verified 2026-08-27)

The three candidates ADR-0005's research already touched at a high level were re-checked specifically for **standalone file/text scanning**, not git-diff scanning:

| Tool | Standalone-content scan mode | Invocation | Verified against |
|---|---|---|---|
| **detect-secrets** (Yelp) | Yes — pure-Python package; `SecretsCollection().scan_file(path)` under `with default_settings():` is a documented in-process API, no subprocess or git repo required | **In-process Python import** | [Yelp/detect-secrets `docs/design.md`](https://github.com/Yelp/detect-secrets/blob/master/docs/design.md), [main repo](https://github.com/Yelp/detect-secrets) |
| **gitleaks** | Yes — the `detect --no-git` flag is now **deprecated** (since v8.19.0) in favor of `gitleaks dir -s <path>` (aliases: `files`, `directory`), which scans plain directories/files with no git repo required; separately, a `gitleaks stdin` command streams piped data | **Subprocess only** — no documented Go-embeddable or Python library API found | [gitleaks/gitleaks](https://github.com/gitleaks/gitleaks) |
| **trufflehog** | Yes — `trufflehog filesystem [<path>...]` scans arbitrary files/directories without a git clone, and also accepts piped stdin | **Subprocess only** (Go binary); no Python bindings documented | [TruffleHog Filesystem docs](https://trufflesecurity.com/docs/filesystem) |

A fourth relevant fact: trufflehog's headline differentiator is **live credential verification** — for supported detectors it can call out to the real provider API (AWS, GitHub, Stripe, etc.) to confirm a matched string is an actually-active credential. This feature's existence alone is architecturally significant for AI_BRAIN's case (see 2.2).

### 2.2 Recommendation: `detect-secrets`, invoked in-process

**Chosen mechanism:** the `detect-secrets` Python package, called **in-process** (direct Python import, `SecretsCollection.scan_file()`), not via subprocess.

**Justification:**

1. **No subprocess needed, and that's a real architectural win here — not just a style preference.** ADR-0005's subprocess-based design for `git` was driven by a specific, evidenced concern: two "safer-looking" abstraction libraries (GitPython, Dulwich) turned out to carry real injection CVEs of their own, so for git operations specifically, wrapping the real CLI directly was judged safer than trusting a library's internal abstraction. That reasoning doesn't transfer to secret scanning: `detect-secrets` isn't reimplementing a security-critical protocol — it's a pattern/entropy matcher over text. Choosing the in-process form avoids spawning a new OS process for every single note the indexing pipeline touches — a real, recurring cost given this scan sits inside a per-note Huey job that runs once per debounced filesystem save.
2. **No network egress.** `detect-secrets`' plugins (regex + entropy heuristics) never phone home. trufflehog's verification model would introduce a *new*, previously-undesigned outbound network path triggered purely by indexing a note — exactly the kind of egress `docs/SECURITY_MODEL.md`'s TB-9 already treats as sensitive. Silently calling out to AWS/GitHub/Stripe to "verify" a string found in a private note during routine indexing is a new, undesigned trust boundary this design should not introduce — deferred, not adopted.
3. **Purpose-built false-positive tooling that matters concretely for this vault.** `detect-secrets` ships dedicated heuristic filters — `is_sequential_string`, `is_potential_uuid`, `is_templated_secret` (catches `{secret}`/`<secret>`/`${secret}` placeholder shapes), `is_likely_id_string`, `is_swagger_file`, and others — plus an inline `# pragma: allowlist secret` convention. This is directly relevant given `docs/DATA_MODEL.md` §0's confirmed real vault content: CVE identifiers, content hashes, MinHash signatures, and UUID-shaped strings are exactly what these filters exist to suppress. Neither gitleaks nor trufflehog documents an equivalent filter library this granular.
4. **Fits the already-accepted architecture.** ADR-0003 committed the RAG pipeline to "hand-rolled composable primitives on top of already-narrow, purpose-built libraries." A pure-Python library callable as a function is the better fit than adding a second Go binary into the ingestion hot path.

**gitleaks is not rejected outright** — it's already an accepted dependency (ADR-0005). It is recommended here only as an **optional, secondary, batch/periodic defense-in-depth layer**: an occasional full-vault `gitleaks dir -s <vault_root> --report-format json` run (analogous in spirit to ADR-0009's reconciliation sweep), on a schedule — not as the synchronous per-note gate. Explicitly a "nice to have," not a P0 requirement.

## 3. Pipeline Placement

### 3.1 Exact insertion point

Per `docs/EVENT_MODEL.md` §3.4's indexing-job trace, the scan is inserted as the **first substep of step 4** ("Changed → re-derive current truth"):

```
1. Compute current on-disk content-hash for `path`.
2. Compare to stored hash/index_version.
3. Unchanged → no-op (job.completed, noop=true).
4. Changed →
     4a. [NEW] scan_note_for_secrets(path) — before anything else
     4b. parse frontmatter (existing)
     4c. chunk (chonkie) — operating on the *scan-adjusted* text (§4)
     4d. embed (sentence-transformers)
     4e. upsert to Qdrant
     4f. update FTS5 + metadata rows
     4g. update provenance/lineage record
5. Classify semantic event, emit.
```

Placing the scan at 4a rather than earlier (e.g., in the debounce/enqueue layer) is deliberate: it inherits the existing `noop` short-circuit for free — an unchanged note is never rescanned — and it runs inside the same Huey job whose write-ordering guarantee (Qdrant → FTS5 → metadata-row-hash-update-last) already gives this design its crash-recovery story for free (§8).

### 3.2 Whole-note vs. per-chunk: whole-note, scanned once, before chunking

**Decision: scan the whole raw note file, once, before `chonkie` runs.**

Reasoning:
- **A secret can straddle a chunk boundary.** `chonkie`'s splitter has no awareness of credential shapes; a PEM block or an AWS access-key-ID-plus-secret pair could legitimately be split across two chunks by a generic recursive/structural splitter, and a per-chunk scan run independently on each half could miss both halves. Whole-note scanning sees the credential intact regardless of where chunk boundaries later fall.
- **Cost and simplicity.** One `scan_file()` call per note, versus N calls with per-call overhead multiplied, for no correctness benefit given the boundary-splitting risk above.
- **The note, not the chunk, is the natural unit of status-tracking.** `docs/DATA_MODEL.md`'s `notes` table already carries note-level state (`status`, and the orthogonal `index_state` field per `docs/EVENT_MODEL.md` §4.1's own precedent). A scan result is naturally another note-level fact, not a per-chunk one.
- **Line numbers still map forward.** `detect-secrets` reports `line_number` against the raw file. After chunking, each finding's line number can be cheaply mapped to the `chunk_index` whose source-line range contains it.

### 3.3 Synchronous, in the same job — not a separate step

**Decision: synchronous, blocking, inside the same Huey `index_note` job** — not a separately-dispatched job or async step.

Reasoning:
- **It is a gate, not a side-effect.** The point of this control is that nothing gets written to SQLite/Qdrant before it runs. Splitting it into a second, independently-scheduled Huey job would reintroduce exactly the race the design must prevent — Huey gives no cross-job ordering guarantee for two separately-enqueued jobs against the same note.
- **It's cheap relative to what already runs synchronously in this job.** In-process pattern/entropy matching with no network call is a small fraction of the cost of the embedding step that already runs synchronously in the same job.
- **It inherits existing failure handling for free.** A scanner exception or timeout naturally routes into Huey's existing retry/backoff and `job.failed`/reconciliation story — no new failure-handling machinery is required (§8).

The scan function itself should still be a small, independently-testable module (`scan_note_for_secrets()`) callable with no Huey/SQLite/Qdrant dependency, per ADR-0003's decoupling principle — "synchronous, in the job" describes where it's called from, not how it's built.

## 4. On-Detection Behavior

### 4.1 The evidence that shapes this decision

`docs/DATA_MODEL.md` §0 confirms the real vault contains (a) AI chat-export folders where pasted debugging code could legitimately include credential-shaped strings, and (b) an explicit OWASP security-training folder whose entire purpose is to discuss credentials, keys, and vulnerabilities as **teaching material**. This scanner is a P0 item precisely because the naive version — hard-block indexing on any hit — would make exactly this valuable, legitimate content **permanently unsearchable** the first time it's touched, which per `docs/EVENT_MODEL.md` §6 is the literal first real workload Phase 1 runs (the initial bulk ingestion job against this real vault). A bad first outcome on day one is a genuine design failure, not a hypothetical one.

At the same time, `docs/DATA_MODEL.md` §4 is explicit that anything landing in `chunks.chunk_text`/`chunks_fts` "becomes searchable," and TB-8 establishes Qdrant's vectors are an invertible copy of the same text. So doing nothing is not acceptable either.

### 4.2 Recommendation: hybrid — confidence-tiered, redact-not-block by default, always flag, never bulk-suppress

**No tier hard-blocks indexing by default.** The vault is a single-user, local-first, already-on-this-disk knowledge base — the note's raw text is already sitting in the vault as a plaintext file regardless of what AI_BRAIN does. The *incremental* risk indexing adds is specific and narrower than "the secret exists at all": (1) FTS5 makes it trivially greppable, (2) Qdrant's inversion risk (TB-8) makes it reconstructable from vectors, (3) an unconfirmed `note_summarize` call (TB-9) could exfiltrate it to a third-party LLM. All three are addressed by **redacting the specific matched span before it is chunked/embedded/indexed** — not by refusing to index the note at all.

Concretely, per finding, tiered by `detect-secrets` plugin type:

| Tier | Plugins | Action |
|---|---|---|
| **High-confidence** (structural/format-verified: `AWSKeyDetector`, `PrivateKeyDetector`, `GitHubTokenDetector`, `StripeDetector`, and other dedicated regex-format plugins) | Not covered by an allowlist entry (§5) | **Index normally, but redact the exact matched span** in the text that gets chunked/embedded/stored (replace with a fixed, semantically-labeled placeholder, e.g. `[REDACTED:aws_access_key_id]`) — the *source vault file on disk is never touched*, only AI_BRAIN's own derived SQLite/Qdrant copies. Set `notes.secret_scan_status = 'flagged'`, severity `high`, and record a finding row (§6) for human review. |
| **Low-confidence** (entropy-based: `Base64HighEntropyString`, `HexHighEntropyString`) | Any (these are the class most prone to false-positiving on hashes, UUIDs, CVE IDs, and encoded blobs — exactly the shapes `docs/DATA_MODEL.md`'s own content contains) | **Index normally, no redaction.** Redacting a merely-plausible entropy match risks mangling legitimate prose. Record a `low`-severity finding row for optional review, without urgent surfacing. |
| **Allowlisted** (any tier) | Fingerprint matches an existing allowlist entry (§5) | Index normally, no redaction, no new flag (already reviewed). |

**Order matters for the redaction to actually help.** Redaction must happen to the text **before** it is handed to `chonkie` (step 4a runs before 4c). Redacting only the copy destined for SQLite while still embedding the raw text into Qdrant would leave TB-8's inversion exposure fully intact for exactly the content this control exists to protect. Both derived stores must see the same redacted text.

**On whether redaction hurts retrieval quality:** because redaction is scoped to a single, narrowly-matched, high-confidence credential-shaped token — not the surrounding prose — the semantic content of the passage is preserved; a labeled placeholder is arguably a *more* semantically useful thing to embed than a near-random high-entropy string, whose embedding contributes little retrieval signal anyway. This is a reasoned expectation, not a verified one — §10 includes a concrete retrieval-quality regression test to validate it empirically before this behavior ships.

**A stricter opt-in mode is offered, not defaulted:** a config flag (e.g. `secret_scanner.block_on_high_confidence: true`) lets a user who later shares or syncs the vault (TB-5) switch high-confidence findings to hard-block instead of redact-and-flag. Off by default.

## 5. Allowlisting Mechanism

**Not folder-based.** A blanket "don't scan the OWASP folder" exclusion is explicitly rejected — it is precisely the "giant permanent bypass hole" this design must avoid: a real secret accidentally pasted into a note inside that folder would then be silently invisible to the scanner forever, worse than the false-positive problem it would solve.

**Fingerprint-scoped, per-finding, reactive-only:**

- Every finding carries `detect-secrets`' own `hashed_secret` (a hash of the secret value + filename + detector type — never the plaintext secret itself). The allowlist is keyed on this fingerprint, stored in a new `secret_scan_allowlist` table: `(finding_fingerprint, note_path, plugin_type, reason, allowlisted_by, allowlisted_at)`.
- **An entry can only be created against an already-surfaced finding** — a human reviews a specific flagged finding (via the MCP surface in §6) and allowlists *that one fingerprint*, never a path glob or a folder prefix up front.
- **`reason` is required, not optional** — every allowlist decision is a recorded, auditable judgment call (CLAUDE.md rule 24), not a silent toggle.
- **Scoped narrowly by construction:** because the fingerprint incorporates the secret value, a genuinely different secret appearing later in the same file produces a different fingerprint and is **not** covered by a stale allowlist entry.
- **Reviewable, not append-only-forever:** allowlist entries should be surfaced periodically (e.g. via `system_diagnostics`: "N allowlisted findings, oldest M days ago").
- **Distinct from ADR-0005's `.gitleaks.toml` allowlist.** No shared state between the two — one governs "safe to commit to AI_BRAIN's own code repo," the other governs "safe to index as vault content."

`detect-secrets`' own inline `# pragma: allowlist secret` convention is not the primary mechanism here (impractical for bulk legacy chat-export content) but is not precluded as a lighter-weight alternative for future user-authored notes.

## 6. Interfaces

```python
@dataclass(frozen=True)
class SecretFinding:
    plugin_type: str                 # e.g. "AWSKeyDetector", "Base64HighEntropyString"
    line_number: int                 # 1-based, against the raw on-disk file
    confidence: Literal["high", "low"]
    secret_hash: str                 # detect-secrets' own hashed_secret; NEVER the raw value
    span: tuple[int, int] | None     # char offsets within the line, for redaction
    allowlisted: bool                # resolved against secret_scan_allowlist before returning

@dataclass(frozen=True)
class SecretScanResult:
    note_path: Path
    status: Literal["clean", "flagged", "scan_error"]
    findings: list[SecretFinding]
    scanner_version: str
    scan_duration_ms: int
    error: str | None = None         # populated only when status == "scan_error"

def scan_note_for_secrets(note_path: Path, *, timeout_s: float) -> SecretScanResult:
    """
    Pure function: no Huey/SQLite/Qdrant dependency. Wraps
    detect_secrets.SecretsCollection().scan_file(note_path) under
    detect_secrets.settings.default_settings(), classifies each
    PotentialSecret into a confidence tier by plugin type, resolves
    against secret_scan_allowlist, and returns within timeout_s or
    raises on timeout (caller decides fail-closed handling, see §8).
    """
```

**Called from the indexing job**, roughly:

```python
raw_text = note_path.read_text()
scan_result = scan_note_for_secrets(note_path, timeout_s=SCAN_TIMEOUT_S)

if scan_result.status == "scan_error":
    raise IndexingFailure(f"secret scan failed: {scan_result.error}")  # fails closed, see §8

text_for_chunking = raw_text
if any(f.confidence == "high" and not f.allowlisted for f in scan_result.findings):
    text_for_chunking = redact_high_confidence_spans(raw_text, scan_result.findings)

persist_secret_scan_result(note_id, scan_result)   # writes note_secret_findings rows,
                                                    # sets notes.secret_scan_status
# ... proceed: parse frontmatter, chunk(text_for_chunking), embed, upsert ...
```

**Schema addition required** (flag for a future ADR): a `notes.secret_scan_status TEXT CHECK (... IN ('clean','flagged','scan_error'))` column, added **orthogonally** to `notes.status` and `notes.index_state` — reusing the exact precedent `docs/EVENT_MODEL.md` §4.1 already set for `index_state` — plus a `note_secret_findings` table (`note_id`, `plugin_type`, `line_number`, `confidence`, `secret_hash`, `redacted` bool, `detected_at`) and the `secret_scan_allowlist` table from §5.

**MCP surface:** two small tools following ADR-0007's existing contract shape: `secret_findings_list(status="flagged")` (read-only) and `secret_finding_resolve(finding_id, resolution: "allowlist"|"acknowledge", reason)` (mutating, touches only AI_BRAIN's own metadata, not vault content — MRTR-confirmation classification belongs to ADR-0007's owners). `vault_status`/`note_provenance` should also surface `secret_scan_status` per-note, consistent with `index_state`.

## 7. Dependencies

- **`detect-secrets`** (Yelp) — pure Python, PyPI package, actively maintained (4.6k+ GitHub stars, 1,450+ commits, Python 3.10–3.12 supported per PyPI metadata as of 2026-08-27). **New dependency**, and per TB-12's own stated standard, belongs in the same high-scrutiny tier already applied to `chonkie` (both touch 100% of ingested vault content): CVE history, maintainer trust, and release cadence should be checked to the same standard before Phase 1 relies on it.
- **`gitleaks`** — no new dependency; already required by ADR-0005. Reused only for the optional periodic full-vault batch layer noted in §2.2.

## 8. Failure Modes

**If the scanner itself errors, crashes, or times out on a note: fail closed.**

The indexing job raises, the note is **not** written to SQLite or Qdrant, `notes.index_state` is set to `'failed'` with `last_index_error` populated (reusing, unmodified, the exact permanent-failure path `docs/EVENT_MODEL.md` §4.1 already designed for any indexing failure), and the existing reconciliation sweep or a manual `reindex_start` naturally retries it later.

**Why fail closed, not fail open:**
- This scanner is the sole gate between raw content and two persistent stores that are themselves harder to purge after the fact than a single vault file. A control whose entire purpose is closing a P0 risk should not silently disable itself under exactly the condition (a crash) where something is already going wrong.
- The cost of failing closed is small and self-healing: the affected note is temporarily unsearchable, identical in effect to any other transient indexing failure the design already accepts.
- Fail-open would only be defensible if this control were advisory/best-effort. It isn't — `docs/SECURITY_MODEL.md`'s checklist places it in **P0**, "must be resolved before any real vault is pointed at AI_BRAIN."

A generous but bounded timeout should be set even though `detect-secrets` makes no network calls — a genuine hang more plausibly indicates pathological regex backtracking against a crafted or unusual input than ordinary slowness, worth logging distinctly as a potential ReDoS signal.

## 9. Security Considerations

**Residual risk — this is heuristic defense-in-depth, not a guarantee.** `detect-secrets`' plugin set will not catch: secrets deliberately obfuscated (reversed, base64-wrapped-twice, split across two lines), internal/custom token formats with no dedicated plugin, or a secret embedded as an image (screenshot) in a chat export. This carries the same epistemic status `docs/SECURITY_MODEL.md` already assigns its other heuristic controls (e.g., the TB-2 injection-pattern scanner) — this document does not claim otherwise.

**Interaction with ADR-0005's pre-commit gitleaks: genuinely disjoint boundaries, not merely "earlier vs. later."** ADR-0005's hook is installed against AI_BRAIN's own software repository and never receives vault content as input at all (CLAUDE.md rule 13's separation) — it isn't that it scans vault content late, it structurally never scans vault content, at any time. Even in a hypothetical future where the vault's own git repository also got a pre-commit gitleaks hook, that would still fire too late relative to this design's target: for MCP-driven writes, the indexing chain (chunk/embed/upsert) completes before `git.commit_completed` is even triggered; for filesystem-watcher-driven writes, there is no commit gate in the loop at all until whatever periodic Git-backup job next runs. This scanner and ADR-0005's hook protect two disjoint datasets at two disjoint points, and both remain necessary — removing either leaves a real gap the other cannot cover.

**Redaction must happen before embedding, not just before the SQLite write** (restated because it's security-critical, not just a pipeline nicety): redacting only the SQLite copy while embedding raw text into Qdrant would leave TB-8's embedding-inversion exposure fully intact for exactly the content this control exists to protect.

## 10. Test Strategy

**Unit tests for `scan_note_for_secrets()` in isolation** (no Huey/SQLite/Qdrant — pure function):

1. **Positive, high-confidence:** a fixture note styled on the real OWASP folder shape containing a documentation-style example AWS-key-shaped string. Assert: flagged high confidence, **not** hard-blocked, note remains indexed, surrounding prose byte-identical, only the matched span redacted.
2. **Positive, high-confidence, chat-export shape:** a fixture styled on the real `CLAUDE`/`CHAT_GPT` format with a pasted code block containing a realistic-but-fake hardcoded key. Assert same as (1), plus validate FTS5 keyword search on surrounding topic terms still returns the note.
3. **Positive, high-confidence, PEM block:** a `-----BEGIN RSA PRIVATE KEY-----` block inline. Assert detected via `PrivateKeyDetector`, redacted, flagged.
4. **True negative:** an ordinary prose note with no secret-shaped content. Assert zero findings, `secret_scan_status='clean'`.
5. **Low-confidence entropy false-positive regression (core protection this design validates):** fixtures containing a sha256-shaped content hash, a UUID, a CVE identifier, and a MinHash-signature-looking base64 blob. Assert either suppressed by `detect-secrets`' built-in heuristic filters, or — if a finding slips through — tagged `low` confidence and **not** redacted.
6. **Allowlist scoping:** allowlist a specific finding fingerprint; re-scan the same file — assert excluded from `flagged` status. Mutate the file to introduce a *different* secret value at the same line — assert the new finding is **not** suppressed by the stale allowlist entry.
7. **Failure mode:** mock the scanner to raise/time out on an oversized synthetic note. Assert the indexing job fails closed — `index_state='failed'`, no SQLite/Qdrant writes occur, `job.failed` emitted, note re-enqueued by the next reconciliation sweep.
8. **Retrieval-quality regression:** for fixtures (1)–(3), embed and index both raw and redacted versions; run a small fixed set of `vault_search` queries about the *topic* of the passage against each; assert top-K relevance ranking for the redacted version is not meaningfully degraded relative to the raw baseline.

**Integration test:** run the fixtures above through the actual indexing job end-to-end, asserting final SQLite row state (`notes.secret_scan_status`, `note_secret_findings` rows) and Qdrant payload content match expectations — this is the test that would catch an ordering bug where redaction was applied to the SQLite copy but not the embedded text.

## Sources

- [Yelp/detect-secrets — main repository](https://github.com/Yelp/detect-secrets) (accessed 2026-08-27)
- [Yelp/detect-secrets `docs/design.md`](https://github.com/Yelp/detect-secrets/blob/master/docs/design.md) (accessed 2026-08-27)
- [Yelp/detect-secrets `detect_secrets/filters/heuristic.py`](https://github.com/Yelp/detect-secrets/blob/master/detect_secrets/filters/heuristic.py) (accessed 2026-08-27)
- [gitleaks/gitleaks — main repository](https://github.com/gitleaks/gitleaks) (accessed 2026-08-27)
- [gitleaks issue: `.gitleaksignore` not considered when scanning individual files](https://github.com/gitleaks/gitleaks/issues/1178)
- [TruffleHog — Filesystem scan docs](https://trufflesecurity.com/docs/filesystem) (accessed 2026-08-27)
- [TruffleHog Commands: Git vs Filesystem — Truffle Security blog](https://trufflesecurity.com/blog/trufflehog-commands-git-vs-filesystem)
- `docs/SECURITY_MODEL.md`, `docs/adr/0005-git-automation-library.md`, `docs/adr/0003-rag-orchestration-approach.md`, `docs/EVENT_MODEL.md`, `docs/DATA_MODEL.md`

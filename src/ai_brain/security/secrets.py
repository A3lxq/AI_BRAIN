"""Pre-ingestion secret scanning for vault note content.

See `docs/design/pre-ingestion-secret-scanning.md` §6 (Interfaces), §4.2
(on-detection tiering), §5 (allowlist fingerprint matching), and §8 (failure
modes) for the full design this module implements.

This module is a pure, standalone unit with no Huey/SQLite/Qdrant
dependency (design §3.3): it only scans a file on disk and returns a result
object. The "real" allowlist lives in the `secret_scan_allowlist` table
(design §5), which is out of scope here — callers resolve fingerprints
against that table themselves and pass the resulting set in via the
`allowlist` parameter, so this module never touches persistence.

`persist_secret_scan_result()` from the design's §6 example pipeline usage
is likewise out of scope: it depends on the SQLite repository layer, which
does not exist yet in this codebase.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path
from time import perf_counter
from typing import Literal, cast

from detect_secrets.core.plugins.util import get_mapping_from_secret_type_to_class
from detect_secrets.core.potential_secret import PotentialSecret
from detect_secrets.core.secrets_collection import SecretsCollection
from detect_secrets.settings import default_settings

__all__ = [
    "SecretFinding",
    "SecretScanResult",
    "scan_note_for_secrets",
    "redact_high_confidence_spans",
]

logger = logging.getLogger(__name__)

# detect-secrets 1.5.0's built-in plugins (`detect_secrets/plugins/*`), as
# inspected against the installed package: `AWSKeyDetector`,
# `ArtifactoryDetector`, `AzureStorageKeyDetector`, `BasicAuthDetector`,
# `CloudantDetector`, `DiscordBotTokenDetector`, `GitHubTokenDetector`,
# `GitLabTokenDetector`, `IbmCloudIamDetector`, `IbmCosHmacDetector`,
# `IPPublicDetector`, `JwtTokenDetector`, `KeywordDetector`,
# `MailchimpDetector`, `NpmDetector`, `OpenAIDetector`, `PrivateKeyDetector`,
# `PypiTokenDetector`, `SendGridDetector`, `SlackDetector`,
# `SoftlayerDetector`, `SquareOAuthDetector`, `StripeDetector`,
# `TelegramBotTokenDetector`, `TwilioKeyDetector`, `Base64HighEntropyString`,
# `HexHighEntropyString`.
#
# Only the two entropy-based detectors lack a structural/format signature
# (a fixed prefix, a known token shape, a keyword-context regex, etc.) — they
# fire on "this string's Shannon entropy is high", which is exactly the
# heuristic design §4.2 identifies as prone to false-positiving on content
# hashes, UUIDs, CVE IDs, and other encoded-looking-but-not-secret strings.
# Every other built-in plugin verifies a concrete format, so it defaults to
# high confidence.
_LOW_CONFIDENCE_PLUGIN_NAMES: frozenset[str] = frozenset(
    {
        "Base64HighEntropyString",
        "HexHighEntropyString",
    }
)

# `PotentialSecret.type` is detect-secrets' own human-readable label (e.g.
# "AWS Access Key"), not the plugin class name the design's `plugin_type`
# field documents (e.g. "AWSKeyDetector"). This mapping is built once from
# detect-secrets' own registry so the class-name mapping is never guessed or
# hand-duplicated. `get_mapping_from_secret_type_to_class` iterates the
# whole `detect_secrets.plugins` package regardless of which settings
# context is active, so it is safe to resolve at import time.
_PLUGIN_CLASS_BY_SECRET_TYPE = cast(
    "dict[str, type]", get_mapping_from_secret_type_to_class()
)
_SECRET_TYPE_TO_PLUGIN_CLASS_NAME: dict[str, str] = {
    secret_type: plugin_class.__name__
    for secret_type, plugin_class in _PLUGIN_CLASS_BY_SECRET_TYPE.items()
}


def _scanner_version() -> str:
    try:
        return importlib_metadata.version("detect-secrets")
    except importlib_metadata.PackageNotFoundError:
        return "unknown"


_SCANNER_VERSION: str = _scanner_version()


@dataclass(frozen=True)
class SecretFinding:
    """One `detect-secrets` match, classified and resolved against an allowlist."""

    plugin_type: str
    line_number: int
    confidence: Literal["high", "low"]
    secret_hash: str
    span: tuple[int, int] | None
    allowlisted: bool


@dataclass(frozen=True)
class SecretScanResult:
    """Outcome of scanning one note file for secrets.

    `status` semantics:
      - `"clean"`: no findings, or every finding present is already
        allowlisted. An allowlisted finding was already reviewed by a human
        (design §5) and design §4.2 assigns it "index normally, no
        redaction, no new flag" — so a note whose only findings are all
        allowlisted is treated identically to a note with zero findings for
        status purposes. This is a reasoned choice the design does not
        spell out explicitly: `findings` still lists every allowlisted
        finding for audit purposes, only `status` collapses to `"clean"`.
      - `"flagged"`: at least one finding (high or low confidence) is not
        allowlisted.
      - `"scan_error"`: the scan could not complete; `error` is populated.
    """

    note_path: Path
    status: Literal["clean", "flagged", "scan_error"]
    findings: list[SecretFinding]
    scanner_version: str
    scan_duration_ms: int
    error: str | None = None


def _classify_confidence(plugin_type: str) -> Literal["high", "low"]:
    return "low" if plugin_type in _LOW_CONFIDENCE_PLUGIN_NAMES else "high"


def _plugin_type_for(secret: PotentialSecret) -> str:
    return _SECRET_TYPE_TO_PLUGIN_CLASS_NAME.get(secret.type, secret.type)


def _span_for(secret: PotentialSecret, lines: list[str]) -> tuple[int, int] | None:
    secret_value = secret.secret_value
    if secret_value is None:
        return None
    index = secret.line_number - 1
    if index < 0 or index >= len(lines):
        return None
    start = lines[index].find(secret_value)
    if start == -1:
        return None
    return start, start + len(secret_value)


def _run_scan(note_path: Path) -> list[PotentialSecret]:
    collection = SecretsCollection()
    with default_settings():
        collection.scan_file(str(note_path))
    findings: list[PotentialSecret] = []
    for secret_set in collection.data.values():
        findings.extend(secret_set)
    return findings


def scan_note_for_secrets(
    note_path: Path,
    *,
    timeout_s: float,
    allowlist: frozenset[str] = frozenset(),
) -> SecretScanResult:
    """Scan `note_path` for secrets and return a `SecretScanResult`.

    Pure function: no Huey/SQLite/Qdrant dependency. Wraps
    `detect_secrets.SecretsCollection().scan_file(note_path)` under
    `detect_secrets.settings.default_settings()` (verified against the
    installed detect-secrets 1.5.0 — this import path and calling
    convention work as documented), classifies each `PotentialSecret` into a
    confidence tier by plugin type, resolves each finding's `secret_hash`
    against `allowlist`, and returns within `timeout_s`.

    Deviation from the design's own docstring sketch (§6): the design says
    this function "returns within timeout_s or raises on timeout (caller
    decides fail-closed handling)". This implementation instead always
    *returns* a `SecretScanResult` with `status="scan_error"` on timeout or
    any other scan failure, never raising. This matches the design's own
    §6 example call site, which checks
    `scan_result.status == "scan_error"` and raises itself — i.e. the
    design's actual documented calling convention already expects a
    returned error result, not a propagated exception, so this
    implementation follows that convention literally rather than the
    docstring's looser paraphrase.

    Read/decode failures are also always returned as `scan_error`, not
    silently treated as "no secrets found". This is a deliberate departure
    from detect-secrets' own internal behavior: `detect_secrets.core.scan.
    scan_file` catches `IOError` (covering `FileNotFoundError`,
    `PermissionError`, `IsADirectoryError`, ...) and `UnicodeDecodeError`
    internally and silently yields zero findings rather than raising.
    Relying on that would violate design §8's fail-closed philosophy — an
    unreadable or undecodable note would otherwise be misreported as
    `"clean"` instead of `"scan_error"`. This module reads the file itself
    first specifically to surface those failures as `scan_error`.

    Timeout is implemented with `concurrent.futures.ThreadPoolExecutor`
    rather than `signal.alarm`, per the design's own stated preference: it
    is portable and does not require Unix signal handling (which is also
    process-wide and does not compose if this function is ever called from
    a non-main thread). The executor is shut down with `wait=False` after a
    timeout so this function returns promptly rather than blocking on a
    runaway worker thread — detect-secrets makes no network calls or
    filesystem writes, so an abandoned scan thread has no side effects
    beyond eventually finishing or being reclaimed at process exit.
    """
    start = perf_counter()

    try:
        raw_text = note_path.read_text(encoding="utf-8")
    except Exception as exc:
        return SecretScanResult(
            note_path=note_path,
            status="scan_error",
            findings=[],
            scanner_version=_SCANNER_VERSION,
            scan_duration_ms=int((perf_counter() - start) * 1000),
            error=f"unable to read note: {exc}",
        )

    lines = raw_text.splitlines()

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_run_scan, note_path)
        try:
            raw_findings = future.result(timeout=timeout_s)
        except FutureTimeoutError:
            return SecretScanResult(
                note_path=note_path,
                status="scan_error",
                findings=[],
                scanner_version=_SCANNER_VERSION,
                scan_duration_ms=int((perf_counter() - start) * 1000),
                error=f"scan timed out after {timeout_s}s",
            )
        except Exception as exc:
            return SecretScanResult(
                note_path=note_path,
                status="scan_error",
                findings=[],
                scanner_version=_SCANNER_VERSION,
                scan_duration_ms=int((perf_counter() - start) * 1000),
                error=f"unexpected error during secret scan: {exc}",
            )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    findings: list[SecretFinding] = []
    for secret in raw_findings:
        plugin_type = _plugin_type_for(secret)
        secret_hash = secret.secret_hash
        findings.append(
            SecretFinding(
                plugin_type=plugin_type,
                line_number=secret.line_number,
                confidence=_classify_confidence(plugin_type),
                secret_hash=secret_hash,
                span=_span_for(secret, lines),
                allowlisted=secret_hash in allowlist,
            )
        )

    status: Literal["clean", "flagged", "scan_error"]
    if not findings or all(finding.allowlisted for finding in findings):
        status = "clean"
    else:
        status = "flagged"

    return SecretScanResult(
        note_path=note_path,
        status=status,
        findings=findings,
        scanner_version=_SCANNER_VERSION,
        scan_duration_ms=int((perf_counter() - start) * 1000),
        error=None,
    )


def redact_high_confidence_spans(raw_text: str, findings: list[SecretFinding]) -> str:
    """Replace each high-confidence, non-allowlisted finding's match with a placeholder.

    Redaction strategy actually used, and why: `detect-secrets` is
    fundamentally line-oriented (design §6's own `span` field is typed
    `tuple[int, int] | None` precisely because exact character offsets are
    not always recoverable — e.g. `PrivateKeyDetector` reports a PEM block's
    `secret_value` as just the `BEGIN ... KEY` header text, not the key body
    that follows on subsequent lines). This function therefore:

    - Groups findings by `line_number` and rewrites each affected line once.
    - When every finding on a line has a known `span`, replaces exactly
      those character ranges with `[REDACTED:{plugin_type}]`, processing
      multiple spans on the same line from rightmost to leftmost so earlier
      replacements don't invalidate later offsets.
    - When any finding on a line lacks a `span`, replaces the *entire*
      line's content with a single `[REDACTED:{plugin_type}]` placeholder
      instead of guessing an offset — a whole-line redaction is always
      safe, a fabricated character range is not.

    Only findings with `confidence == "high"` and `allowlisted is False` are
    redacted, per design §4.2 ("low-confidence findings are still
    recorded/flagged for optional review, just not redacted").
    """
    lines = raw_text.splitlines(keepends=True)

    to_redact = [
        finding
        for finding in findings
        if finding.confidence == "high" and not finding.allowlisted
    ]

    findings_by_line: dict[int, list[SecretFinding]] = {}
    for finding in to_redact:
        findings_by_line.setdefault(finding.line_number, []).append(finding)

    for line_number, line_findings in findings_by_line.items():
        index = line_number - 1
        if index < 0 or index >= len(lines):
            continue

        line = lines[index]
        if line.endswith("\r\n"):
            ending = "\r\n"
            body = line[: -len(ending)]
        elif line.endswith("\n"):
            ending = "\n"
            body = line[: -len(ending)]
        else:
            ending = ""
            body = line

        unspanned = [finding for finding in line_findings if finding.span is None]
        if unspanned:
            labels = "+".join(sorted({finding.plugin_type for finding in unspanned}))
            body = f"[REDACTED:{labels}]"
        else:
            spanned_with_offsets: list[tuple[SecretFinding, tuple[int, int]]] = []
            for finding in line_findings:
                span = finding.span
                if span is not None:
                    spanned_with_offsets.append((finding, span))
            spanned_with_offsets.sort(key=lambda item: item[1][0], reverse=True)
            for finding, (start, end) in spanned_with_offsets:
                start = max(0, min(start, len(body)))
                end = max(start, min(end, len(body)))
                body = f"{body[:start]}[REDACTED:{finding.plugin_type}]{body[end:]}"

        lines[index] = body + ending

    return "".join(lines)

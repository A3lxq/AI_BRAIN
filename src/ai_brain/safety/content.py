"""Vault content safety boundary.

See `docs/design/vault-safety-boundary.md` §3.2/§4 for the full design this
module implements. This module answers exactly one question: "can this
note's raw text be parsed into metadata + body without ever executing
untrusted content or crashing on adversarial/malformed input?" It performs
no business-level YAML schema validation (see the design's §1 scope note).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

import frontmatter

__all__ = [
    "NoteShape",
    "ParsedNote",
    "VaultContentError",
    "FrontmatterTooLargeError",
    "FrontmatterParseError",
    "parse_note_safely",
]

logger = logging.getLogger(__name__)


class VaultContentError(Exception):
    """Base class for every vault content-safety error this module raises."""


class FrontmatterTooLargeError(VaultContentError):
    """Raised when a real frontmatter block exceeds `max_frontmatter_bytes`.

    Raised *before* the block is ever handed to `python-frontmatter`/PyYAML.
    """


class FrontmatterParseError(VaultContentError):
    """Raised when a real frontmatter block fails to parse as YAML.

    Wraps `python-frontmatter`'s/PyYAML's own parse errors.
    """


class NoteShape(Enum):
    """Which of `DATA_MODEL.md` §0's three real content shapes a note is."""

    FRONTMATTER = "frontmatter"
    LEGACY_CHAT_EXPORT = "legacy_chat_export"
    PLAIN = "plain"


@dataclass(frozen=True)
class ParsedNote:
    """Result of safely parsing a note's raw text."""

    metadata: dict[str, Any]
    body: str
    shape: NoteShape
    source_url: str | None
    provider_hint: str | None
    parse_warning: str | None


# Folder-name -> provider mapping (DATA_MODEL.md §0). Provider hints are
# derived *only* from the folder name, never from note content.
_PROVIDER_HINT_BY_FOLDER: dict[str, str] = {
    "CHAT_GPT": "openai",
    "CLAUDE": "anthropic",
    "GROK_GPT": "xai",
    "QWEN": "qwen",
}

# A real frontmatter block must start at the very first character of the
# file: `---`, the block, then a closing `---`. This mirrors
# python-frontmatter's own delimiter requirement (design §4) and lets us
# measure the block's byte size *before* python-frontmatter/PyYAML ever see
# it, which is the additive control this module exists to provide.
_FRONTMATTER_BLOCK_RE = re.compile(r"\A---[ \t]*\r?\n(.*?\r?\n)---[ \t]*\r?\n", re.DOTALL)

_TURN_HEADER_RE = re.compile(r"^###\s*(USER|ASSISTANT)\s*$", re.IGNORECASE)
_YOU_ASKED_RE = re.compile(r"^#\s*you asked\s*$", re.IGNORECASE)
# Tolerant of provider-name variation ("# chatgpt response", "# claude
# response", "# grok response", ...) while staying narrow enough not to
# match an arbitrary prose heading that happens to contain the word
# "response".
_PROVIDER_RESPONSE_RE = re.compile(r"^#\s*[A-Za-z0-9 _-]{1,40}\s+response\s*$", re.IGNORECASE)
_FROM_LINE_RE = re.compile(r"^>\s*From:\s*(\S.*)$")

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

# Header lines are always short in real content; anything longer is not a
# plausible turn/response header. This is defense-in-depth, not a
# correctness requirement — the patterns above have no nested quantifiers
# and are not vulnerable to catastrophic backtracking regardless of length.
_MAX_PLAUSIBLE_HEADER_LEN = 200


def _extract_raw_frontmatter_block(raw_text: str) -> str | None:
    match = _FRONTMATTER_BLOCK_RE.match(raw_text)
    if match is None:
        return None
    return match.group(1)


def _first_non_empty_line(text: str) -> str | None:
    for line in text.splitlines():
        if line.strip():
            return line
    return None


def _looks_like_legacy_chat_export(text: str) -> bool:
    first_line = _first_non_empty_line(text)
    if first_line is not None:
        stripped_first = first_line.strip()
        if stripped_first.startswith(">") and "From:" in stripped_first:
            return True

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) > _MAX_PLAUSIBLE_HEADER_LEN:
            continue
        if _TURN_HEADER_RE.match(stripped):
            return True
        if _YOU_ASKED_RE.match(stripped) or _PROVIDER_RESPONSE_RE.match(stripped):
            return True
    return False


def _sanitize_source_url(raw_url: str, max_source_url_len: int) -> str:
    sanitized = _ANSI_ESCAPE_RE.sub("", raw_url)
    sanitized = _CONTROL_CHARS_RE.sub("", sanitized)
    return sanitized.strip()[:max_source_url_len]


def _extract_source_url(raw_text: str, max_source_url_len: int) -> tuple[str | None, str | None]:
    """Find and sanitize a legacy `> From: <url>` line.

    Candidate-line detection uses only plain string operations (`startswith`,
    `in`), never a regex, so it is safe to run against an unbounded-length
    line. The candidate line is then TRUNCATED to `max_source_url_len`
    *before* any regex runs on it — this ordering, not the specific pattern
    chosen, is what neutralizes ReDoS risk regardless of input size.
    """
    candidate_line: str | None = None
    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">") and "From:" in stripped:
            candidate_line = stripped
            break

    if candidate_line is None:
        return None, "no From: line found"

    truncated = candidate_line[:max_source_url_len]
    match = _FROM_LINE_RE.match(truncated)
    if match is None:
        return None, "From: line present but unparseable"

    sanitized = _sanitize_source_url(match.group(1), max_source_url_len)
    if not sanitized:
        return None, "From: line present but unparseable"

    return sanitized, None


def _extract_source_url_safely(
    raw_text: str, max_source_url_len: int
) -> tuple[str | None, str | None]:
    try:
        return _extract_source_url(raw_text, max_source_url_len)
    except Exception:
        logger.warning("unexpected error extracting legacy source_url", exc_info=True)
        return None, "unexpected error during From: line extraction"


def _provider_hint_for_folder(folder_name: str) -> str | None:
    return _PROVIDER_HINT_BY_FOLDER.get(folder_name)


def parse_note_safely(
    raw_text: str,
    *,
    folder_name: str,
    max_frontmatter_bytes: int = 8192,
    max_source_url_len: int = 2048,
) -> ParsedNote:
    """Classify and parse `raw_text` into a `ParsedNote`.

    Never calls `yaml.load`/`yaml.safe_load` directly. Real-frontmatter
    parsing is delegated to `python-frontmatter`, whose default loader is
    `yaml.SafeLoader` (verified against upstream source — design §4). The
    raw frontmatter block's byte length is checked *before* it is handed to
    `python-frontmatter` at all, since that library applies no such bound
    itself.

    Raises (only for the FRONTMATTER shape; both are the caller's/ingestion
    job's responsibility to catch and treat as "index as plain body, flag
    for review" — this function itself only raises, it does not degrade
    them internally):
      FrontmatterTooLargeError
      FrontmatterParseError

    Never raises for LEGACY_CHAT_EXPORT or PLAIN shapes: malformed legacy
    fields degrade to `source_url=None` plus a `parse_warning`, and any
    unexpected internal error during that best-effort extraction is caught
    and logged rather than propagated.
    """
    provider_hint = _provider_hint_for_folder(folder_name)

    raw_block = _extract_raw_frontmatter_block(raw_text)
    if raw_block is not None:
        block_byte_len = len(raw_block.encode("utf-8"))
        if block_byte_len > max_frontmatter_bytes:
            raise FrontmatterTooLargeError(
                f"frontmatter block is {block_byte_len} bytes, "
                f"exceeds max_frontmatter_bytes={max_frontmatter_bytes}"
            )
        try:
            post = frontmatter.loads(raw_text)
        except Exception as exc:
            raise FrontmatterParseError(f"failed to parse frontmatter: {exc}") from exc

        return ParsedNote(
            metadata=dict(post.metadata),
            body=post.content,
            shape=NoteShape.FRONTMATTER,
            source_url=None,
            provider_hint=provider_hint,
            parse_warning=None,
        )

    if _looks_like_legacy_chat_export(raw_text):
        source_url, parse_warning = _extract_source_url_safely(raw_text, max_source_url_len)
        return ParsedNote(
            metadata={},
            body=raw_text,
            shape=NoteShape.LEGACY_CHAT_EXPORT,
            source_url=source_url,
            provider_hint=provider_hint,
            parse_warning=parse_warning,
        )

    return ParsedNote(
        metadata={},
        body=raw_text,
        shape=NoteShape.PLAIN,
        source_url=None,
        provider_hint=provider_hint,
        parse_warning=None,
    )

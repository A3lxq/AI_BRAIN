"""Provenance inference for the legacy vault corpus.

See `docs/design/migration-runner-and-vault-ingestion.md` §2.8 and
`docs/DATA_MODEL.md` §0 for the full design this module implements. The
real vault's legacy corpus predates ATHENA AI-BRAIN and has no frontmatter to read
`origin`/`provider` from directly, so those fields must instead be derived
from the AI-origin folder name plus the content shape `parse_note_safely`
(`athena.safety.content`) already classified. This module never reads
file content itself and never decides `origin='human'`/`'web_research'`/
`'merged'` — those are assigned elsewhere in the system, not inferred from
folder/shape here.
"""

from __future__ import annotations

from athena.safety.content import NoteShape

__all__ = ["infer_origin", "infer_provider"]

# DATA_MODEL.md §0's folder-name -> provider mapping. Extend as new
# AI-origin folders are observed in the real vault; an unmapped folder name
# must map to provider=None, never raise KeyError.
_FOLDER_PROVIDER_MAP: dict[str, str] = {
    "CHAT_GPT": "openai",
    "CLAUDE": "anthropic",
    "GROK_GPT": "xai",
    "QWEN": "qwen",
}


def infer_provider(folder_name: str) -> str | None:
    return _FOLDER_PROVIDER_MAP.get(folder_name)


def infer_origin(folder_name: str, shape: NoteShape) -> tuple[str, str | None]:
    provider = infer_provider(folder_name)

    # A folder present in the mapping table is a dedicated, user-organized
    # AI-export folder. DATA_MODEL.md §0 states unconditionally that
    # "origin is ai_generated for the three chat-export folders" -- it does
    # not condition this on `shape` also having been detected as
    # LEGACY_CHAT_EXPORT for this particular file. Shape detection is a
    # content heuristic that can miss an atypical export (an empty stub, a
    # truncated turn, unusual formatting); the user's deliberate folder
    # placement is the stronger and more reliable signal, so folder name
    # wins here even against a PLAIN shape.
    if provider is not None:
        return "ai_generated", provider

    # An unmapped folder whose content still looks like a chat export is
    # itself strong AI-origin evidence per DATA_MODEL.md §0 ("a chat export
    # with no known provider mapping is still clearly AI-original
    # content") -- never silently fall through to 'imported' here.
    if shape is NoteShape.LEGACY_CHAT_EXPORT:
        return "ai_generated", None

    # Reference/training material (e.g. an OWASP-style folder): PLAIN shape
    # in a folder that is neither mapped nor detected as a chat export.
    return "imported", None

"""Structure-aware Markdown chunking (docs/design/indexing-pipeline.md §2.2).

Wraps `chonkie.RecursiveChunker`. `RecursiveChunker.from_recipe("markdown")`
is deliberately never used: it fetches recipe JSON from the Hugging Face Hub
over the network at call time (verified against `chonkie.utils.hub.Hubbie`),
which conflicts with this project's local-first posture. Instead, a
`RecursiveRules` is hand-built below with an ATX-heading level prepended to
chonkie's own default paragraph/sentence/word/token levels.

Two things verified directly against the installed `chonkie==1.7.0` source
that the design doc did not pin down (`chunker/recursive.py`,
`chunker/base.py`, `refinery/overlap.py`, `types/recursive.py`):

- `RecursiveLevel` carries `pattern`/`pattern_mode` fields, but
  `RecursiveChunker._split_text` never reads them -- only `.delimiters` and
  `.whitespace` are consulted, and `.delimiters` are matched as *literal*
  byte substrings (via `chonkie_core.split_offsets`/`split_pattern_offsets`),
  not regex. Heading detection here is therefore literal-substring matching
  on `"\n# "` .. `"\n###### "`, not a real ATX-heading parse.
- `RecursiveChunker.__init__` has no overlap parameter at all. Overlap is a
  separate post-processing pass, `chonkie.OverlapRefinery`, applied to the
  finished chunk list. `chunk_overlap` below is implemented that way
  (`method="prefix"`: each chunk after the first is prefixed with the tail
  of the previous chunk, up to `chunk_overlap` tokens).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from chonkie import OverlapRefinery, RecursiveChunker, RecursiveLevel, RecursiveRules

__all__ = ["Chunk", "chunk_note"]

logger = logging.getLogger(__name__)

# Requires the hash marks to immediately follow a newline (no leading
# indentation), so an indented "    # comment" inside a fenced code block
# does not get mistaken for a heading boundary.
_ATX_HEADER_DELIMITERS = [f"\n{'#' * level} " for level in range(1, 7)]


def _markdown_rules() -> RecursiveRules:
    return RecursiveRules(
        levels=[
            RecursiveLevel(delimiters=_ATX_HEADER_DELIMITERS, include_delim="next"),
            RecursiveLevel(delimiters=["\n\n", "\r\n", "\n", "\r"]),
            RecursiveLevel(delimiters=[". ", "! ", "? "]),
            RecursiveLevel(whitespace=True),
            RecursiveLevel(),
        ]
    )


@dataclass(frozen=True)
class Chunk:
    text: str
    chunk_index: int
    token_count: int | None


def chunk_note(
    body: str,
    *,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    max_tokens: int = 8192,
) -> list[Chunk]:
    """Split `body` into heading-aware chunks.

    `body` is always `parsed.body` from `ai_brain.safety.content.parse_note_safely`
    -- frontmatter-stripped plain Markdown, never raw note text.
    """
    chunker = RecursiveChunker(
        tokenizer="character",
        chunk_size=chunk_size,
        rules=_markdown_rules(),
    )
    raw_chunks = chunker.chunk(body)

    if chunk_overlap > 0:
        refinery = OverlapRefinery(
            tokenizer="character",
            context_size=chunk_overlap,
            mode="token",
            method="prefix",
            merge=True,
        )
        raw_chunks = refinery.refine(raw_chunks)

    chunks: list[Chunk] = []
    for index, raw in enumerate(raw_chunks):
        text = raw.text
        token_count: int | None = raw.token_count
        if token_count is not None and token_count > max_tokens:
            # chonkie's own terminal token-level split already bounds every
            # chunk to `chunk_size` tokens, so this should be unreachable
            # under sane configuration (chunk_size <= max_tokens); kept as a
            # defensive backstop per docs/design/indexing-pipeline.md §2.2.
            encoded = chunker.tokenizer.encode(text)
            text = chunker.tokenizer.decode(encoded[:max_tokens])
            logger.warning(
                "chunk %d exceeded max_tokens=%d (was %d tokens); truncated",
                index,
                max_tokens,
                token_count,
            )
            token_count = max_tokens
        chunks.append(Chunk(text=text, chunk_index=index, token_count=token_count))
    return chunks

"""Tests for `ai_brain.indexing.chunking`.

Turn-header fixtures mirror `docs/DATA_MODEL.md` §0's real vault shapes
(also used by `tests/safety/test_content.py`/`tests/vault/test_ingest.py`):
ChatGPT/Claude-style `# you asked`/`# chatgpt response` headers, and
Qwen-style `### USER`/`### ASSISTANT` headers.
"""

from __future__ import annotations

import logging
import re

import pytest

from ai_brain.indexing.chunking import Chunk, chunk_note

_HEADER_RE = re.compile(r"^(#{1,6} .+)$", re.MULTILINE)

_CHATGPT_STYLE_BODY = (
    "> From: https://chat.openai.com/share/abc123\n"
    "\n"
    "# you asked\n"
    "\n"
    "Can you explain how gradient descent works in the context of training "
    "neural networks, and why the learning rate matters so much for "
    "convergence behavior in practice?\n"
    "\n"
    "# chatgpt response\n"
    "\n"
    "Gradient descent is an iterative optimization algorithm used to minimize "
    "a loss function by moving parameters in the direction of steepest "
    "descent. The learning rate controls the step size taken at each "
    "iteration, and choosing it well is critical: too large and training "
    "diverges, too small and convergence is painfully slow.\n"
    "\n"
    "# you asked\n"
    "\n"
    "What about Adam versus plain SGD as optimizers for deep learning?\n"
    "\n"
    "# chatgpt response\n"
    "\n"
    "Adam adapts the learning rate per parameter using estimates of first "
    "and second moments of the gradients, which often makes it converge "
    "faster than plain SGD on many practical deep learning tasks, though "
    "SGD with momentum can generalize better in some regimes.\n"
)

_QWEN_STYLE_BODY = (
    "### USER\n"
    "Hello there, I have a fairly long question about distributed systems "
    "and how consensus algorithms like Raft actually achieve safety "
    "guarantees even when nodes fail unpredictably during an election.\n"
    "\n"
    "### ASSISTANT\n"
    "Raft achieves safety through a combination of leader election terms, "
    "a majority-quorum commit rule, and a log-matching property that "
    "ensures any two logs containing an entry with the same index and term "
    "are identical up to that point, which prevents divergent histories.\n"
    "\n"
    "### USER\n"
    "And how does that compare to Paxos in terms of implementation complexity?\n"
    "\n"
    "### ASSISTANT\n"
    "Paxos solves the same fundamental problem but is generally considered "
    "harder to reason about and implement correctly because its roles and "
    "phases are less explicitly tied to a single elected leader.\n"
)


def _chunk_start_positions(chunks: list[Chunk]) -> list[int]:
    """Recover each chunk's start offset, valid only when chunk_overlap=0
    (chunks then exactly partition/tile the source text in order -- see
    `test_no_chunk_text_starts_mid_sentence_of_prior_turn` -- so the start
    offset is just the running length total, not a text search).
    """
    positions = []
    cursor = 0
    for chunk in chunks:
        positions.append(cursor)
        cursor += len(chunk.text)
    return positions


class TestTurnHeaderBoundaries:
    """The empirical test flagged by docs/design/indexing-pipeline.md §2.2/§5/§7/§9:
    does chonkie's hand-built heading-aware RecursiveRules actually split at
    AI_BRAIN's real turn-header shapes, or does a custom pre-splitter become a
    required follow-up? Overlap is disabled here (chunk_overlap=0) so chunk
    boundaries reflect chonkie's raw structural splitting, not the separate
    overlap post-processing pass.
    """

    def test_chatgpt_style_headers_start_a_chunk(self) -> None:
        chunks = chunk_note(_CHATGPT_STYLE_BODY, chunk_size=150, chunk_overlap=0)
        assert len(chunks) > 1  # sanity: fixture is actually long enough to need splitting

        headers = [m.group(1) for m in _HEADER_RE.finditer(_CHATGPT_STYLE_BODY)]
        assert headers == ["# you asked", "# chatgpt response", "# you asked", "# chatgpt response"]

        starts = _chunk_start_positions(chunks)
        for header in headers:
            header_pos = _CHATGPT_STYLE_BODY.index(header)
            # A header (after the very first, which coincides with index 0
            # only when nothing precedes it) must land at some chunk's start,
            # not buried in the middle of a chunk carrying prior turn content.
            assert any(abs(start - header_pos) <= 1 for start in starts), (
                f"header {header!r} at {header_pos} does not align with any "
                f"chunk boundary {starts}"
            )

    def test_qwen_style_headers_start_a_chunk(self) -> None:
        chunks = chunk_note(_QWEN_STYLE_BODY, chunk_size=150, chunk_overlap=0)
        assert len(chunks) > 1

        headers = [m.group(1) for m in _HEADER_RE.finditer(_QWEN_STYLE_BODY)]
        assert headers == ["### USER", "### ASSISTANT", "### USER", "### ASSISTANT"]

        starts = _chunk_start_positions(chunks)
        for header in headers:
            header_pos = _QWEN_STYLE_BODY.index(header)
            assert any(abs(start - header_pos) <= 1 for start in starts), (
                f"header {header!r} at {header_pos} does not align with any "
                f"chunk boundary {starts}"
            )

    def test_no_chunk_text_starts_mid_sentence_of_prior_turn(self) -> None:
        """Stronger phrasing of the same finding: every chunk after the first
        either starts with a turn header or is a direct word/sentence-level
        continuation of the *same* turn as the previous chunk -- headers are
        never swallowed into the interior of an unrelated chunk."""
        chunks = chunk_note(_QWEN_STYLE_BODY, chunk_size=150, chunk_overlap=0)
        joined = "".join(c.text for c in chunks)
        assert joined == _QWEN_STYLE_BODY  # splitting is a pure partition, no data loss/reorder


class TestCodeFenceHandling:
    def test_fence_smaller_than_chunk_size_is_never_split(self) -> None:
        body = (
            "# you asked\n\nShow me a python function.\n\n"
            "# chatgpt response\n\nSure, here you go:\n\n"
            "```python\n"
            "def foo(x):\n"
            "    # this is a comment inside the fence\n"
            "    return x + 1\n"
            "```\n\nHope that helps.\n"
        )
        fence = body[body.index("```python") : body.index("return x + 1") + len("return x + 1") + 5]
        chunks = chunk_note(body, chunk_size=500, chunk_overlap=0)
        assert any(fence in chunk.text for chunk in chunks), [c.text for c in chunks]

    def test_fence_exceeding_chunk_size_is_split_mid_fence_known_limitation(self) -> None:
        """Documents a real, verified limitation rather than asserting a false
        guarantee: chonkie's RecursiveChunker (default rules or this module's
        hand-built heading-aware rules) has no fenced-code-block awareness at
        all. A fence longer than `chunk_size` tokens *will* be split across
        chunk boundaries, headers or not. See the module docstring / design
        doc follow-up note -- a genuine fence-aware pre-splitter is a
        separate, not-yet-built enhancement, not something rule tuning fixes.
        """
        fence_lines = "\n".join(f"line_{i} = {i}" for i in range(40))
        body = (
            "# you asked\nWrite a long script.\n\n"
            "# chatgpt response\nHere:\n\n```python\n" + fence_lines + "\n```\n\nDone.\n"
        )
        fence_start = body.index("```python")
        fence_end = body.index("```", fence_start + 3) + 3

        chunks = chunk_note(body, chunk_size=120, chunk_overlap=0)
        starts = _chunk_start_positions(chunks)
        boundary_inside_fence = [s for s in starts if fence_start < s < fence_end]
        assert boundary_inside_fence, (
            "expected this fixture to demonstrate the known mid-fence-split "
            "limitation; if this now fails, chonkie's behavior changed and "
            "the module docstring's caveat should be revisited"
        )


class TestEmptyAndTrivialInputs:
    def test_empty_string_does_not_raise(self) -> None:
        assert chunk_note("") == []

    def test_whitespace_only_does_not_raise(self) -> None:
        chunks = chunk_note("   \n\n\t \n")
        assert len(chunks) <= 1

    def test_single_word_does_not_raise(self) -> None:
        chunks = chunk_note("hello")
        assert len(chunks) == 1
        assert chunks[0].text == "hello"
        assert chunks[0].chunk_index == 0


class TestOversizedChunkTruncation:
    def test_oversized_chunk_is_truncated_not_raised(self) -> None:
        # chonkie's own terminal token-level split bounds every chunk to
        # `chunk_size`, so a chunk can only exceed `max_tokens` if the caller
        # configures chunk_size > max_tokens. That misconfiguration is the
        # only way to actually exercise this module's own defensive
        # truncation backstop (docs/design/indexing-pipeline.md §2.2/§5).
        body = "x" * 30_000
        chunks = chunk_note(body, chunk_size=20_000, chunk_overlap=0, max_tokens=8192)
        assert all(c.token_count is not None and c.token_count <= 8192 for c in chunks)
        assert all(len(c.text) <= 8192 for c in chunks)

    def test_oversized_chunk_truncation_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        body = "y" * 30_000
        with caplog.at_level(logging.WARNING, logger="ai_brain.indexing.chunking"):
            chunk_note(body, chunk_size=20_000, chunk_overlap=0, max_tokens=8192)
        assert any(r.levelno == logging.WARNING for r in caplog.records)


class TestChunkIndexSequencing:
    def test_chunk_index_is_zero_based_and_sequential(self) -> None:
        chunks = chunk_note(_CHATGPT_STYLE_BODY, chunk_size=100, chunk_overlap=10)
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    def test_single_chunk_has_index_zero(self) -> None:
        chunks = chunk_note("A short note that fits in one chunk.")
        assert chunks[0].chunk_index == 0


class TestOverlap:
    def test_overlap_prefixes_each_chunk_after_the_first_with_prior_tail(self) -> None:
        chunks = chunk_note(_QWEN_STYLE_BODY, chunk_size=150, chunk_overlap=20)
        assert len(chunks) > 1
        for previous, current in zip(chunks, chunks[1:], strict=False):
            tail = previous.text[-20:]
            assert current.text.startswith(tail)

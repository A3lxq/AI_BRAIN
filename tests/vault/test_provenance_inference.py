"""Tests for `ai_brain.vault.provenance_inference`.

Fixtures mirror `docs/DATA_MODEL.md` §0's real vault folder layout
(`CHAT_GPT`/`CLAUDE`/`GROK_GPT`/`QWEN` as AI-origin folders, an
OWASP-style folder as unrecognized reference material).
"""

from __future__ import annotations

import pytest

from ai_brain.safety.content import NoteShape
from ai_brain.vault.provenance_inference import infer_origin, infer_provider


class TestInferOriginKnownFoldersChatExport:
    @pytest.mark.parametrize(
        ("folder_name", "expected_provider"),
        [
            ("CHAT_GPT", "openai"),
            ("CLAUDE", "anthropic"),
            ("GROK_GPT", "xai"),
            ("QWEN", "qwen"),
        ],
    )
    def test_known_folder_with_chat_export_shape(
        self, folder_name: str, expected_provider: str
    ) -> None:
        origin, provider = infer_origin(folder_name, NoteShape.LEGACY_CHAT_EXPORT)
        assert origin == "ai_generated"
        assert provider == expected_provider


class TestInferOriginUnmappedFolders:
    def test_unrecognized_folder_with_chat_export_shape_is_still_ai_generated(self) -> None:
        # DATA_MODEL.md §0: a chat export with no known provider mapping is
        # still clearly AI-original content -- must never raise KeyError.
        origin, provider = infer_origin("MYSTERY_AI", NoteShape.LEGACY_CHAT_EXPORT)
        assert origin == "ai_generated"
        assert provider is None

    def test_owasp_style_folder_with_plain_shape_is_imported(self) -> None:
        origin, provider = infer_origin("OWASP-A05:injection.mdfiles", NoteShape.PLAIN)
        assert origin == "imported"
        assert provider is None

    def test_unrecognized_folder_with_plain_shape_is_imported(self) -> None:
        origin, provider = infer_origin("RANDOM_NOTES", NoteShape.PLAIN)
        assert origin == "imported"
        assert provider is None


class TestInferOriginKnownFolderPlainShape:
    """Decision: known AI-origin folder name + PLAIN shape.

    DATA_MODEL.md §0 does not spell out this exact combination, so the
    interpretation is made explicit and tested here rather than left
    silent. Chosen interpretation (see the comment in
    `provenance_inference.infer_origin`): folder name wins independently of
    shape for a folder present in `_FOLDER_PROVIDER_MAP`, since §0 states
    the ai_generated classification unconditionally for the AI-origin
    folders, and deliberate folder placement by the user is a more reliable
    signal than a content-shape heuristic that can miss an atypical export
    (e.g. a short stub with no turn headers) inside an otherwise
    AI-dedicated folder.
    """

    @pytest.mark.parametrize(
        ("folder_name", "expected_provider"),
        [
            ("CHAT_GPT", "openai"),
            ("CLAUDE", "anthropic"),
            ("GROK_GPT", "xai"),
            ("QWEN", "qwen"),
        ],
    )
    def test_known_folder_with_plain_shape_still_ai_generated(
        self, folder_name: str, expected_provider: str
    ) -> None:
        origin, provider = infer_origin(folder_name, NoteShape.PLAIN)
        assert origin == "ai_generated"
        assert provider == expected_provider


class TestInferOriginNeverReturnsOtherOriginValues:
    @pytest.mark.parametrize("shape", list(NoteShape))
    @pytest.mark.parametrize(
        "folder_name", ["CHAT_GPT", "CLAUDE", "GROK_GPT", "QWEN", "UNKNOWN", ""]
    )
    def test_origin_is_always_ai_generated_or_imported(
        self, folder_name: str, shape: NoteShape
    ) -> None:
        origin, _ = infer_origin(folder_name, shape)
        assert origin in {"ai_generated", "imported"}
        assert origin not in {"human", "web_research", "merged"}


class TestInferProvider:
    @pytest.mark.parametrize(
        ("folder_name", "expected"),
        [
            ("CHAT_GPT", "openai"),
            ("CLAUDE", "anthropic"),
            ("GROK_GPT", "xai"),
            ("QWEN", "qwen"),
        ],
    )
    def test_known_folder_maps_to_provider(self, folder_name: str, expected: str) -> None:
        assert infer_provider(folder_name) == expected

    def test_unknown_folder_returns_none(self) -> None:
        assert infer_provider("SOME_OTHER_FOLDER") is None

    def test_empty_string_folder_name_returns_none_without_crashing(self) -> None:
        assert infer_provider("") is None

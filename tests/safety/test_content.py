"""Tests for `athena.safety.content` — the vault content safety boundary.

Fixtures mirror `docs/DATA_MODEL.md` §0's three real vault content shapes:
frontmatter, legacy chat-export, and plain reference material.
"""

from __future__ import annotations

import time

import pytest

from athena.safety.content import (
    FrontmatterParseError,
    FrontmatterTooLargeError,
    NoteShape,
    parse_note_safely,
)


class TestFrontmatterShape:
    def test_valid_frontmatter_happy_path(self) -> None:
        raw_text = (
            "---\n"
            "title: My Note\n"
            "tags:\n"
            "  - alpha\n"
            "  - beta\n"
            "---\n"
            "Body content here.\n"
        )
        result = parse_note_safely(raw_text, folder_name="CLAUDE")
        assert result.shape is NoteShape.FRONTMATTER
        assert result.metadata["title"] == "My Note"
        assert result.metadata["tags"] == ["alpha", "beta"]
        assert "Body content here." in result.body
        assert result.provider_hint == "anthropic"
        assert result.parse_warning is None

    def test_frontmatter_exceeding_max_bytes_raises(self) -> None:
        big_value = "a" * 500
        raw_text = f"---\nkey: \"{big_value}\"\n---\nBody.\n"
        with pytest.raises(FrontmatterTooLargeError):
            parse_note_safely(raw_text, folder_name="CLAUDE", max_frontmatter_bytes=100)

    def test_frontmatter_within_default_max_bytes_does_not_raise(self) -> None:
        raw_text = "---\nkey: value\n---\nBody.\n"
        result = parse_note_safely(raw_text, folder_name="CLAUDE")
        assert result.shape is NoteShape.FRONTMATTER

    def test_malformed_yaml_raises_parse_error(self) -> None:
        raw_text = "---\nfoo: [1, 2\nbar: : invalid\n---\nBody.\n"
        with pytest.raises(FrontmatterParseError):
            parse_note_safely(raw_text, folder_name="CLAUDE")

    def test_frontmatter_with_anchor_alias_reuse_completes_quickly(self) -> None:
        lines = ["a: &anchor value"] + [f"b{i}: *anchor" for i in range(200)]
        raw_text = "---\n" + "\n".join(lines) + "\n---\nBody.\n"
        start = time.monotonic()
        result = parse_note_safely(raw_text, folder_name="CLAUDE", max_frontmatter_bytes=100_000)
        elapsed = time.monotonic() - start
        assert result.shape is NoteShape.FRONTMATTER
        assert elapsed < 2.0

    def test_unsafe_yaml_tag_is_not_executed(self) -> None:
        raw_text = (
            "---\n"
            'evil: !!python/object/apply:os.system ["echo pwned-marker-file"]\n'
            "---\n"
            "Body.\n"
        )
        try:
            result = parse_note_safely(raw_text, folder_name="CLAUDE")
        except FrontmatterParseError:
            # SafeLoader refused the tag outright — acceptable per design §4.
            return
        # If it didn't raise, the value must not be an executed/constructed
        # Python object (e.g. the os.system() return code, an int) — it must
        # remain inert, unparsed data (or absent).
        assert "evil" not in result.metadata or not isinstance(result.metadata["evil"], int)


class TestLegacyChatExportShape:
    def test_chatgpt_style_fixture(self) -> None:
        raw_text = (
            "> From: https://chat.openai.com/share/abc123\n"
            "\n"
            "# you asked\n"
            "What is the capital of France?\n"
            "\n"
            "# chatgpt response\n"
            "The capital of France is Paris.\n"
        )
        result = parse_note_safely(raw_text, folder_name="CHAT_GPT")
        assert result.shape is NoteShape.LEGACY_CHAT_EXPORT
        assert result.source_url == "https://chat.openai.com/share/abc123"
        assert result.provider_hint == "openai"
        assert result.parse_warning is None

    def test_qwen_style_fixture_no_from_line(self) -> None:
        raw_text = (
            "### USER\n"
            "Hello there.\n"
            "\n"
            "### ASSISTANT\n"
            "Hi! How can I help?\n"
        )
        result = parse_note_safely(raw_text, folder_name="QWEN")
        assert result.shape is NoteShape.LEGACY_CHAT_EXPORT
        assert result.source_url is None
        assert result.provider_hint == "qwen"
        assert result.parse_warning == "no From: line found"

    def test_claude_style_response_header_variant(self) -> None:
        raw_text = (
            "> From: https://claude.ai/share/xyz\n"
            "\n"
            "# you asked\n"
            "Explain recursion.\n"
            "\n"
            "# claude response\n"
            "Recursion is when a function calls itself.\n"
        )
        result = parse_note_safely(raw_text, folder_name="CLAUDE")
        assert result.shape is NoteShape.LEGACY_CHAT_EXPORT
        assert result.source_url == "https://claude.ai/share/xyz"
        assert result.provider_hint == "anthropic"

    def test_huge_from_line_with_no_newline_completes_quickly(self) -> None:
        raw_text = "> From: " + ("x" * 10_000)
        start = time.monotonic()
        result = parse_note_safely(raw_text, folder_name="CHAT_GPT")
        elapsed = time.monotonic() - start
        assert elapsed < 2.0
        assert result.shape is NoteShape.LEGACY_CHAT_EXPORT
        assert result.source_url is not None
        assert len(result.source_url) <= 2048

    def test_from_line_with_nul_bytes_and_ansi_escapes_is_sanitized(self) -> None:
        raw_text = (
            "> From: \x1b[31mhttp://example.com/\x00path\x1b[0m\n"
            "\n"
            "### USER\n"
            "hi\n"
        )
        result = parse_note_safely(raw_text, folder_name="CHAT_GPT")
        assert result.shape is NoteShape.LEGACY_CHAT_EXPORT
        assert result.source_url is not None
        assert "\x00" not in result.source_url
        assert "\x1b" not in result.source_url
        assert result.source_url == "http://example.com/path"

    def test_unrecognized_folder_name_gives_none_provider_hint(self) -> None:
        raw_text = "### USER\nhi\n\n### ASSISTANT\nhello\n"
        result = parse_note_safely(raw_text, folder_name="SOME_UNKNOWN_FOLDER")
        assert result.provider_hint is None
        assert result.shape is NoteShape.LEGACY_CHAT_EXPORT


class TestPlainShape:
    def test_setext_header_reference_material_is_plain(self) -> None:
        raw_text = (
            "OWASP A05: Security Misconfiguration\n"
            "=====================================\n"
            "\n"
            "Version: 1.0 (Draft)\n"
            "\n"
            "Some prose content describing the vulnerability category.\n"
            "\n"
            "Sub-section\n"
            "-----------\n"
            "\n"
            "More prose.\n"
        )
        result = parse_note_safely(raw_text, folder_name="OWASP-A05")
        assert result.shape is NoteShape.PLAIN
        assert result.source_url is None
        assert result.metadata == {}
        assert result.parse_warning is None

    def test_empty_string_input_is_plain(self) -> None:
        result = parse_note_safely("", folder_name="CLAUDE")
        assert result.shape is NoteShape.PLAIN
        assert result.metadata == {}
        assert result.body == ""

    def test_whitespace_only_input_is_plain(self) -> None:
        result = parse_note_safely("   \n\n\t \n", folder_name="CLAUDE")
        assert result.shape is NoteShape.PLAIN

    def test_single_word_input_is_plain(self) -> None:
        result = parse_note_safely("hello", folder_name="CLAUDE")
        assert result.shape is NoteShape.PLAIN
        assert result.body == "hello"


class TestProviderHintMapping:
    @pytest.mark.parametrize(
        ("folder_name", "expected"),
        [
            ("CHAT_GPT", "openai"),
            ("CLAUDE", "anthropic"),
            ("GROK_GPT", "xai"),
            ("QWEN", "qwen"),
        ],
    )
    def test_known_folder_names_map_correctly(self, folder_name: str, expected: str) -> None:
        result = parse_note_safely("hello", folder_name=folder_name)
        assert result.provider_hint == expected

    def test_unknown_folder_name_never_raises(self) -> None:
        result = parse_note_safely("hello", folder_name="")
        assert result.provider_hint is None

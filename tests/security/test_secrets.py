"""Tests for `athena.security.secrets` — the pre-ingestion secret scanner.

These tests run the real `detect-secrets` scanner against real fixture
files written to `tmp_path`; the scanner library itself is never mocked,
since the point of these tests is to verify real detection behavior against
the installed detect-secrets version. Only the timeout mechanism (test 7)
patches `SecretsCollection.scan_file` to make a hang reproducible on demand.

All credential-shaped strings below are well-known documentation/example
values (e.g. AWS's own published example access key) or synthetic
fixtures — never real credentials.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from detect_secrets.core.secrets_collection import SecretsCollection

from athena.security.secrets import (
    SecretFinding,
    SecretScanResult,
    redact_high_confidence_spans,
    scan_note_for_secrets,
)

_AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


class TestHighConfidencePositive:
    def test_aws_access_key_detected_as_high_confidence_and_flags(self, tmp_path: Path) -> None:
        note = _write(
            tmp_path,
            "aws_example.txt",
            "Example AWS credentials block used in an OWASP training note.\n"
            "\n"
            f"aws_access_key_id = {_AWS_ACCESS_KEY_ID}\n"
            "\n"
            "This paragraph continues discussing key rotation afterward.\n",
        )

        result = scan_note_for_secrets(note, timeout_s=5.0)

        assert result.status == "flagged"
        assert result.error is None
        high_confidence = [f for f in result.findings if f.confidence == "high"]
        assert len(high_confidence) >= 1
        assert any(f.plugin_type == "AWSKeyDetector" for f in high_confidence)
        assert all(not f.allowlisted for f in high_confidence)

    def test_pem_private_key_block_detected_as_high_confidence(self, tmp_path: Path) -> None:
        note = _write(
            tmp_path,
            "pem_example.txt",
            "This note discusses key rotation procedures.\n"
            "\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEr8c3sBrbF6NRThm7QcnFbb+jc9AZa\n"
            "oyD+bp3sBrbF6NRThm7QcnFbb+jc9AZaoyD+bp3sBrbF6NRThm7QcnFbwIDAQAB\n"
            "AoGAKV1a7dxRcVjPSKQVUOsm6q9J+6ZKY0RPfR9oM7Zi4TAaLYd1TmXfEXAMPLE\n"
            "-----END RSA PRIVATE KEY-----\n"
            "\n"
            "More prose after the key.\n",
        )

        result = scan_note_for_secrets(note, timeout_s=5.0)

        assert result.status == "flagged"
        assert any(
            f.plugin_type == "PrivateKeyDetector" and f.confidence == "high"
            for f in result.findings
        )


class TestTrueNegative:
    def test_ordinary_prose_is_clean(self, tmp_path: Path) -> None:
        note = _write(
            tmp_path,
            "prose.txt",
            "This is an ordinary paragraph about gardening. Tomatoes prefer\n"
            "a soil pH between six and seven, and benefit from consistent\n"
            "watering throughout the growing season. There is nothing here\n"
            "that resembles a credential of any kind.\n",
        )

        result = scan_note_for_secrets(note, timeout_s=5.0)

        assert result.status == "clean"
        assert result.findings == []
        assert result.error is None


class TestLowConfidenceEntropyFalsePositives:
    def test_hash_uuid_and_cve_do_not_cause_a_hard_flag(self, tmp_path: Path) -> None:
        """Document actual detect-secrets 1.5.0 behavior for these shapes.

        Against the installed version, none of a sha256-shaped hex hash, a
        UUID4, or a CVE identifier trip any plugin at all — detect-secrets'
        own heuristic filters (`is_potential_uuid`, sequential/format
        checks, etc.) suppress them before they ever become a
        `PotentialSecret`. Per the task's own allowance, "not detected at
        all" is a valid pass here, just as much as "detected but low
        confidence" would be — the assertion below accepts either outcome
        so this test tracks real library behavior rather than assuming one
        specific result.
        """
        note = _write(
            tmp_path,
            "entropy_shapes.txt",
            "Incident notes.\n"
            "\n"
            "content_hash: 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08\n"
            "correlation_id: 550e8400-e29b-41d4-a716-446655440000\n"
            "reference: CVE-2026-42215\n"
            "\n"
            "This paragraph is ordinary prose about tomatoes and soil pH,\n"
            "included so the fixture is not a bare list of tokens.\n",
        )

        result = scan_note_for_secrets(note, timeout_s=5.0)

        assert result.status in ("clean", "flagged")
        for finding in result.findings:
            assert finding.confidence == "low"
        assert result.error is None


class TestAllowlistResolution:
    def test_allowlisted_hash_marks_finding_and_clears_flagged_status(
        self, tmp_path: Path
    ) -> None:
        note = _write(
            tmp_path,
            "aws_example.txt",
            f"aws_access_key_id = {_AWS_ACCESS_KEY_ID}\n",
        )

        first_scan = scan_note_for_secrets(note, timeout_s=5.0)
        assert first_scan.status == "flagged"
        target = next(f for f in first_scan.findings if f.plugin_type == "AWSKeyDetector")
        assert target.allowlisted is False

        second_scan = scan_note_for_secrets(
            note, timeout_s=5.0, allowlist=frozenset({target.secret_hash})
        )

        rescanned = next(
            f for f in second_scan.findings if f.plugin_type == "AWSKeyDetector"
        )
        assert rescanned.allowlisted is True
        assert second_scan.status == "clean"


class TestRedaction:
    def test_redacts_aws_key_and_preserves_surrounding_text(self, tmp_path: Path) -> None:
        raw_text = (
            "Example AWS credentials block used in an OWASP training note.\n"
            "\n"
            f"aws_access_key_id = {_AWS_ACCESS_KEY_ID}\n"
            "\n"
            "This paragraph continues discussing key rotation afterward.\n"
        )
        note = _write(tmp_path, "aws_example.txt", raw_text)

        result = scan_note_for_secrets(note, timeout_s=5.0)
        redacted = redact_high_confidence_spans(raw_text, result.findings)

        assert _AWS_ACCESS_KEY_ID not in redacted
        assert "[REDACTED:AWSKeyDetector]" in redacted
        assert "Example AWS credentials block used in an OWASP training note." in redacted
        assert "This paragraph continues discussing key rotation afterward." in redacted
        assert "aws_access_key_id = " in redacted

    def test_allowlisted_high_confidence_finding_is_not_redacted(self, tmp_path: Path) -> None:
        raw_text = f"aws_access_key_id = {_AWS_ACCESS_KEY_ID}\n"
        note = _write(tmp_path, "aws_example.txt", raw_text)

        first_scan = scan_note_for_secrets(note, timeout_s=5.0)
        target = next(f for f in first_scan.findings if f.plugin_type == "AWSKeyDetector")

        second_scan = scan_note_for_secrets(
            note, timeout_s=5.0, allowlist=frozenset({target.secret_hash})
        )
        redacted = redact_high_confidence_spans(raw_text, second_scan.findings)

        assert redacted == raw_text


class TestTimeoutHandling:
    def test_slow_scan_returns_scan_error_on_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        note = _write(tmp_path, "note.txt", "hello world\n")

        def _slow_scan_file(self: SecretsCollection, filename: str) -> None:
            time.sleep(1.0)

        monkeypatch.setattr(SecretsCollection, "scan_file", _slow_scan_file)

        start = time.monotonic()
        result = scan_note_for_secrets(note, timeout_s=0.05)
        elapsed = time.monotonic() - start

        assert result.status == "scan_error"
        assert result.error is not None
        assert "timed out" in result.error
        assert elapsed < 1.0


class TestGeneralScanError:
    def test_nonexistent_file_returns_scan_error_not_raised_exception(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "does_not_exist.txt"

        result = scan_note_for_secrets(missing, timeout_s=5.0)

        assert result.status == "scan_error"
        assert result.error is not None
        assert result.findings == []

    def test_directory_path_returns_scan_error(self, tmp_path: Path) -> None:
        directory = tmp_path / "a_directory"
        directory.mkdir()

        result = scan_note_for_secrets(directory, timeout_s=5.0)

        assert result.status == "scan_error"
        assert result.error is not None


class TestResultShape:
    def test_scan_result_and_finding_types(self, tmp_path: Path) -> None:
        note = _write(tmp_path, "prose.txt", "Nothing sensitive here.\n")
        result = scan_note_for_secrets(note, timeout_s=5.0)

        assert isinstance(result, SecretScanResult)
        assert result.note_path == note
        assert isinstance(result.scanner_version, str) and result.scanner_version
        assert isinstance(result.scan_duration_ms, int)
        assert result.scan_duration_ms >= 0
        for finding in result.findings:
            assert isinstance(finding, SecretFinding)

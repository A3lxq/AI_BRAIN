"""Tests for ai_brain.hardening.permissions."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from ai_brain.hardening.permissions import (
    PermissionHardeningFailed,
    ensure_private_dir,
    ensure_private_file,
)


@pytest.fixture
def restore_umask() -> Iterator[None]:
    old = os.umask(0)
    os.umask(old)
    yield None
    os.umask(old)


def test_fresh_file_creation_is_0600(tmp_path: Path) -> None:
    target = tmp_path / "ai_brain.db"
    ensure_private_file(target)
    assert target.exists()
    assert target.stat().st_mode & 0o777 == 0o600


def test_fresh_dir_creation_is_0700(tmp_path: Path) -> None:
    target = tmp_path / "data"
    ensure_private_dir(target)
    assert target.is_dir()
    assert target.stat().st_mode & 0o777 == 0o700


def test_file_creation_is_umask_independent(tmp_path: Path, restore_umask: None) -> None:
    os.umask(0o000)
    target = tmp_path / "job-store.db"
    ensure_private_file(target)
    assert target.stat().st_mode & 0o777 == 0o600


def test_dir_creation_is_umask_independent(tmp_path: Path, restore_umask: None) -> None:
    os.umask(0o000)
    target = tmp_path / "data"
    ensure_private_dir(target)
    assert target.stat().st_mode & 0o777 == 0o700


def test_file_creation_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "ai_brain.db"
    ensure_private_file(target)
    ensure_private_file(target)
    assert target.stat().st_mode & 0o777 == 0o600


def test_dir_creation_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "data"
    ensure_private_dir(target)
    ensure_private_dir(target)
    assert target.stat().st_mode & 0o777 == 0o700


def test_pre_existing_file_with_wrong_mode_is_corrected(tmp_path: Path) -> None:
    target = tmp_path / "legacy.db"
    target.touch()
    os.chmod(target, 0o644)
    assert target.stat().st_mode & 0o777 == 0o644

    ensure_private_file(target)

    assert target.stat().st_mode & 0o777 == 0o600


def test_pre_existing_dir_with_wrong_mode_is_corrected(tmp_path: Path) -> None:
    target = tmp_path / "data"
    target.mkdir(mode=0o755)
    os.chmod(target, 0o755)  # noqa: S103 - deliberately permissive precondition under test
    assert target.stat().st_mode & 0o777 == 0o755

    ensure_private_dir(target)

    assert target.stat().st_mode & 0o777 == 0o700


def test_tier_b_chmod_permission_error_is_logged_and_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    target = tmp_path / "legacy.db"
    target.touch()
    os.chmod(target, 0o644)

    def _raise_permission_error(*args: object, **kwargs: object) -> None:
        raise PermissionError("simulated environmental failure")

    monkeypatch.setattr(os, "chmod", _raise_permission_error)

    with caplog.at_level(logging.CRITICAL, logger="ai_brain.hardening.permissions"):
        ensure_private_file(target)

    critical_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert critical_records
    assert str(target) in critical_records[0].getMessage()


def test_tier_b_mkdir_oserror_is_logged_and_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    target = tmp_path / "data"

    def _raise_oserror(*args: object, **kwargs: object) -> None:
        raise OSError("simulated environmental failure")

    monkeypatch.setattr(Path, "mkdir", _raise_oserror)

    with caplog.at_level(logging.CRITICAL, logger="ai_brain.hardening.permissions"):
        ensure_private_dir(target)

    critical_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert critical_records
    assert str(target) in critical_records[0].getMessage()


def test_tier_a_bad_mode_after_chmod_raises_hardening_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "ai_brain.db"

    real_chmod = os.chmod

    def _chmod_to_wrong_mode(path: object, mode: int, *args: object, **kwargs: object) -> None:
        real_chmod(path, 0o644)

    monkeypatch.setattr(os, "chmod", _chmod_to_wrong_mode)

    with pytest.raises(PermissionHardeningFailed):
        ensure_private_file(target)


def test_tier_a_bad_mode_after_chmod_raises_hardening_failed_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data"

    real_chmod = os.chmod

    def _chmod_to_wrong_mode(path: object, mode: int, *args: object, **kwargs: object) -> None:
        real_chmod(path, 0o755)

    monkeypatch.setattr(os, "chmod", _chmod_to_wrong_mode)

    with pytest.raises(PermissionHardeningFailed):
        ensure_private_dir(target)

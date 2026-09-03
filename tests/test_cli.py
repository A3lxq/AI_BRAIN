from __future__ import annotations

from pathlib import Path

import pytest

from athena.cli import main


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATHENA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("ATHENA_VAULT_DIR", raising=False)
    monkeypatch.delenv("ATHENA_HUEY_SECRET", raising=False)


def test_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["version"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "athena" in captured.out


def test_doctor_command_runs_and_prints_report(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["doctor"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "vault_root" in captured.out
    assert "Overall:" in captured.out


def test_doctor_command_exit_code_reflects_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATHENA_VAULT_DIR", str(tmp_path / "nonexistent-vault"))
    exit_code = main(["doctor"])
    assert exit_code == 1


def test_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit):
        main([])

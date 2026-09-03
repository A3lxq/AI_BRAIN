from __future__ import annotations

from pathlib import Path

from athena.config import AthenaConfig
from athena.diagnostics import run_doctor


def _config(
    tmp_path: Path, *, vault: Path | None = None, secret: str | None = None
) -> AthenaConfig:
    data_dir = tmp_path / "data"
    return AthenaConfig(
        vault_root=vault,
        data_dir=data_dir,
        db_path=data_dir / "athena.db",
        huey_db_path=data_dir / "huey.db",
        huey_serializer_secret=secret,
        secret_scanner_block_on_high_confidence=False,
        qdrant_url="http://127.0.0.1:6333",
        log_level="INFO",
    )


def test_doctor_reports_ok_overall_with_full_config(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    config = _config(tmp_path, vault=vault, secret="a-real-secret")  # noqa: S106 — test fixture

    report = run_doctor(config)

    names = {check.name: check for check in report.checks}
    assert names["vault_root"].status == "ok"
    assert names["data_dir_permissions"].status == "ok"
    assert names["metadata_db_permissions"].status == "ok"
    assert names["huey_db_permissions"].status == "ok"
    assert names["huey_serializer"].status == "ok"
    assert names["secret_scanner"].status == "ok"
    assert report.exit_code == 0


def test_doctor_warns_without_vault_or_secret(tmp_path: Path) -> None:
    config = _config(tmp_path, vault=None, secret=None)

    report = run_doctor(config)

    names = {check.name: check for check in report.checks}
    assert names["vault_root"].status == "warn"
    assert names["huey_serializer"].status == "warn"
    # Warnings alone must not fail the overall exit code.
    assert report.exit_code == 0
    assert report.overall == "warn"


def test_doctor_fails_on_nonexistent_vault(tmp_path: Path) -> None:
    config = _config(tmp_path, vault=tmp_path / "does-not-exist")

    report = run_doctor(config)

    names = {check.name: check for check in report.checks}
    assert names["vault_root"].status == "fail"
    assert report.exit_code == 1


def test_doctor_never_raises_even_with_hostile_paths(tmp_path: Path) -> None:
    config = _config(tmp_path, vault=tmp_path / "also-missing", secret="")
    # Should complete and return a report, not raise, regardless of how many
    # checks fail — this is diagnostics.run_doctor's own documented contract.
    report = run_doctor(config)
    assert report.overall in {"ok", "warn", "fail"}

"""The `ai-brain doctor` diagnostics command.

Exercises every P0 security module end-to-end against the live
configuration, per `docs/ROADMAP.md` Phase 1's "diagnostics/doctor command"
deliverable. A failing check here means the corresponding threat-model
mitigation (`docs/SECURITY_MODEL.md`) is not actually in effect, not merely
that a nice-to-have is missing — see each check's message for what's at
stake and how to fix it.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from huey import SqliteHuey
from huey.serializer import SignedSerializer
from qdrant_client import QdrantClient

from ai_brain.config import AIBrainConfig
from ai_brain.db.connection import open_connection
from ai_brain.db.migrate import (
    DEFAULT_MIGRATIONS_DIR,
    MigrationChecksumMismatchError,
    SchemaStatus,
    check_schema_status,
)
from ai_brain.hardening.permissions import (
    PermissionHardeningFailed,
    ensure_private_dir,
    ensure_private_file,
)
from ai_brain.hardening.serializer import SerializerMisconfigured, assert_safe_job_serializer
from ai_brain.safety.paths import VaultRoot, VaultRootConfigError
from ai_brain.security.secrets import scan_note_for_secrets

Status = Literal["ok", "warn", "fail"]

_STATUS_RANK: dict[Status, int] = {"ok": 0, "warn": 1, "fail": 2}


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: Status
    message: str


@dataclass(frozen=True)
class DoctorReport:
    checks: list[DoctorCheck] = field(default_factory=list)

    @property
    def overall(self) -> Status:
        if not self.checks:
            return "ok"
        return max((check.status for check in self.checks), key=_STATUS_RANK.__getitem__)

    @property
    def exit_code(self) -> int:
        return 1 if self.overall == "fail" else 0


def _check_python_version() -> DoctorCheck:
    # `requires-python = ">=3.12"` in pyproject.toml already prevents pip from
    # installing AI_BRAIN on an older interpreter, so this is informational,
    # not a real pass/fail gate.
    return DoctorCheck("python_version", "ok", f"Python {sys.version.split()[0]}")


def _check_vault_root(config: AIBrainConfig) -> DoctorCheck:
    if config.vault_root is None:
        return DoctorCheck(
            "vault_root",
            "warn",
            "AI_BRAIN_VAULT_DIR is not set — no vault is configured yet",
        )
    try:
        root = VaultRoot.initialize(config.vault_root)
    except VaultRootConfigError as exc:
        return DoctorCheck("vault_root", "fail", str(exc))
    return DoctorCheck("vault_root", "ok", f"vault root resolves to {root.path}")


def _check_data_dir(config: AIBrainConfig) -> DoctorCheck:
    try:
        ensure_private_dir(config.data_dir)
    except PermissionHardeningFailed as exc:
        return DoctorCheck("data_dir_permissions", "fail", str(exc))
    return DoctorCheck(
        "data_dir_permissions", "ok", f"{config.data_dir} exists with mode 0700"
    )


def _check_db_file_permissions(name: str, path: Path) -> DoctorCheck:
    try:
        ensure_private_file(path)
    except PermissionHardeningFailed as exc:
        return DoctorCheck(name, "fail", str(exc))
    return DoctorCheck(name, "ok", f"{path} exists with mode 0600")


def _check_huey_serializer(config: AIBrainConfig) -> DoctorCheck:
    if not config.huey_serializer_secret:
        return DoctorCheck(
            "huey_serializer",
            "warn",
            "AI_BRAIN_HUEY_SECRET is not set — job payloads would be unauthenticated "
            "pickle (ADR-0002) once the job queue is wired up",
        )
    huey = SqliteHuey(
        name="ai-brain-doctor-check",
        filename=str(config.huey_db_path),
        serializer=SignedSerializer(secret=config.huey_serializer_secret),
    )
    try:
        assert_safe_job_serializer(huey)
    except SerializerMisconfigured as exc:
        return DoctorCheck("huey_serializer", "fail", str(exc))
    return DoctorCheck("huey_serializer", "ok", "SignedSerializer configured with a secret")


def _check_secret_scanner() -> DoctorCheck:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as handle:
        handle.write("This is an ordinary note with no secrets in it.\n")
        probe_path = Path(handle.name)
    try:
        result = scan_note_for_secrets(probe_path, timeout_s=5.0)
    finally:
        probe_path.unlink(missing_ok=True)

    if result.status == "scan_error":
        return DoctorCheck("secret_scanner", "fail", f"self-test scan failed: {result.error}")
    return DoctorCheck(
        "secret_scanner", "ok", f"detect-secrets {result.scanner_version} operational"
    )


async def _read_schema_status(config: AIBrainConfig) -> SchemaStatus:
    async with open_connection(config.db_path) as conn:
        return await check_schema_status(conn, DEFAULT_MIGRATIONS_DIR)


def _check_schema_version(config: AIBrainConfig) -> DoctorCheck:
    try:
        status = asyncio.run(_read_schema_status(config))
    except MigrationChecksumMismatchError as exc:
        return DoctorCheck("schema_version", "fail", str(exc))

    if not status.up_to_date:
        return DoctorCheck(
            "schema_version",
            "warn",
            f"database schema is at version {status.current_version}, "
            f"{status.highest_available_version} available — run `ai-brain migrate`",
        )
    return DoctorCheck(
        "schema_version", "ok", f"database schema is up to date (version {status.current_version})"
    )


def _check_qdrant_reachable(config: AIBrainConfig) -> DoctorCheck:
    # warn, not fail: Qdrant is a separate deployment concern from AI_BRAIN's
    # own process health (design doc §6), matching bwrap_available/
    # docker_available's own warn-level, optional-dependency posture below.
    try:
        QdrantClient(url=config.qdrant_url, timeout=3).get_collections()
    except Exception as exc:
        return DoctorCheck(
            "qdrant_reachable", "warn", f"Qdrant unreachable at {config.qdrant_url}: {exc}"
        )
    return DoctorCheck("qdrant_reachable", "ok", f"Qdrant reachable at {config.qdrant_url}")


def _check_external_tool(name: str, binary: str, *, required: bool) -> DoctorCheck:
    found = shutil.which(binary)
    if found:
        return DoctorCheck(name, "ok", f"{binary} found at {found}")
    status: Status = "fail" if required else "warn"
    return DoctorCheck(name, status, f"{binary} not found on PATH")


def run_doctor(config: AIBrainConfig) -> DoctorReport:
    """Run every diagnostic check and return a complete report.

    Never raises: every check function catches its own module's specific
    exceptions and turns them into a `DoctorCheck`, so one broken subsystem
    is reported clearly rather than crashing the whole command.
    """
    checks = [
        _check_python_version(),
        _check_vault_root(config),
        _check_data_dir(config),
        _check_db_file_permissions("metadata_db_permissions", config.db_path),
        _check_db_file_permissions("huey_db_permissions", config.huey_db_path),
        _check_schema_version(config),
        _check_huey_serializer(config),
        _check_secret_scanner(),
        _check_qdrant_reachable(config),
        _check_external_tool("git_available", "git", required=True),
        _check_external_tool("bwrap_available", "bwrap", required=False),
        _check_external_tool("systemctl_available", "systemctl", required=False),
        _check_external_tool("docker_available", "docker", required=False),
    ]
    return DoctorReport(checks=checks)

"""Smoke tests for athena.worker.

worker.py constructs its module-level `huey` instance (and hard-fails via
assert_safe_job_serializer) at import time, keyed off `load_config()`'s
environment snapshot -- so every test here sets the environment first, then
imports the module fresh (evicting any cached import) rather than relying on
whatever config happened to be active when some other test last imported it.

worker.py itself never runs migrations (that's the explicit `athena
migrate` CLI step, not automatic) -- every test that calls a worker function
touching `config.db_path` must apply migrations first, exactly as a real
deployment would need to run `athena migrate` before `athena ingest
bootstrap`.
"""

from __future__ import annotations

import asyncio
import sys
from importlib import import_module
from pathlib import Path
from types import ModuleType

import aiosqlite
import pytest

from athena.db.migrate import DEFAULT_MIGRATIONS_DIR, apply_pending_migrations


def _fresh_worker_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, vault_dir: Path | None = None
) -> ModuleType:
    monkeypatch.setenv("ATHENA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ATHENA_HUEY_SECRET", "a-real-test-secret")  # noqa: S105 -- test fixture
    if vault_dir is not None:
        monkeypatch.setenv("ATHENA_VAULT_DIR", str(vault_dir))
    else:
        monkeypatch.delenv("ATHENA_VAULT_DIR", raising=False)

    sys.modules.pop("athena.worker", None)
    worker = import_module("athena.worker")

    async def _migrate() -> None:
        conn = await aiosqlite.connect(worker._config.db_path)
        try:
            await apply_pending_migrations(conn, DEFAULT_MIGRATIONS_DIR)
        finally:
            await conn.close()

    asyncio.run(_migrate())
    return worker


def test_build_huey_hard_fails_without_a_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Get a valid module imported first (so `from athena.worker import
    # build_huey` below doesn't itself trip over the module's own top-level
    # `huey = build_huey(_config)` call with no secret configured), then
    # test build_huey directly against a deliberately misconfigured object.
    _fresh_worker_module(tmp_path, monkeypatch)
    from athena.config import AthenaConfig
    from athena.hardening.serializer import SerializerMisconfigured
    from athena.worker import build_huey

    config = AthenaConfig(
        vault_root=None,
        data_dir=tmp_path,
        db_path=tmp_path / "athena.db",
        huey_db_path=tmp_path / "huey.db",
        huey_serializer_secret=None,
        secret_scanner_block_on_high_confidence=False,
        qdrant_url="http://127.0.0.1:6333",
        log_level="INFO",
    )
    with pytest.raises(SerializerMisconfigured):
        build_huey(config)


def test_module_registers_task_and_startup_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _fresh_worker_module(tmp_path, monkeypatch)

    registered = worker.huey._registry._registry  # type: ignore[attr-defined]
    assert any(key.endswith("ingest_note_task") for key in registered)
    assert any(key.endswith("reconcile_vault_task") for key in registered)
    assert "_worker_startup" in worker.huey._startup


def test_run_bootstrap_ingests_the_configured_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "note.md").write_text("hello\n", encoding="utf-8")

    worker = _fresh_worker_module(tmp_path, monkeypatch, vault_dir=vault_dir)

    summary = worker.run_bootstrap()

    assert summary.notes_ingested == 1


def test_run_reconcile_against_configured_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "note.md").write_text("hello\n", encoding="utf-8")

    worker = _fresh_worker_module(tmp_path, monkeypatch, vault_dir=vault_dir)

    summary = worker.run_reconcile()

    assert summary.paths_scanned == 1
    assert summary.discrepancies_found == 1


def test_run_bootstrap_without_vault_configured_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _fresh_worker_module(tmp_path, monkeypatch, vault_dir=None)

    with pytest.raises(RuntimeError, match="ATHENA_VAULT_DIR"):
        worker.run_bootstrap()


def test_ingest_note_task_call_local_performs_real_ingestion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`call_local` bypasses Huey's queue and runs the task function's body
    synchronously, in-process -- the closest thing to an end-to-end
    exercise of the actual registered task without a running consumer."""
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    note_path = vault_dir / "note.md"
    note_path.write_text("hello\n", encoding="utf-8")

    worker = _fresh_worker_module(tmp_path, monkeypatch, vault_dir=vault_dir)

    worker.ingest_note_task.call_local(str(note_path), "c1", None)

    from athena.db.connection import open_connection
    from athena.db.repository import notes as notes_repo

    async def _check() -> None:
        async with open_connection(worker._config.db_path) as conn:
            row = await notes_repo.get_by_path(conn, "note.md")
            assert row is not None

    asyncio.run(_check())

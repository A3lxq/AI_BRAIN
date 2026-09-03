from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
from huey import SqliteHuey

from athena.db.migrate import DEFAULT_MIGRATIONS_DIR, apply_pending_migrations
from athena.safety.paths import VaultRoot


@pytest.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(tmp_path / "athena.db")
    await apply_pending_migrations(connection, DEFAULT_MIGRATIONS_DIR)
    yield connection
    await connection.close()


@pytest.fixture
def huey(tmp_path: Path) -> SqliteHuey:
    # Huey's SQLite storage opens a fresh connection per call -- ":memory:"
    # would give each call an empty, table-less database (verified
    # empirically). A real temp file is required.
    return SqliteHuey(name="athena-test", filename=str(tmp_path / "huey.db"))


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    return root


@pytest.fixture
def vault_root(vault_dir: Path) -> VaultRoot:
    return VaultRoot.initialize(vault_dir)

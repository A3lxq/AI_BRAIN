"""SQLite migration runner (ADR-0004): PRAGMA user_version + numbered .sql files.

`executescript()` is deliberately not used here -- verified empirically that it
auto-commits each statement individually regardless of an enclosing explicit
transaction, defeating rollback-on-failure. Each migration file is instead split
into individual statements (respecting CREATE TRIGGER ... BEGIN ... END; bodies)
and executed one at a time inside one explicit BEGIN/COMMIT/ROLLBACK.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from athena.db.connection import set_mandatory_pragmas

DEFAULT_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_TRIGGER_BLOCK_RE = re.compile(r"CREATE\s+TRIGGER\b.*?\bEND\s*;", re.IGNORECASE | re.DOTALL)


class MigrationError(Exception):
    """A migration file failed to apply. Its transaction was rolled back; the
    database's PRAGMA user_version is unchanged."""


class MigrationChecksumMismatchError(Exception):
    """An already-applied migration's on-disk content (or presence) no longer
    matches what was recorded in schema_migrations at apply time."""


@dataclass(frozen=True)
class MigrationRecord:
    version: int
    filename: str
    checksum: str
    applied_at: str


@dataclass(frozen=True)
class SchemaStatus:
    current_version: int
    highest_available_version: int

    @property
    def up_to_date(self) -> bool:
        return self.current_version >= self.highest_available_version


def _split_statements(sql: str) -> list[str]:
    """Split a migration file into individual statements. Semicolons inside a
    `CREATE TRIGGER ... BEGIN ... END;` block are treated as part of one
    statement, never split on."""
    statements: list[str] = []
    pos = 0
    for match in _TRIGGER_BLOCK_RE.finditer(sql):
        before = sql[pos : match.start()]
        statements.extend(s.strip() for s in before.split(";") if s.strip())
        statements.append(match.group(0).strip())
        pos = match.end()
    tail = sql[pos:]
    statements.extend(s.strip() for s in tail.split(";") if s.strip())
    return statements


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover_migrations(migrations_dir: Path) -> list[tuple[int, Path]]:
    """Every `NNNN_name.sql` file under `migrations_dir`, sorted by numeric prefix."""
    migrations: list[tuple[int, Path]] = []
    for path in migrations_dir.glob("*.sql"):
        prefix = path.name.split("_", 1)[0]
        if prefix.isdigit():
            migrations.append((int(prefix), path))
    migrations.sort(key=lambda item: item[0])
    return migrations


async def _table_exists(conn: aiosqlite.Connection, name: str) -> bool:
    cursor = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    )
    row = await cursor.fetchone()
    return row is not None


async def _user_version(conn: aiosqlite.Connection) -> int:
    cursor = await conn.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    return int(row[0]) if row is not None else 0


async def _verify_applied_checksums(
    conn: aiosqlite.Connection, migrations: list[tuple[int, Path]]
) -> None:
    if not await _table_exists(conn, "schema_migrations"):
        return
    cursor = await conn.execute("SELECT version, filename, checksum FROM schema_migrations")
    applied_rows = await cursor.fetchall()
    by_version = dict(migrations)
    for version, filename, recorded_checksum in applied_rows:
        path = by_version.get(version)
        if path is None:
            raise MigrationChecksumMismatchError(
                f"migration {version} ({filename}) was previously applied but its file "
                "no longer exists under the migrations directory"
            )
        actual_checksum = _checksum(path)
        if actual_checksum != recorded_checksum:
            raise MigrationChecksumMismatchError(
                f"migration {version} ({filename}) has been modified since it was "
                f"applied: recorded checksum {recorded_checksum}, on-disk checksum "
                f"{actual_checksum}"
            )


async def _applied_records(conn: aiosqlite.Connection) -> list[MigrationRecord]:
    if not await _table_exists(conn, "schema_migrations"):
        return []
    cursor = await conn.execute(
        "SELECT version, filename, checksum, applied_at FROM schema_migrations ORDER BY version"
    )
    rows = await cursor.fetchall()
    return [
        MigrationRecord(version=row[0], filename=row[1], checksum=row[2], applied_at=row[3])
        for row in rows
    ]


async def check_schema_status(conn: aiosqlite.Connection, migrations_dir: Path) -> SchemaStatus:
    """Read-only: current `PRAGMA user_version` vs. the highest numbered
    migration file available, verifying already-applied migrations haven't
    drifted. Never applies anything -- used by `athena doctor`'s
    schema_version check. Raises MigrationChecksumMismatchError on drift,
    same as `apply_pending_migrations`.
    """
    await set_mandatory_pragmas(conn)
    migrations = discover_migrations(migrations_dir)
    await _verify_applied_checksums(conn, migrations)
    current_version = await _user_version(conn)
    highest_available_version = migrations[-1][0] if migrations else 0
    return SchemaStatus(
        current_version=current_version, highest_available_version=highest_available_version
    )


async def apply_pending_migrations(
    conn: aiosqlite.Connection, migrations_dir: Path
) -> list[MigrationRecord]:
    """Apply every migration in `migrations_dir` newer than the DB's current
    PRAGMA user_version, in strict numeric order, each in its own transaction.

    Raises MigrationChecksumMismatchError before applying anything if an
    already-applied migration's file has drifted. Raises MigrationError (with
    the failing migration's transaction rolled back, user_version unchanged) if
    a pending migration fails to apply -- nothing further is attempted.

    Returns every MigrationRecord now applied, including ones applied in a
    previous run.
    """
    await set_mandatory_pragmas(conn)
    migrations = discover_migrations(migrations_dir)
    await _verify_applied_checksums(conn, migrations)

    current_version = await _user_version(conn)

    for version, path in migrations:
        if version <= current_version:
            continue

        checksum = _checksum(path)
        statements = _split_statements(path.read_text(encoding="utf-8"))
        applied_at = datetime.now(UTC).isoformat()

        await conn.execute("BEGIN")
        try:
            for statement in statements:
                await conn.execute(statement)
            await conn.execute(
                "INSERT INTO schema_migrations (version, filename, checksum, applied_at) "
                "VALUES (?, ?, ?, ?)",
                (version, path.name, checksum, applied_at),
            )
            await conn.execute(f"PRAGMA user_version = {version}")
            await conn.commit()
        except Exception as exc:
            await conn.rollback()
            raise MigrationError(f"migration {version} ({path.name}) failed to apply") from exc

    return await _applied_records(conn)

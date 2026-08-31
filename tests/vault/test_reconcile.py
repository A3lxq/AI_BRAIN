from __future__ import annotations

from pathlib import Path

import aiosqlite
from huey import SqliteHuey

from ai_brain.db.repository import notes as notes_repo
from ai_brain.safety.paths import VaultRoot
from ai_brain.vault.ingest import ingest_note
from ai_brain.vault.reconcile import reconcile_vault


def _write(vault_dir: Path, relative: str, text: str = "content\n") -> Path:
    path = vault_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


async def test_reconcile_ingests_a_file_the_watcher_missed(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    _write(vault_dir, "missed.md")

    summary = await reconcile_vault(conn, huey, vault_root)

    assert summary.discrepancies_found == 1
    assert summary.paths_scanned == 1
    assert await notes_repo.get_by_path(conn, "missed.md") is not None


async def test_reconcile_is_a_true_noop_when_index_matches_disk(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    path = _write(vault_dir, "already-current.md")
    await ingest_note(conn, huey, vault_root, str(path), correlation_id="c1")

    summary = await reconcile_vault(conn, huey, vault_root)

    assert summary.paths_scanned == 1
    assert summary.discrepancies_found == 0


async def test_reconcile_detects_note_missing_from_disk(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    path = _write(vault_dir, "will-vanish.md")
    result = await ingest_note(conn, huey, vault_root, str(path), correlation_id="c1")
    path.unlink()

    summary = await reconcile_vault(conn, huey, vault_root)

    assert summary.discrepancies_found == 1
    row = await notes_repo.get_by_path(conn, "will-vanish.md")
    assert row is not None
    assert row.id == result.note_id
    assert row.deleted_at is not None


async def test_reconcile_detects_hash_mismatch(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    path = _write(vault_dir, "changes.md", "version one\n")
    await ingest_note(conn, huey, vault_root, str(path), correlation_id="c1")
    path.write_text("version two\n", encoding="utf-8")

    summary = await reconcile_vault(conn, huey, vault_root)

    assert summary.discrepancies_found == 1
    row = await notes_repo.get_by_path(conn, "changes.md")
    assert row is not None


async def test_reconcile_completed_event_has_correct_summary_counts(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    _write(vault_dir, "a.md")
    _write(vault_dir, "b.md")

    summary = await reconcile_vault(conn, huey, vault_root)

    cursor = await conn.execute(
        "SELECT payload_json FROM events WHERE event_type = 'reconciliation.completed' "
        "AND correlation_id = ?",
        (summary.correlation_id,),
    )
    row = await cursor.fetchone()
    assert row is not None

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_type = 'reconciliation.discrepancy_found' "
        "AND correlation_id = ?",
        (summary.correlation_id,),
    )
    assert (await cursor.fetchone())[0] == summary.discrepancies_found == 2

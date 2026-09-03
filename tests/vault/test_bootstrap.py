from __future__ import annotations

from pathlib import Path

import aiosqlite
from huey import SqliteHuey

from athena.db.repository import notes as notes_repo
from athena.safety.paths import VaultRoot
from athena.vault.bootstrap import bootstrap_ingest_vault, iter_markdown_files


def _write(vault_dir: Path, relative: str, text: str = "content\n") -> Path:
    path = vault_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


async def test_bootstrap_ingests_every_markdown_file(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    _write(vault_dir, "a.md")
    _write(vault_dir, "sub/b.md")
    _write(vault_dir, "not-markdown.txt")

    summary = await bootstrap_ingest_vault(conn, huey, vault_root)

    assert summary.notes_ingested == 2
    assert summary.notes_failed == 0
    assert await notes_repo.get_by_path(conn, "a.md") is not None
    assert await notes_repo.get_by_path(conn, "sub/b.md") is not None
    assert await notes_repo.get_by_path(conn, "not-markdown.txt") is None


async def test_bootstrap_excludes_git_and_obsidian_dirs(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    _write(vault_dir, ".git/COMMIT_EDITMSG.md")
    _write(vault_dir, ".obsidian/plugins/foo/data.md")
    _write(vault_dir, "real.md")

    summary = await bootstrap_ingest_vault(conn, huey, vault_root)

    assert summary.notes_ingested == 1
    assert await notes_repo.get_by_path(conn, "real.md") is not None


async def test_bootstrap_skips_symlinked_files(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    target = _write(vault_dir, "real.md")
    link = vault_dir / "link.md"
    link.symlink_to(target)

    summary = await bootstrap_ingest_vault(conn, huey, vault_root)

    assert summary.notes_ingested == 1
    assert await notes_repo.get_by_path(conn, "link.md") is None


async def test_bootstrap_is_a_fast_noop_on_second_run(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    _write(vault_dir, "a.md")
    _write(vault_dir, "b.md")

    first = await bootstrap_ingest_vault(conn, huey, vault_root)
    second = await bootstrap_ingest_vault(conn, huey, vault_root)

    assert first.notes_ingested == 2
    assert second.notes_ingested == 0
    assert second.outcome_counts.get("noop") == 2

    cursor = await conn.execute("SELECT COUNT(*) FROM notes")
    assert (await cursor.fetchone())[0] == 2


async def test_bootstrap_records_a_research_job_and_completion_event(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    _write(vault_dir, "a.md")

    summary = await bootstrap_ingest_vault(conn, huey, vault_root)

    cursor = await conn.execute(
        "SELECT job_type, status FROM research_jobs WHERE huey_task_id = ?",
        (f"bootstrap:{summary.correlation_id}",),
    )
    row = await cursor.fetchone()
    assert row == ("ingestion", "succeeded")

    cursor = await conn.execute(
        "SELECT payload_json FROM events WHERE event_type = 'ingestion.job_completed' "
        "AND correlation_id = ?",
        (summary.correlation_id,),
    )
    assert (await cursor.fetchone()) is not None


def test_iter_markdown_files_excludes_dotdirs_and_nonmd(vault_dir: Path) -> None:
    _write(vault_dir, "keep.md")
    _write(vault_dir, ".git/x.md")
    _write(vault_dir, "skip.txt")

    root = VaultRoot.initialize(vault_dir)
    found = {p.relative_to(vault_dir).as_posix() for p in iter_markdown_files(root)}

    assert found == {"keep.md"}

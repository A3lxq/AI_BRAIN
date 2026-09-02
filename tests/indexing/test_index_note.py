from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import aiosqlite
from huey import SqliteHuey
from qdrant_client import QdrantClient

from ai_brain.db.repository import notes as notes_repo
from ai_brain.indexing.index_note import index_note
from ai_brain.safety.paths import VaultRoot
from ai_brain.vault.ingest import ingest_note


def _write(vault_dir: Path, relative: str, text: str) -> Path:
    path = vault_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


async def _ingest(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, path: Path
) -> int:
    result = await ingest_note(conn, huey, vault_root, str(path), correlation_id="ingest-c1")
    assert result.note_id is not None
    return result.note_id


async def test_index_note_creates_chunks_and_marks_current(
    conn: aiosqlite.Connection,
    huey: SqliteHuey,
    vault_root: VaultRoot,
    vault_dir: Path,
    qdrant_client: QdrantClient,
) -> None:
    path = _write(
        vault_dir, "a.md", "This is the first paragraph.\n\nThis is the second paragraph.\n"
    )
    note_id = await _ingest(conn, huey, vault_root, path)

    row = await notes_repo.get_by_id(conn, note_id)
    assert row is not None
    assert row.index_state == "stale"

    result = await index_note(
        conn, qdrant_client, vault_root, note_id, correlation_id="c1", causation_id=None
    )

    assert result.outcome == "indexed"
    assert result.chunk_count > 0

    row = await notes_repo.get_by_id(conn, note_id)
    assert row is not None
    assert row.index_state == "current"
    assert row.last_index_error is None
    assert row.chunk_count == result.chunk_count
    assert row.last_indexed_at is not None

    cursor = await conn.execute(
        "SELECT chunk_index, qdrant_point_id, embedding_model_version FROM chunks "
        "WHERE note_id = ? ORDER BY chunk_index",
        (note_id,),
    )
    chunk_rows = await cursor.fetchall()
    assert len(chunk_rows) == result.chunk_count
    assert [r[0] for r in chunk_rows] == list(range(len(chunk_rows)))
    for _, point_id, embedding_version in chunk_rows:
        assert point_id and not point_id.startswith("pending-")
        assert embedding_version.startswith("bge-m3@")


async def test_index_note_noop_for_deleted_note(
    conn: aiosqlite.Connection,
    huey: SqliteHuey,
    vault_root: VaultRoot,
    vault_dir: Path,
    qdrant_client: QdrantClient,
) -> None:
    path = _write(vault_dir, "a.md", "content\n")
    note_id = await _ingest(conn, huey, vault_root, path)
    path.unlink()
    await ingest_note(conn, huey, vault_root, str(path), correlation_id="c-delete")

    result = await index_note(
        conn, qdrant_client, vault_root, note_id, correlation_id="c1", causation_id=None
    )

    assert result.outcome == "noop"


async def test_index_note_noop_for_nonexistent_note(
    conn: aiosqlite.Connection,
    huey: SqliteHuey,
    vault_root: VaultRoot,
    vault_dir: Path,
    qdrant_client: QdrantClient,
) -> None:
    result = await index_note(
        conn, qdrant_client, vault_root, 99999, correlation_id="c1", causation_id=None
    )
    assert result.outcome == "noop"


async def test_reindexing_replaces_old_chunks_not_appends(
    conn: aiosqlite.Connection,
    huey: SqliteHuey,
    vault_root: VaultRoot,
    vault_dir: Path,
    qdrant_client: QdrantClient,
) -> None:
    path = _write(vault_dir, "a.md", "one paragraph of content here.\n")
    note_id = await _ingest(conn, huey, vault_root, path)
    first = await index_note(
        conn, qdrant_client, vault_root, note_id, correlation_id="c1", causation_id=None
    )

    path.write_text(
        "one paragraph of content here.\n\nand now a second, quite different paragraph.\n",
        encoding="utf-8",
    )
    await ingest_note(conn, huey, vault_root, str(path), correlation_id="c2")
    second = await index_note(
        conn, qdrant_client, vault_root, note_id, correlation_id="c2", causation_id=None
    )

    assert second.chunk_count >= first.chunk_count
    cursor = await conn.execute("SELECT COUNT(*) FROM chunks WHERE note_id = ?", (note_id,))
    assert (await cursor.fetchone())[0] == second.chunk_count


async def test_index_note_failure_marks_failed_and_reraises(
    conn: aiosqlite.Connection,
    huey: SqliteHuey,
    vault_root: VaultRoot,
    vault_dir: Path,
    qdrant_client: QdrantClient,
) -> None:
    path = _write(vault_dir, "a.md", "content that will fail to embed.\n")
    note_id = await _ingest(conn, huey, vault_root, path)

    with patch(
        "ai_brain.indexing.index_note.embed_dense",
        side_effect=RuntimeError("embedding service down"),
    ):
        try:
            await index_note(
                conn, qdrant_client, vault_root, note_id, correlation_id="c1", causation_id=None
            )
            raise AssertionError("expected RuntimeError to propagate")
        except RuntimeError:
            pass

    row = await notes_repo.get_by_id(conn, note_id)
    assert row is not None
    assert row.index_state == "failed"
    assert row.last_index_error == "embedding service down"

    cursor = await conn.execute("SELECT COUNT(*) FROM chunks WHERE note_id = ?", (note_id,))
    assert (await cursor.fetchone())[0] == 0

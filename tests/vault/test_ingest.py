from __future__ import annotations

from pathlib import Path

import aiosqlite
from huey import SqliteHuey

from athena.db.repository import notes as notes_repo
from athena.safety.paths import VaultRoot
from athena.vault.ingest import ingest_note

_AWS_EXAMPLE_KEY = "AKIAIOSFODNN7EXAMPLE"  # AWS's own published example key, not a real credential


def _write(vault_dir: Path, relative: str, text: str) -> Path:
    path = vault_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


async def _events(conn: aiosqlite.Connection, correlation_id: str) -> list[tuple[str, str]]:
    cursor = await conn.execute(
        "SELECT event_type, payload_json FROM events WHERE correlation_id = ? ORDER BY id",
        (correlation_id,),
    )
    return await cursor.fetchall()  # type: ignore[return-value]


async def test_ingest_creates_a_new_plain_note(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    _write(vault_dir, "notes/plain.md", "Just some ordinary prose.\n")

    result = await ingest_note(
        conn, huey, vault_root, str(vault_dir / "notes" / "plain.md"), correlation_id="c1"
    )

    assert result.outcome == "created"
    row = await notes_repo.get_by_path(conn, "notes/plain.md")
    assert row is not None
    assert row.origin == "imported"
    assert row.status == "draft"

    event_types = [e[0] for e in await _events(conn, "c1")]
    assert event_types == ["job.started", "vault.note_created", "job.completed"]


async def test_ingest_is_idempotent_on_unchanged_file(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    path = _write(vault_dir, "a.md", "unchanged content\n")

    first = await ingest_note(conn, huey, vault_root, str(path), correlation_id="c1")
    second = await ingest_note(conn, huey, vault_root, str(path), correlation_id="c2")

    assert first.outcome == "created"
    assert second.outcome == "noop"
    assert second.note_id == first.note_id

    cursor = await conn.execute("SELECT COUNT(*) FROM notes")
    assert (await cursor.fetchone())[0] == 1


async def test_ingest_updates_changed_content(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    path = _write(vault_dir, "a.md", "version one\n")
    first = await ingest_note(conn, huey, vault_root, str(path), correlation_id="c1")

    path.write_text("version two\n", encoding="utf-8")
    second = await ingest_note(conn, huey, vault_root, str(path), correlation_id="c2")

    assert second.outcome == "updated"
    assert second.note_id == first.note_id
    row = await notes_repo.get_by_path(conn, "a.md")
    assert row is not None
    assert row.content_hash != ""

    event_types = [e[0] for e in await _events(conn, "c2")]
    assert event_types == ["job.started", "vault.note_modified", "job.completed"]


async def test_ingest_detects_deletion(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    path = _write(vault_dir, "a.md", "will be deleted\n")
    created = await ingest_note(conn, huey, vault_root, str(path), correlation_id="c1")
    path.unlink()

    result = await ingest_note(conn, huey, vault_root, str(path), correlation_id="c2")

    assert result.outcome == "deleted"
    assert result.note_id == created.note_id
    row = await notes_repo.get_by_path(conn, "a.md")
    assert row is not None
    assert row.deleted_at is not None


async def test_ingest_noop_on_vanished_path_never_previously_indexed(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    # A path that never existed at all -- resolve_vault_path(MAYBE_EXISTING)
    # succeeds lexically, safe_path.path.exists() is False, and no note was
    # ever recorded at this path.
    result = await ingest_note(
        conn, huey, vault_root, str(vault_dir / "never-existed.md"), correlation_id="c1"
    )
    assert result.outcome == "noop"
    assert result.note_id is None


async def test_ingest_detects_move_via_content_hash(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    old_path = _write(vault_dir, "old/name.md", "identical content across the move\n")
    created = await ingest_note(conn, huey, vault_root, str(old_path), correlation_id="c1")

    new_path = vault_dir / "new" / "name.md"
    new_path.parent.mkdir(parents=True)
    old_path.rename(new_path)

    # Both watchdog-normalized signals arrive as independent ingest_note calls,
    # in either order (design doc §2.5). Simulate the "new path" one first.
    result_new = await ingest_note(conn, huey, vault_root, str(new_path), correlation_id="c2")
    assert result_new.outcome == "moved"
    assert result_new.note_id == created.note_id

    # The "old path" signal (still pending, per the watcher's two independent
    # events for a rename) must no-op, not misclassify as a fresh deletion.
    result_old = await ingest_note(conn, huey, vault_root, str(old_path), correlation_id="c3")
    assert result_old.outcome == "noop"

    row = await notes_repo.get_by_path(conn, "new/name.md")
    assert row is not None
    assert row.id == created.note_id
    assert await notes_repo.get_by_path(conn, "old/name.md") is None


async def test_ingest_move_detection_order_independence(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    """The reverse ordering of the previous test: the 'old path' (vanished)
    signal processed *before* the 'new path' signal has appeared on disk --
    design doc §2.5 requires this to be safe regardless of ordering."""
    old_path = _write(vault_dir, "old2/name.md", "identical content, reverse order\n")
    created = await ingest_note(conn, huey, vault_root, str(old_path), correlation_id="c1")

    new_path = vault_dir / "new2" / "name.md"
    new_path.parent.mkdir(parents=True)
    old_path.rename(new_path)

    result_old = await ingest_note(conn, huey, vault_root, str(old_path), correlation_id="c2")
    assert result_old.outcome == "deleted"

    result_new = await ingest_note(conn, huey, vault_root, str(new_path), correlation_id="c3")
    assert result_new.outcome == "created"
    assert result_new.note_id != created.note_id
    row_old = await notes_repo.get_by_path(conn, "old2/name.md")
    assert row_old is not None
    assert row_old.deleted_at is not None


async def test_ingest_redacts_high_confidence_secret_and_records_finding(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    text = f"Some notes about deploying.\n\naws_access_key_id = {_AWS_EXAMPLE_KEY}\n"
    path = _write(vault_dir, "secret.md", text)

    result = await ingest_note(conn, huey, vault_root, str(path), correlation_id="c1")

    assert result.outcome == "created"
    cursor = await conn.execute(
        "SELECT secret_scan_status FROM notes WHERE id = ?", (result.note_id,)
    )
    assert (await cursor.fetchone())[0] == "flagged"

    cursor = await conn.execute(
        "SELECT plugin_type, confidence, redacted FROM note_secret_findings WHERE note_id = ?",
        (result.note_id,),
    )
    findings = await cursor.fetchall()
    assert len(findings) == 1
    assert findings[0][0] == "AWSKeyDetector"
    assert findings[0][1] == "high"
    assert findings[0][2] == 1

    row = await notes_repo.get_by_path(conn, "secret.md")
    assert row is not None
    assert _AWS_EXAMPLE_KEY not in row.title  # sanity: title unaffected


async def test_ingest_allowlisted_finding_is_not_redacted_and_note_stays_clean(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    from athena.security.secrets import scan_note_for_secrets

    text = f"aws_access_key_id = {_AWS_EXAMPLE_KEY}\n"
    path = _write(vault_dir, "allowlisted.md", text)

    # Discover the real fingerprint the same way a human-review MCP tool
    # would (design doc's allowlist is fingerprint-scoped, ADR-0011 §5).
    probe = scan_note_for_secrets(path, timeout_s=5.0)
    fingerprint = probe.findings[0].secret_hash

    await conn.execute(
        "INSERT INTO secret_scan_allowlist "
        "(finding_fingerprint, note_path, plugin_type, reason, allowlisted_by, allowlisted_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (fingerprint, "allowlisted.md", "AWSKeyDetector", "known test fixture", "tester", "t0"),
    )
    await conn.commit()

    result = await ingest_note(conn, huey, vault_root, str(path), correlation_id="c1")

    assert result.outcome == "created"
    cursor = await conn.execute(
        "SELECT secret_scan_status FROM notes WHERE id = ?", (result.note_id,)
    )
    assert (await cursor.fetchone())[0] == "clean"


async def test_ingest_block_on_high_confidence_secrets_skips_note(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    text = f"aws_access_key_id = {_AWS_EXAMPLE_KEY}\n"
    path = _write(vault_dir, "blocked.md", text)

    result = await ingest_note(
        conn,
        huey,
        vault_root,
        str(path),
        correlation_id="c1",
        block_on_high_confidence_secrets=True,
    )

    assert result.outcome == "blocked"
    assert await notes_repo.get_by_path(conn, "blocked.md") is None


async def test_ingest_handles_malformed_frontmatter_gracefully(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    text = "---\nthis: [is, not, valid: yaml\n---\nbody text\n"
    path = _write(vault_dir, "malformed.md", text)

    result = await ingest_note(conn, huey, vault_root, str(path), correlation_id="c1")

    assert result.outcome == "created"
    row = await notes_repo.get_by_path(conn, "malformed.md")
    assert row is not None
    assert row.status == "draft"


async def test_ingest_recovers_frontmatter_title_and_tags(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    text = "---\ntitle: My Real Title\ntags: [rag, embeddings]\n---\nBody.\n"
    path = _write(vault_dir, "titled.md", text)

    result = await ingest_note(conn, huey, vault_root, str(path), correlation_id="c1")

    row = await notes_repo.get_by_path(conn, "titled.md")
    assert row is not None
    assert row.title == "My Real Title"
    assert set(row.tags_text.split()) == {"rag", "embeddings"}
    assert result.outcome == "created"


async def test_ingest_infers_origin_for_real_chat_export_shape(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    text = (
        "> From: https://chat.openai.com/share/abc123\n\n"
        "# you asked\n\nHello\n\n# chatgpt response\n\nHi!\n"
    )
    path = _write(vault_dir, "CHAT_GPT/conversation.md", text)

    result = await ingest_note(conn, huey, vault_root, str(path), correlation_id="c1")

    row = await notes_repo.get_by_path(conn, "CHAT_GPT/conversation.md")
    assert row is not None
    assert row.origin == "ai_generated"
    assert row.provider == "openai"
    assert result.outcome == "created"

    cursor = await conn.execute(
        "SELECT url FROM provenance_sources ps JOIN provenance p ON p.id = ps.provenance_id "
        "WHERE p.note_id = ?",
        (result.note_id,),
    )
    assert (await cursor.fetchone())[0] == "https://chat.openai.com/share/abc123"


async def test_ingest_rejects_path_traversal(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    result = await ingest_note(
        conn, huey, vault_root, "../../etc/passwd", correlation_id="c1"
    )
    assert result.outcome == "scan_error"
    assert result.note_id is None


async def test_concurrent_ingest_of_same_path_second_call_noops(
    conn: aiosqlite.Connection, huey: SqliteHuey, vault_root: VaultRoot, vault_dir: Path
) -> None:
    """huey.lock_task is fail-fast (verified against the installed API), not
    blocking -- a second call for the same path while the lock is already
    held returns noop immediately rather than waiting."""
    path = _write(vault_dir, "locked.md", "content\n")
    lock_name = f"ingest:{path}"

    with huey.lock_task(lock_name):
        result = await ingest_note(conn, huey, vault_root, str(path), correlation_id="c1")

    assert result.outcome == "noop"
    assert result.detail == "already in progress for this path"
    assert await notes_repo.get_by_path(conn, "locked.md") is None

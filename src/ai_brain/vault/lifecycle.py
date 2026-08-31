"""Note lifecycle service (docs/design/migration-runner-and-vault-ingestion.md §2.9).

The internal write-path Phase 6's MCP tools (`note_create`/`note_update`/
`note_move`/`note_delete`) will eventually call -- built now, exercised now
only by the ingestion pipeline (`ai_brain.vault.ingest`), per CLAUDE.md rule 15
(internal modules stay decoupled from MCP transport).

Explicitly not this module's job: deciding whether a mutation is authorized
(MRTR confirmation, ADR-0007, Phase 6) or writing to the vault filesystem
itself -- a future `note_create` MCP tool writes the file, then calls this
service to record it, exactly as `ingest_note` only ever reads a path that
already exists on disk by the time it calls `create_note`.
"""

from __future__ import annotations

import aiosqlite

from ai_brain.db.repository import lifecycle as lifecycle_repo
from ai_brain.db.repository import notes as notes_repo

__all__ = [
    "create_note",
    "update_note_content",
    "move_note",
    "delete_note",
    "transition_status",
]


async def create_note(
    conn: aiosqlite.Connection,
    *,
    path: str,
    title: str,
    origin: str,
    provider: str | None,
    folder: str | None,
    content_hash: str,
    created_at: str,
    changed_by: str,
) -> int:
    """Insert a new note (status defaults to 'draft' via the DB's own DEFAULT
    -- see docs/design/migration-runner-and-vault-ingestion.md §2.9 on why
    freshly-ingested legacy content starts in 'draft', not 'active') and
    record the from_status=NULL lifecycle transition. Returns the new note id.
    """
    note_id = await notes_repo.insert(
        conn,
        path=path,
        title=title,
        origin=origin,
        provider=provider,
        folder=folder,
        content_hash=content_hash,
        created_at=created_at,
    )
    await lifecycle_repo.record_transition(
        conn,
        note_id=note_id,
        from_status=None,
        to_status="draft",
        reason="ingested",
        changed_by=changed_by,
        changed_at=created_at,
    )
    return note_id


async def update_note_content(
    conn: aiosqlite.Connection, note_id: int, *, content_hash: str, updated_at: str
) -> None:
    """A content update does not itself change lifecycle `status` -- status
    is a content-lifecycle axis (draft/active/verified/...), orthogonal to
    whether the underlying text changed (DATA_MODEL.md §4)."""
    await notes_repo.update_content(conn, note_id, content_hash=content_hash, updated_at=updated_at)


async def move_note(
    conn: aiosqlite.Connection, note_id: int, *, new_path: str, updated_at: str
) -> None:
    await notes_repo.move(conn, note_id, new_path=new_path, updated_at=updated_at)


async def delete_note(conn: aiosqlite.Connection, note_id: int, *, deleted_at: str) -> None:
    """Soft-delete tombstone only -- deletion does not transition `status`
    (DATA_MODEL.md §4's soft-delete rationale: `deleted_at` and `status` are
    independent axes; a tombstoned note keeps whatever status it last had)."""
    await notes_repo.soft_delete(conn, note_id, deleted_at=deleted_at)


async def transition_status(
    conn: aiosqlite.Connection,
    note_id: int,
    *,
    from_status: str | None,
    to_status: str,
    reason: str | None,
    changed_by: str | None,
    changed_at: str,
) -> None:
    """Update `notes.status` and record the transition in one call -- the one
    function that should ever change `notes.status` post-creation, per the
    design doc §2.9. Not yet called anywhere in Phase 2 (status promotion
    from 'draft' is an explicitly deferred open item, design doc §8) -- built
    now because Phase 6's MCP tools will need exactly this."""
    await notes_repo.update_status(conn, note_id, status=to_status, updated_at=changed_at)
    await lifecycle_repo.record_transition(
        conn,
        note_id=note_id,
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        changed_by=changed_by,
        changed_at=changed_at,
    )

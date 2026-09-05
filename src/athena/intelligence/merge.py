"""Review and merge for confirmed duplicate candidates
(docs/design/knowledge-intelligence.md §2.2).

Master Spec §10 is explicit that "a high similarity score must not
automatically imply that two notes are semantically interchangeable" --
every merge here requires an explicit, separate confirming action (never a
side effect of `duplicates.scan_for_duplicates`), and `merge_notes` itself
is only reachable from a `'confirmed'` candidate, never directly from
`'pending'`.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite
from qdrant_client import QdrantClient

from athena.db.repository import duplicates as duplicates_repo
from athena.db.repository import notes as notes_repo
from athena.db.repository import provenance as provenance_repo
from athena.indexing.index_note import index_note
from athena.safety.paths import PathMode, VaultRoot, resolve_vault_path
from athena.vault.lifecycle import transition_status
from athena.vault.lifecycle import update_note_content as record_content_update

__all__ = [
    "list_pending_duplicates",
    "resolve_duplicate",
    "MergeResult",
    "merge_notes",
]

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def list_pending_duplicates(
    conn: aiosqlite.Connection,
) -> list[duplicates_repo.DuplicateCandidateRow]:
    """Every `'pending'` candidate whose both notes are still active
    (docs/design/knowledge-intelligence.md §5) -- a candidate referencing a
    note deleted since the scan ran is excluded from the review list, not
    surfaced as mergeable.
    """
    candidates = await duplicates_repo.list_by_status(conn, "pending")
    live_candidates = []
    for candidate in candidates:
        note_a = await notes_repo.get_by_id(conn, candidate.note_a_id)
        note_b = await notes_repo.get_by_id(conn, candidate.note_b_id)
        if note_a is None or note_a.deleted_at is not None:
            continue
        if note_b is None or note_b.deleted_at is not None:
            continue
        live_candidates.append(candidate)
    return live_candidates


async def resolve_duplicate(
    conn: aiosqlite.Connection,
    candidate_id: int,
    *,
    resolution: str,
    resolved_by: str,
    resolution_note: str | None = None,
) -> None:
    """Mark a candidate `'confirmed'` or `'rejected'` -- never `'merged'`
    here, since that status is only ever set by `merge_notes` itself as part
    of an actual merge, not as a resolution a caller can request directly.
    """
    if resolution not in ("confirmed", "rejected"):
        raise ValueError(f"resolution must be 'confirmed' or 'rejected', got {resolution!r}")
    candidate = await duplicates_repo.get_by_id(conn, candidate_id)
    if candidate is None:
        raise ValueError(f"no duplicate_candidates row with id={candidate_id}")
    await duplicates_repo.update_resolution(
        conn,
        candidate_id,
        status=resolution,
        resolved_at=_now(),
        resolved_by=resolved_by,
        resolution_note=resolution_note,
    )


@dataclass(frozen=True)
class MergeResult:
    kept_note_id: int
    absorbed_note_id: int
    provenance_id: int


def _find_candidate(
    candidate: duplicates_repo.DuplicateCandidateRow, keep_note_id: int, absorb_note_id: int
) -> bool:
    pair = {candidate.note_a_id, candidate.note_b_id}
    return pair == {keep_note_id, absorb_note_id}


async def merge_notes(
    conn: aiosqlite.Connection,
    qdrant_client: QdrantClient,
    vault_root: VaultRoot,
    *,
    keep_note_id: int,
    absorb_note_id: int,
    merged_by: str,
) -> MergeResult:
    """Merge `absorb_note_id` into `keep_note_id` (docs/design/
    knowledge-intelligence.md §2.2). Only reachable from a `'confirmed'`
    `duplicate_candidates` row for this exact pair -- rejected outright
    otherwise, so this can never be called as a shortcut around the
    two-step confirm-then-merge review gate.

    Ordering matters for crash-safety, mirroring `athena.indexing.index_note`'s
    own established pattern: the vault file write (the one genuinely fallible
    step -- disk full, permission error) happens first, before any database
    write commits. If it fails, this raises with nothing yet written to the
    database -- no partial state to roll back. Re-indexing the merged content
    (`index_note`) is treated as best-effort/non-blocking once the content
    merge itself has landed, matching how Phase 3/4 already treat indexing as
    separable from ingestion: a re-index failure here is logged and the merge
    still completes (the note's own `index_state='failed'` bookkeeping,
    already handled inside `index_note`, makes it naturally re-indexable
    later via `athena index bootstrap`/reconciliation) -- it does not leave
    the merge itself half-done in a way a retry would double-apply.
    """
    confirmed_candidates = await duplicates_repo.list_by_status(conn, "confirmed")
    matching = [c for c in confirmed_candidates if _find_candidate(c, keep_note_id, absorb_note_id)]
    if not matching:
        raise ValueError(
            f"no 'confirmed' duplicate_candidates row for the pair "
            f"({keep_note_id}, {absorb_note_id}) -- resolve_duplicate(...) "
            "must confirm it first"
        )
    candidate = matching[0]

    keep_note = await notes_repo.get_by_id(conn, keep_note_id)
    absorb_note = await notes_repo.get_by_id(conn, absorb_note_id)
    if keep_note is None or keep_note.deleted_at is not None:
        raise ValueError(f"keep_note_id={keep_note_id} does not resolve to an active note")
    if absorb_note is None or absorb_note.deleted_at is not None:
        raise ValueError(f"absorb_note_id={absorb_note_id} does not resolve to an active note")

    keep_safe_path = resolve_vault_path(keep_note.path, vault_root, PathMode.EXISTING)
    absorb_safe_path = resolve_vault_path(absorb_note.path, vault_root, PathMode.EXISTING)
    keep_body = keep_safe_path.path.read_text(encoding="utf-8")
    absorb_body = absorb_safe_path.path.read_text(encoding="utf-8")

    merged_body = f"{keep_body.rstrip()}\n\n## Merged from {absorb_note.path}\n\n{absorb_body}"

    # The one fallible step -- done before any database write.
    keep_safe_path.path.write_text(merged_body, encoding="utf-8")

    now = _now()
    new_content_hash = hashlib.sha256(merged_body.encode("utf-8")).hexdigest()
    await record_content_update(
        conn, keep_note_id, content_hash=new_content_hash, updated_at=now
    )

    try:
        await index_note(
            conn,
            qdrant_client,
            vault_root,
            keep_note_id,
            correlation_id=f"merge:{keep_note_id}:{absorb_note_id}",
            causation_id=None,
        )
    except Exception:
        logger.warning(
            "re-indexing the merged note_id=%s failed; content merge still "
            "completed, note is re-indexable later via `athena index "
            "bootstrap`/reconciliation",
            keep_note_id,
            exc_info=True,
        )

    await notes_repo.soft_delete(conn, absorb_note_id, deleted_at=now)
    await transition_status(
        conn,
        absorb_note_id,
        from_status=absorb_note.status,
        to_status="superseded",
        reason=f"merged into note_id={keep_note_id}",
        changed_by=merged_by,
        changed_at=now,
    )

    provenance_id = await provenance_repo.insert_activity(
        conn,
        note_id=keep_note_id,
        activity_type="merge",
        provider=None,
        model=None,
        human_edited=True,
        occurred_at=now,
        recorded_at=now,
        supersedes_note_id=absorb_note_id,
        transformation_notes=f"merged {absorb_note.path} into {keep_note.path}",
    )

    await duplicates_repo.update_resolution(
        conn,
        candidate.id,
        status="merged",
        resolved_at=now,
        resolved_by=merged_by,
        resolution_note=candidate.resolution_note,
    )

    return MergeResult(
        kept_note_id=keep_note_id, absorbed_note_id=absorb_note_id, provenance_id=provenance_id
    )

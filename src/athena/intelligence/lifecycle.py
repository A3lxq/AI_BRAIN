"""Machine-triggered lifecycle transitions (docs/design/knowledge-intelligence.md
§2.4-2.5).

Two narrow, explicitly-accepted policies on top of `athena.vault.lifecycle`'s
existing `transition_status()` (the only function that should ever change
`notes.status` post-creation, and the one this module always goes through --
no direct writes to `notes.status` here):

- `promote_on_first_index`: `'draft' -> 'active'` the first time `index_note()`
  succeeds for a note (§2.4). Idempotent -- a no-op for any status other than
  exactly `'draft'`, since this is called on every successful index, not just
  the first.
- `run_stale_sweep`: `'active'`/`'verified' -> 'stale'` for notes not updated
  in more than `stale_after_days` days, excluding notes with an unresolved
  (`status='pending'`) `duplicate_candidates` row naming them -- already
  covered by the duplicate-review queue, so not independently flagged stale
  (§2.5's explicit reasoning).

Both proposed policies were presented for explicit accept/reject as part of
this design's acceptance (CLAUDE.md rule 10) and were accepted as written.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite

from athena.db.repository import duplicates as duplicates_repo
from athena.db.repository import notes as notes_repo
from athena.vault.lifecycle import transition_status

__all__ = ["StaleSweepSummary", "promote_on_first_index", "run_stale_sweep"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def promote_on_first_index(conn: aiosqlite.Connection, note_id: int) -> None:
    """Transition a note `'draft' -> 'active'` the first time it is
    successfully indexed (docs/design/knowledge-intelligence.md §2.4).

    Called on every successful `index_note()` run, not just the first, so
    this must be -- and is -- idempotent: only a note whose *current* status
    is exactly `'draft'` is transitioned; any other status (already
    `'active'`, `'verified'`, `'stale'`, `'superseded'`, `'archived'`) is a
    silent no-op, not an error.
    """
    note = await notes_repo.get_by_id(conn, note_id)
    if note is None:
        raise ValueError(f"cannot promote unknown note_id={note_id}")

    if note.status != "draft":
        return

    await transition_status(
        conn,
        note_id,
        from_status="draft",
        to_status="active",
        reason="first successful index",
        changed_by="job:index_note",
        changed_at=_now(),
    )


@dataclass(frozen=True)
class StaleSweepSummary:
    notes_flagged: int
    notes_skipped_duplicate_pending: int


async def run_stale_sweep(
    conn: aiosqlite.Connection,
    *,
    stale_after_days: int = 180,
) -> StaleSweepSummary:
    """Flag `'active'`/`'verified'` notes not updated in more than
    `stale_after_days` days as `'stale'` (docs/design/knowledge-intelligence.md
    §2.5).

    `notes_repo.list_stale_candidates` already excludes soft-deleted notes,
    notes not currently `'active'`/`'verified'`, and notes updated at or
    after the cutoff. This function additionally excludes any candidate with
    an unresolved (`status='pending'`) `duplicate_candidates` row naming it
    as either side of the pair -- such a note is already surfaced by the
    duplicate-review queue, so stacking a stale flag on top would just be
    noise (§2.5). Only a `'pending'` duplicate candidate exempts a note;
    `'confirmed'`/`'rejected'`/`'merged'` candidates do not.
    """
    cutoff = (datetime.now(UTC) - timedelta(days=stale_after_days)).isoformat()
    candidates = await notes_repo.list_stale_candidates(conn, cutoff=cutoff)

    pending = await duplicates_repo.list_by_status(conn, "pending")
    pending_note_ids: set[int] = set()
    for row in pending:
        pending_note_ids.add(row.note_a_id)
        pending_note_ids.add(row.note_b_id)

    notes_flagged = 0
    notes_skipped_duplicate_pending = 0
    now = _now()

    for note in candidates:
        if note.id in pending_note_ids:
            notes_skipped_duplicate_pending += 1
            continue

        await transition_status(
            conn,
            note.id,
            from_status=note.status,
            to_status="stale",
            reason=f"not updated in over {stale_after_days} days",
            changed_by="job:stale_sweep",
            changed_at=now,
        )
        notes_flagged += 1

    return StaleSweepSummary(
        notes_flagged=notes_flagged,
        notes_skipped_duplicate_pending=notes_skipped_duplicate_pending,
    )

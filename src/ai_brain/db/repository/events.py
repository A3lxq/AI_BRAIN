"""Repository function for the `events` table (docs/EVENT_MODEL.md §2, §2.1; ADR-0010)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import aiosqlite


async def append_event(
    conn: aiosqlite.Connection,
    *,
    event_type: str,
    source: str,
    correlation_id: str,
    payload: dict[str, Any],
    causation_id: str | None = None,
    idempotency_key: str | None = None,
    actor: str | None = None,
    event_id: str | None = None,
    occurred_at: str | None = None,
) -> str:
    """Insert one row into `events` and return the `event_id` used.

    Mints a fresh uuid4 `event_id` and a UTC-now ISO8601 `occurred_at` when
    the caller doesn't supply them -- production callers rely on this;
    tests may supply both explicitly for deterministic envelopes.
    """
    resolved_event_id = event_id if event_id is not None else str(uuid.uuid4())
    resolved_occurred_at = (
        occurred_at if occurred_at is not None else datetime.now(UTC).isoformat()
    )
    payload_json = json.dumps(payload)

    await conn.execute(
        "INSERT INTO events (event_id, event_type, occurred_at, source, "
        "correlation_id, causation_id, idempotency_key, actor, payload_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            resolved_event_id,
            event_type,
            resolved_occurred_at,
            source,
            correlation_id,
            causation_id,
            idempotency_key,
            actor,
            payload_json,
        ),
    )
    await conn.commit()
    return resolved_event_id

"""The idempotent per-note indexing job (docs/design/indexing-pipeline.md §2.5).

The Phase 3 counterpart to `athena.vault.ingest.ingest_note` -- chained
after that job's success (see `athena.worker`), never called standalone
for a note that hasn't already been ingested. Re-derives the note's body
from disk via the identical read -> secret-scan -> redact -> parse pipeline
`ingest_note` already performed, rather than trusting a cached copy, per
ADR-0009's "re-derive truth from disk, never diff" philosophy applied one
layer up. This duplicates a small amount of logic from `ingest_note` rather
than extracting a shared helper -- a deliberate choice to avoid coupling
Phase 2's already-shipped, already-tested job to Phase 3's, not an oversight.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import aiosqlite
from qdrant_client import QdrantClient

from athena.db.repository import chunks as chunks_repo
from athena.db.repository import events as events_repo
from athena.db.repository import notes as notes_repo
from athena.db.repository import secret_findings as secret_findings_repo
from athena.indexing.chunking import chunk_note
from athena.indexing.embedding import EMBEDDING_MODEL_VERSION, embed_dense, embed_sparse
from athena.indexing.qdrant_store import delete_points_for_note, upsert_chunks
from athena.safety.content import (
    FrontmatterParseError,
    FrontmatterTooLargeError,
    NoteShape,
    ParsedNote,
    parse_note_safely,
)
from athena.safety.paths import PathMode, VaultRoot, resolve_vault_path
from athena.security.secrets import redact_high_confidence_spans, scan_note_for_secrets

__all__ = ["IndexResult", "index_note", "IndexBootstrapSummary", "index_bootstrap"]

logger = logging.getLogger(__name__)

IndexOutcome = Literal["indexed", "noop", "failed"]


@dataclass(frozen=True)
class IndexResult:
    outcome: IndexOutcome
    note_id: int
    chunk_count: int
    detail: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _folder_name_for(vault_relative_path: str) -> str:
    parts = Path(vault_relative_path).parts
    return parts[0] if len(parts) > 1 else ""


async def _reprocess_body(
    conn: aiosqlite.Connection, vault_root: VaultRoot, vault_relative_path: str
) -> ParsedNote:
    """Re-run the identical read -> secret-scan -> redact -> parse pipeline
    `ingest_note` used, so the chunked/embedded text matches exactly what
    `notes.content_hash` was computed from -- including applying the same
    allowlist, so a finding allowlisted since `ingest_note` last ran is
    treated consistently rather than re-redacted here."""
    safe_path = resolve_vault_path(vault_relative_path, vault_root, PathMode.EXISTING)
    raw_text = safe_path.path.read_text(encoding="utf-8")

    allowlist = await secret_findings_repo.get_allowlisted_hashes(conn)
    scan_result = scan_note_for_secrets(safe_path.path, timeout_s=5.0, allowlist=allowlist)
    text_for_parsing = (
        redact_high_confidence_spans(raw_text, scan_result.findings)
        if scan_result.findings
        else raw_text
    )
    folder_name = _folder_name_for(vault_relative_path)
    try:
        return parse_note_safely(text_for_parsing, folder_name=folder_name)
    except (FrontmatterTooLargeError, FrontmatterParseError):
        return ParsedNote(
            metadata={},
            body=text_for_parsing,
            shape=NoteShape.PLAIN,
            source_url=None,
            provider_hint=None,
            parse_warning="frontmatter re-parse failed during indexing",
        )


async def index_note(
    conn: aiosqlite.Connection,
    qdrant_client: QdrantClient,
    vault_root: VaultRoot,
    note_id: int,
    *,
    correlation_id: str,
    causation_id: str | None,
) -> IndexResult:
    note = await notes_repo.get_by_id(conn, note_id)
    if note is None or note.deleted_at is not None:
        return IndexResult("noop", note_id, 0, "note not found or already deleted")

    try:
        parsed = await _reprocess_body(conn, vault_root, note.path)
        chunks = chunk_note(parsed.body)

        # Embedding and the Qdrant upsert both happen *before* any SQLite
        # `chunks` row is written. If either raises, zero partial `chunks`
        # rows exist -- the failure path below finds nothing to clean up,
        # and a retry's own delete-then-reinsert (idempotency, ADR-0009)
        # starts from a clean slate. This ordering costs the `chunks.id`
        # primary key not being available for Qdrant's payload (see
        # `upsert_chunks`'s own docstring) in exchange for that guarantee.
        point_ids: list[str] = []
        if chunks:
            dense_vectors = embed_dense([c.text for c in chunks])
            sparse_vectors = embed_sparse([c.text for c in chunks])
            payload_fields = {
                "note_path": note.path,
                "tags": note.tags_text.split() if note.tags_text else [],
                "folder": note.folder or "",
                "status": note.status,
                "origin": note.origin,
                "provider": note.provider,
                "embedding_model_version": EMBEDDING_MODEL_VERSION,
            }
            delete_points_for_note(qdrant_client, note_id)
            point_ids = upsert_chunks(
                qdrant_client,
                note_id=note_id,
                chunks=chunks,
                dense_vectors=dense_vectors,
                sparse_vectors=sparse_vectors,
                payload_fields=payload_fields,
            )
        else:
            delete_points_for_note(qdrant_client, note_id)

        now = _now()
        await chunks_repo.delete_for_note(conn, note_id)
        for chunk, point_id in zip(chunks, point_ids, strict=True):
            await chunks_repo.insert(
                conn,
                note_id=note_id,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.text,
                content_hash=hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
                qdrant_point_id=point_id,
                embedding_model_version=EMBEDDING_MODEL_VERSION,
                token_count=chunk.token_count,
                created_at=now,
            )

        await notes_repo.mark_indexed(conn, note_id, chunk_count=len(chunks), indexed_at=now)

        await events_repo.append_event(
            conn,
            event_type="index.update_completed",
            source="huey_job",
            correlation_id=correlation_id,
            causation_id=causation_id,
            payload={
                "note_id": note_id,
                "path": note.path,
                "content_hash": note.content_hash,
                "chunk_count": len(chunks),
                "index_version": EMBEDDING_MODEL_VERSION,
            },
        )
        return IndexResult("indexed", note_id, len(chunks))

    except Exception as exc:
        logger.exception("indexing failed for note_id=%s", note_id)
        try:
            await notes_repo.mark_index_failed(conn, note_id, error=str(exc))
            await events_repo.append_event(
                conn,
                event_type="job.failed",
                source="huey_job",
                correlation_id=correlation_id,
                causation_id=causation_id,
                payload={"job_type": "indexing", "retry_count": 0, "last_error": str(exc)},
            )
        except Exception:
            logger.exception("failed to record index failure for note_id=%s", note_id)
        raise


@dataclass(frozen=True)
class IndexBootstrapSummary:
    notes_indexed: int
    notes_failed: int


async def index_bootstrap(
    conn: aiosqlite.Connection,
    qdrant_client: QdrantClient,
    vault_root: VaultRoot,
    *,
    correlation_id: str,
) -> IndexBootstrapSummary:
    """`athena index bootstrap` (design doc §6): a one-time pass indexing
    every note with `index_state != 'current'` -- covers the initial corpus
    Phase 2 already ingested (migration default `'stale'`) plus any
    previously-`'failed'` notes, neither of which the ingest-triggered
    chaining in `athena.worker` would otherwise ever revisit."""
    note_ids = await notes_repo.list_ids_needing_index(conn)
    notes_indexed = 0
    notes_failed = 0
    for note_id in note_ids:
        try:
            result = await index_note(
                conn, qdrant_client, vault_root, note_id,
                correlation_id=correlation_id, causation_id=None,
            )
            if result.outcome == "indexed":
                notes_indexed += 1
        except Exception:
            notes_failed += 1
            logger.exception("indexing failed for note_id=%s during index bootstrap", note_id)
    return IndexBootstrapSummary(notes_indexed=notes_indexed, notes_failed=notes_failed)

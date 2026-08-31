"""Typed async repository functions over aiosqlite, one module per table family.

This is a narrow slice of ADR-0004's full repository layer -- only what the
vault ingestion pipeline needs (docs/design/migration-runner-and-vault-ingestion.md
§2.2). Retrieval-side queries belong to Phase 3/4's own design docs.
"""

from __future__ import annotations

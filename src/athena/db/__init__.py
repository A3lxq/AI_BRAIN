"""ATHENA AI-BRAIN's SQLite metadata database access layer (ADR-0004).

Separate from Huey's own SQLite job-store file (`athena.config.huey_db_path`) --
this package only ever talks to `athena.config.db_path`.
"""

from __future__ import annotations

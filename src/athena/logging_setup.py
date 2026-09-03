"""Structured logging setup for ATHENA AI-BRAIN.

Uses stdlib `logging` with a JSON line formatter — no new dependency for
something the standard library already does adequately (ADR-0001's
small-composable-modules preference).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_RESERVED_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class JsonFormatter(logging.Formatter):
    """Formats each log record as one JSON object per line.

    Any `extra={...}` fields passed to a logging call are included verbatim,
    which is what makes this "structured": callers can attach machine-
    readable context (e.g. `path=`, `note_id=`) instead of interpolating it
    into a free-text message.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_RECORD_ATTRS
        }
        if extras:
            payload.update(extras)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", *, stream: Any = None) -> None:
    """Configure the root logger with a single JSON-line stream handler.

    Idempotent: safe to call more than once (e.g. once from the CLI entry
    point and once again in a test fixture) without duplicating handlers.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)

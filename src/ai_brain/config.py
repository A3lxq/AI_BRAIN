"""AI_BRAIN configuration loading.

Configuration is read from environment variables, with a defaults layer for
anything that has a sane, local-first default (ADR-0001, master spec §14).
No config file parser is introduced yet — `tomllib` (stdlib, 3.11+) is the
natural choice if/when a file-based config is needed, per the "small
composable modules, no dependency beyond what's needed" principle.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / ".local" / "state" / "ai-brain"


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class AIBrainConfig:
    """Resolved AI_BRAIN configuration.

    `vault_root` is intentionally `Path | None`: many diagnostics (e.g. the
    doctor command) must run and report usefully even before a vault has
    been configured, rather than failing at import/construction time.
    """

    vault_root: Path | None
    data_dir: Path
    db_path: Path
    huey_db_path: Path
    huey_serializer_secret: str | None
    secret_scanner_block_on_high_confidence: bool
    qdrant_url: str
    log_level: str

    @property
    def vault_root_configured(self) -> bool:
        return self.vault_root is not None


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> AIBrainConfig:
    """Load configuration from the environment.

    Recognized variables:
      AI_BRAIN_VAULT_DIR                 -- path to the Obsidian vault (no default)
      AI_BRAIN_DATA_DIR                  -- AI_BRAIN's own state dir (default: see DEFAULT_DATA_DIR)
      AI_BRAIN_HUEY_SECRET                -- HMAC secret for Huey's SignedSerializer, ADR-0002
      AI_BRAIN_SECRET_SCANNER_BLOCK_HIGH  -- "true" to hard-block high-confidence secret findings
                                              instead of redact-and-flag (default: false, per
                                              docs/design/pre-ingestion-secret-scanning.md §4.2)
      AI_BRAIN_QDRANT_URL                 -- Qdrant server URL (default: http://127.0.0.1:6333,
                                              matching ADR-0006's 127.0.0.1-only binding)
      AI_BRAIN_LOG_LEVEL                  -- Python logging level name (default: INFO)
    """
    data_dir = _env_path("AI_BRAIN_DATA_DIR") or DEFAULT_DATA_DIR
    return AIBrainConfig(
        vault_root=_env_path("AI_BRAIN_VAULT_DIR"),
        data_dir=data_dir,
        db_path=data_dir / "ai_brain.db",
        huey_db_path=data_dir / "huey.db",
        huey_serializer_secret=os.environ.get("AI_BRAIN_HUEY_SECRET"),
        secret_scanner_block_on_high_confidence=_env_bool(
            "AI_BRAIN_SECRET_SCANNER_BLOCK_HIGH", default=False
        ),
        qdrant_url=os.environ.get("AI_BRAIN_QDRANT_URL", "http://127.0.0.1:6333"),
        log_level=os.environ.get("AI_BRAIN_LOG_LEVEL", "INFO"),
    )

"""File-permission hardening for AI_BRAIN's on-disk artifacts.

See docs/design/storage-runtime-hardening.md Part 2 for the full design.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class PermissionHardeningFailed(Exception):
    """Raised when a post-creation stat check shows a file or directory
    still carries a mode looser than requested, despite the creation and
    chmod calls completing without raising an OS-level error.

    This is Tier A per the design's two-tier policy: AI_BRAIN's own
    bootstrap logic produced a confirmed-bad result.
    """


def ensure_private_file(path: Path, mode: int = 0o600) -> None:
    """Create path if it doesn't exist, with exactly `mode` regardless of
    the process umask. If it already exists, correct its mode if wrong.
    """
    try:
        if not path.exists():
            # Umask still masks os.open's mode argument per POSIX, so the
            # bracket here is defense-in-depth, not the sole guarantee —
            # the explicit os.chmod below is what actually guarantees `mode`.
            old_umask = os.umask(0o077)
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_WRONLY, mode)
            finally:
                os.umask(old_umask)
            os.close(fd)
            os.chmod(path, mode)
        else:
            current_mode = path.stat().st_mode & 0o777
            if current_mode != mode:
                os.chmod(path, mode)
    except OSError as exc:
        logger.critical(
            "Failed to enforce private file permissions on %s "
            "(requested mode %o): %s. Manual remediation: chmod %o %s",
            path,
            mode,
            exc,
            mode,
            path,
        )
        return

    final_mode = path.stat().st_mode & 0o777
    if final_mode != mode:
        raise PermissionHardeningFailed(
            f"{path} has mode {oct(final_mode)} after hardening; expected {oct(mode)}."
        )


def ensure_private_dir(path: Path, mode: int = 0o700) -> None:
    """Same as ensure_private_file, for the containing directory."""
    try:
        # mkdir's mode argument is also umask-affected; the bracket is
        # defense-in-depth, the explicit os.chmod below is authoritative.
        old_umask = os.umask(0o077)
        try:
            path.mkdir(mode=mode, parents=True, exist_ok=True)
        finally:
            os.umask(old_umask)
        os.chmod(path, mode)
    except OSError as exc:
        logger.critical(
            "Failed to enforce private directory permissions on %s "
            "(requested mode %o): %s. Manual remediation: chmod %o %s",
            path,
            mode,
            exc,
            mode,
            path,
        )
        return

    final_mode = path.stat().st_mode & 0o777
    if final_mode != mode:
        raise PermissionHardeningFailed(
            f"{path} has mode {oct(final_mode)} after hardening; expected {oct(mode)}."
        )

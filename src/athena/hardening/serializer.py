"""Startup assertion against Huey's unauthenticated pickle default.

See docs/design/storage-runtime-hardening.md Part 1 for the full design.
"""

from __future__ import annotations

from huey.api import Huey
from huey.serializer import Serializer, SignedSerializer


class SerializerMisconfigured(Exception):
    """Raised when a Huey instance's configured serializer is unsafe.

    Covers both Huey's literal unauthenticated ``Serializer`` default and
    any serializer type not on ATHENA AI-BRAIN's explicit allowlist.
    """


def assert_safe_job_serializer(huey: Huey) -> None:
    """Raise SerializerMisconfigured if huey.serializer is Huey's
    unauthenticated pickle default, or is not one of ATHENA AI-BRAIN's
    explicitly allow-listed serializer types. No return value on success.
    """
    serializer = huey.serializer

    # `type(x) is Serializer` (not isinstance) because isinstance would
    # also match SignedSerializer, a subclass that must be accepted.
    if type(serializer) is Serializer:
        raise SerializerMisconfigured(
            "huey.serializer is Huey's unauthenticated pickle default "
            f"({Serializer.__module__}.{Serializer.__qualname__}); "
            "configure SignedSerializer with a real, non-empty secret."
        )

    if isinstance(serializer, SignedSerializer):
        secret = getattr(serializer, "secret", None)
        if not secret:
            raise SerializerMisconfigured(
                "SignedSerializer is configured with an empty or missing secret."
            )
        return

    raise SerializerMisconfigured(
        f"huey.serializer is of unrecognized type {type(serializer)!r}; "
        "only SignedSerializer (with a non-empty secret) is allow-listed."
    )

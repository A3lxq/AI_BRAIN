"""Tests for ai_brain.hardening.serializer.assert_safe_job_serializer.

Huey 3.3.4 (the version resolved from this project's `huey>=2.5.0`
dependency pin) exposes the Huey base class as `huey.api.Huey`, not
`huey.api.BaseHuey` as named in docs/design/storage-runtime-hardening.md
Part 1 — verified by direct introspection of the installed package. All
tests below therefore construct real `huey.MemoryHuey` instances (a
concrete, lightweight `Huey` subclass requiring no filesystem or network
resources) rather than a `BaseHuey` that does not exist in this version.
"""

from __future__ import annotations

from typing import Any

import pytest
from huey import MemoryHuey
from huey.serializer import Serializer, SignedSerializer

from ai_brain.hardening.serializer import (
    SerializerMisconfigured,
    assert_safe_job_serializer,
)


class _StubHuey:
    """Minimal stand-in exposing only the `.serializer` attribute the
    guard actually reads, for pure-function-logic tests independent of
    Huey's own construction/storage machinery."""

    def __init__(self, serializer: Any) -> None:
        self.serializer = serializer


class _NoiseSerializer(Serializer):
    """A trivial Serializer subclass not on AI_BRAIN's allowlist."""


def test_real_huey_base_serializer_rejected() -> None:
    huey = MemoryHuey("test", serializer=Serializer())
    with pytest.raises(SerializerMisconfigured):
        assert_safe_job_serializer(huey)


def test_real_huey_implicit_default_rejected() -> None:
    huey = MemoryHuey("test")
    assert type(huey.serializer) is Serializer
    with pytest.raises(SerializerMisconfigured):
        assert_safe_job_serializer(huey)


def test_stub_no_serializer_kwarg_matches_real_default_shape() -> None:
    stub = _StubHuey(Serializer())
    with pytest.raises(SerializerMisconfigured):
        assert_safe_job_serializer(stub)  # type: ignore[arg-type]


def test_signed_serializer_with_real_secret_accepted() -> None:
    huey = MemoryHuey(
        "test", serializer=SignedSerializer(secret="test-secret-value")  # noqa: S106
    )
    assert_safe_job_serializer(huey)


def test_signed_serializer_empty_secret_rejected() -> None:
    signed = SignedSerializer.__new__(SignedSerializer)
    signed.secret = b""
    signed.salt = b"huey"
    signed.comp = False
    stub = _StubHuey(signed)
    with pytest.raises(SerializerMisconfigured):
        assert_safe_job_serializer(stub)  # type: ignore[arg-type]


def test_signed_serializer_none_secret_rejected() -> None:
    signed = SignedSerializer.__new__(SignedSerializer)
    signed.secret = None
    signed.salt = b"huey"
    signed.comp = False
    stub = _StubHuey(signed)
    with pytest.raises(SerializerMisconfigured):
        assert_safe_job_serializer(stub)  # type: ignore[arg-type]


def test_unallowlisted_serializer_subclass_rejected() -> None:
    huey = MemoryHuey("test", serializer=_NoiseSerializer())
    assert type(huey.serializer) is not Serializer
    with pytest.raises(SerializerMisconfigured):
        assert_safe_job_serializer(huey)

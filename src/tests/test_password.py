"""Tests for simplified password acquisition."""

from __future__ import annotations

import pytest

from file_tools.client.password import (
    PasswordRequest,
    PasswordSource,
    resolve_password,
)


def test_explicit_password() -> None:
    m = PasswordRequest(password="s3cret", allow_prompt=False).resolve()
    assert m is not None
    assert m.value == "s3cret"
    assert m.source is PasswordSource.EXPLICIT


def test_no_source_no_prompt() -> None:
    assert resolve_password(allow_prompt=False) is None


def test_material_repr_hides_secret() -> None:
    m = PasswordRequest(password="topsecret", allow_prompt=False).resolve()
    assert m is not None
    assert "topsecret" not in repr(m)
    assert "topsecret" not in str(m)

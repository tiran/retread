"""Tests for retread.backends lazy import mechanism."""

import pytest

from retread import backends


def test_getattr_requests_backend() -> None:
    cls = backends.RequestsBackend
    assert cls.__name__ == "RequestsBackend"


def test_getattr_invalid() -> None:
    with pytest.raises(AttributeError, match="NoSuchBackend"):
        backends.NoSuchBackend  # noqa: B018


def test_dir() -> None:
    names = dir(backends)
    assert "RequestsBackend" in names
    assert "AiohttpBackend" in names
    assert "Httpx2Backend" in names
    assert "Httpx2SyncBackend" in names

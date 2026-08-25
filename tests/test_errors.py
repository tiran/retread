"""Tests for retread._errors."""

from retread._errors import (
    ComparisonError,
    InvalidWheelError,
    RetreadError,
    WheelNotFoundError,
)


def test_error_hierarchy() -> None:
    assert issubclass(WheelNotFoundError, RetreadError)
    assert issubclass(ComparisonError, RetreadError)
    assert issubclass(InvalidWheelError, RetreadError)


def test_wheel_not_found_error() -> None:
    exc = WheelNotFoundError("foo-1.0-py3-none-any.whl", "https://pypi.org/simple/")
    assert exc.filename == "foo-1.0-py3-none-any.whl"
    assert exc.index == "https://pypi.org/simple/"
    assert "foo-1.0-py3-none-any.whl" in str(exc)


def test_invalid_wheel_error() -> None:
    exc = InvalidWheelError("bad.tar.gz")
    assert exc.filename == "bad.tar.gz"
    assert "bad.tar.gz" in str(exc)

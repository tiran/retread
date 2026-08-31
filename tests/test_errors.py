"""Tests for retread._errors."""

from retread._errors import (
    ComparisonError,
    InvalidWheelError,
    NoWheelsError,
    ProjectNotFoundError,
    RetreadError,
    VersionNotFoundError,
    WheelNotFoundError,
)


def test_error_hierarchy() -> None:
    assert issubclass(WheelNotFoundError, RetreadError)
    assert issubclass(ComparisonError, RetreadError)
    assert issubclass(InvalidWheelError, RetreadError)
    # The upstream-resolution errors are a family under WheelNotFoundError so
    # callers can still catch them all with ``except WheelNotFoundError``.
    assert issubclass(ProjectNotFoundError, WheelNotFoundError)
    assert issubclass(VersionNotFoundError, WheelNotFoundError)
    assert issubclass(NoWheelsError, WheelNotFoundError)


def test_wheel_not_found_error() -> None:
    exc = WheelNotFoundError("foo-1.0-py3-none-any.whl", "https://pypi.org/simple/")
    assert exc.filename == "foo-1.0-py3-none-any.whl"
    assert exc.index == "https://pypi.org/simple/"
    assert "foo-1.0-py3-none-any.whl" in str(exc)


def test_project_not_found_error() -> None:
    exc = ProjectNotFoundError("foo-1.0-py3-none-any.whl", "https://pypi.example/", "foo")
    assert exc.project == "foo"
    assert exc.index == "https://pypi.example/"
    assert "foo" in str(exc)
    assert "not found" in str(exc)


def test_version_not_found_error() -> None:
    exc = VersionNotFoundError(
        "foo-9.9-py3-none-any.whl", "https://pypi.example/", "foo", "9.9", ("1.0", "2.0")
    )
    assert exc.version == "9.9"
    assert exc.available_versions == ("1.0", "2.0")
    assert "9.9" in str(exc)
    assert "1.0, 2.0" in str(exc)


def test_no_wheels_error() -> None:
    exc = NoWheelsError("foo-1.0-py3-none-any.whl", "https://pypi.example/", "foo", "1.0")
    assert exc.version == "1.0"
    assert "source distribution" in str(exc)


def test_invalid_wheel_error() -> None:
    exc = InvalidWheelError("bad.tar.gz")
    assert exc.filename == "bad.tar.gz"
    assert "bad.tar.gz" in str(exc)

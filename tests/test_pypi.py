"""Tests for retread._pypi parsing functions."""

import json

import pypi_simple.errors
import pytest

from retread._pypi import _parse_content_type, _parse_index_page, _parse_project_page

# --- _parse_content_type ---


@pytest.mark.parametrize(
    ("value", "expected_type", "expected_params"),
    [
        ("application/json", "application/json", {}),
        ("text/html; charset=utf-8", "text/html", {"charset": "utf-8"}),
        ('text/html; charset="utf-8"', "text/html", {"charset": "utf-8"}),
        (
            "text/html; charset=utf-8; boundary=something",
            "text/html",
            {"charset": "utf-8", "boundary": "something"},
        ),
        ("Text/HTML; Charset=UTF-8", "text/html", {"charset": "UTF-8"}),
    ],
    ids=["simple", "charset", "quoted-param", "multiple-params", "case-insensitive"],
)
def test_parse_content_type(
    value: str, expected_type: str, expected_params: dict[str, str]
) -> None:
    media_type, params = _parse_content_type(value)
    assert media_type == expected_type
    assert params == expected_params


# --- _parse_index_page ---

_INDEX_JSON = json.dumps({"meta": {"api-version": "1.0"}, "projects": [{"name": "foo"}]}).encode()
_INDEX_HTML = b'<html><body><a href="/simple/foo/">foo</a></body></html>'


@pytest.mark.parametrize(
    ("content_type", "body", "serial"),
    [
        ("application/vnd.pypi.simple.v1+json", _INDEX_JSON, "12345"),
        ("text/html", _INDEX_HTML, None),
    ],
    ids=["json", "html"],
)
def test_parse_index_page(content_type: str, body: bytes, serial: str | None) -> None:
    page = _parse_index_page(content_type, body, "https://pypi.org/simple/", serial)
    assert page is not None
    if serial:
        assert page.last_serial == serial


def test_parse_index_page_unsupported() -> None:
    with pytest.raises(pypi_simple.errors.UnsupportedContentTypeError):
        _parse_index_page("application/xml", b"<xml/>", "https://pypi.org/simple/", None)


# --- _parse_project_page ---

_PROJECT_JSON = json.dumps(
    {
        "meta": {"api-version": "1.0"},
        "name": "foo",
        "files": [
            {
                "filename": "foo-1.0.tar.gz",
                "url": "https://pypi.org/packages/foo-1.0.tar.gz",
                "hashes": {"sha256": "abc123"},
            }
        ],
    }
).encode()
_PROJECT_HTML = (
    b"<html><body>"
    b'<a href="https://pypi.org/packages/foo-1.0.tar.gz#sha256=abc">foo-1.0.tar.gz</a>'
    b"</body></html>"
)


@pytest.mark.parametrize(
    ("content_type", "body", "serial"),
    [
        ("application/vnd.pypi.simple.v1+json", _PROJECT_JSON, "67890"),
        ("text/html; charset=utf-8", _PROJECT_HTML, None),
    ],
    ids=["json", "html"],
)
def test_parse_project_page(content_type: str, body: bytes, serial: str | None) -> None:
    page = _parse_project_page("foo", content_type, body, "https://pypi.org/simple/foo/", serial)
    assert page.project == "foo"
    if serial:
        assert page.last_serial == serial


def test_parse_project_page_json_has_packages() -> None:
    page = _parse_project_page(
        "foo",
        "application/vnd.pypi.simple.v1+json",
        _PROJECT_JSON,
        "https://pypi.org/simple/foo/",
        None,
    )
    assert len(page.packages) == 1


def test_parse_project_page_unsupported() -> None:
    with pytest.raises(pypi_simple.errors.UnsupportedContentTypeError):
        _parse_project_page(
            "foo", "application/xml", b"<xml/>", "https://pypi.org/simple/foo/", None
        )

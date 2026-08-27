"""Tests for bundled virtual environment detection."""

import json

from packaging.version import Version

from retread.__main__ import _print_comparison, _print_json
from retread._compare import (
    Severity,
    VenvBundle,
    WheelComparison,
    _detect_venv_bundles,
    _find_bundled_venvs,
    compare_wheels,
)

from .conftest import FakeInfo, FakeRemoteZip, make_metadata, make_record

# --- _find_bundled_venvs ---


def _infos(names):
    return {name: FakeInfo(name) for name in names}


def test_find_bundled_venvs_none():
    infos = _infos(["foo/__init__.py", "foo/lib/data.txt"])
    assert _find_bundled_venvs(infos) == []


def test_find_bundled_venvs_dot_venv():
    infos = _infos(
        [
            "foo/__init__.py",
            ".venv/lib/python3.11/site-packages/pip/__init__.py",
            ".venv/lib/python3.11/site-packages/pip/_internal.py",
        ]
    )
    assert _find_bundled_venvs(infos) == [".venv/lib/python3.11/site-packages/"]


def test_find_bundled_venvs_at_root():
    """The pattern is matched even at the start of the path."""
    infos = _infos(["lib/python3.12/site-packages/wheel/__init__.py"])
    assert _find_bundled_venvs(infos) == ["lib/python3.12/site-packages/"]


def test_find_bundled_venvs_free_threaded():
    """Free-threaded interpreter directories (python3.13t) match."""
    infos = _infos(["env/lib/python3.13t/site-packages/foo.py"])
    assert _find_bundled_venvs(infos) == ["env/lib/python3.13t/site-packages/"]


def test_find_bundled_venvs_multiple_distinct():
    infos = _infos(
        [
            ".venv/lib/python3.11/site-packages/a.py",
            "other/lib/python3.12/site-packages/b.py",
        ]
    )
    assert _find_bundled_venvs(infos) == [
        ".venv/lib/python3.11/site-packages/",
        "other/lib/python3.12/site-packages/",
    ]


def test_find_bundled_venvs_ignores_non_site_packages():
    """A lib/python3.x directory without site-packages is not a venv."""
    infos = _infos(["pkg/lib/python3.11/config/foo.py"])
    assert _find_bundled_venvs(infos) == []


def test_find_bundled_venvs_ignores_python2():
    infos = _infos(["env/lib/python2.7/site-packages/foo.py"])
    assert _find_bundled_venvs(infos) == []


# --- _detect_venv_bundles ---

_SITE_PKGS = ".venv/lib/python3.11/site-packages/"


def test_detect_venv_bundles_none():
    infos = _infos(["foo/__init__.py"])
    assert _detect_venv_bundles(infos, infos) == ()


def test_detect_venv_bundles_upstream_is_notice():
    up = _infos([f"{_SITE_PKGS}pip/__init__.py"])
    down = _infos(["foo/__init__.py"])
    assert _detect_venv_bundles(up, down) == (VenvBundle("upstream", Severity.NOTICE, _SITE_PKGS),)


def test_detect_venv_bundles_downstream_is_error():
    up = _infos(["foo/__init__.py"])
    down = _infos([f"{_SITE_PKGS}pip/__init__.py"])
    assert _detect_venv_bundles(up, down) == (
        VenvBundle("downstream", Severity.ERROR, _SITE_PKGS),
    )


def test_detect_venv_bundles_both_sides():
    """A venv present on both sides is a notice upstream, error downstream."""
    infos = _infos([f"{_SITE_PKGS}pip/__init__.py"])
    assert _detect_venv_bundles(infos, infos) == (
        VenvBundle("upstream", Severity.NOTICE, _SITE_PKGS),
        VenvBundle("downstream", Severity.ERROR, _SITE_PKGS),
    )


# --- has_errors ---


def _result(*, venv_bundles=()) -> WheelComparison:
    return WheelComparison(
        upstream="up",
        downstream="down",
        upstream_wheel="foo-1.0-py3-none-any.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
        dist="foo",
        upstream_version=Version("1.0"),
        downstream_version=Version("1.0"),
        only_upstream=(),
        only_downstream=(),
        different=(),
        identical=("foo/__init__.py",),
        venv_bundles=venv_bundles,
    )


def test_downstream_bundle_sets_has_errors():
    result = _result(venv_bundles=(VenvBundle("downstream", Severity.ERROR, _SITE_PKGS),))
    assert result.has_errors is True


def test_upstream_bundle_is_not_an_error():
    result = _result(venv_bundles=(VenvBundle("upstream", Severity.NOTICE, _SITE_PKGS),))
    assert result.has_errors is False


# --- CLI rendering ---


def test_print_comparison_shows_bundles(capsys):
    result = _result(venv_bundles=(VenvBundle("downstream", Severity.ERROR, _SITE_PKGS),))
    _print_comparison(result)
    out = capsys.readouterr().out
    assert "Bundled virtual environments (1):" in out
    assert f"[downstream] ERROR: {_SITE_PKGS}" in out


def test_print_json_shows_bundles(capsys):
    result = _result(venv_bundles=(VenvBundle("downstream", Severity.ERROR, _SITE_PKGS),))
    _print_json(result)
    data = json.loads(capsys.readouterr().out)
    assert data["venv_bundles"] == [
        {"side": "downstream", "severity": "error", "path": _SITE_PKGS}
    ]
    assert data["has_errors"] is True


# --- collapse behaviour via compare_wheels ---

_DIST_INFO = "foo-1.0.dist-info"
_WHEEL_BYTES = b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"


def _make_zip(url: str, extra: list[str]) -> FakeRemoteZip:
    """Build a FakeRemoteZip for foo-1.0 with *extra* extra files."""
    names = [
        "foo/__init__.py",
        f"{_DIST_INFO}/METADATA",
        f"{_DIST_INFO}/WHEEL",
        f"{_DIST_INFO}/RECORD",
        *extra,
    ]
    payload = {
        f"{_DIST_INFO}/METADATA": make_metadata("foo", "1.0"),
        f"{_DIST_INFO}/WHEEL": _WHEEL_BYTES,
        f"{_DIST_INFO}/RECORD": make_record(
            {n: 100 for n in names if n != f"{_DIST_INFO}/RECORD"}
        ),
    }
    return FakeRemoteZip(url, [FakeInfo(n) for n in names], payload)


def test_compare_collapses_downstream_venv_files():
    """Individual venv files never appear in the per-file diff."""
    venv = [
        f"{_SITE_PKGS}pip/__init__.py",
        f"{_SITE_PKGS}pip-26.0.1.dist-info/entry_points.txt",
        f"{_SITE_PKGS}pip/_vendor/tests/test_it.py",
    ]
    up = _make_zip("https://pypi.example/foo-1.0-py3-none-any.whl", [])
    down = _make_zip("https://rebuild.test/foo-1.0-py3-none-any.whl", venv)
    result = compare_wheels(up, down)

    diff_names = (
        {e.filename for e in result.only_upstream}
        | {e.filename for e in result.only_downstream}
        | {d.filename for d in result.different}
        | set(result.identical)
    )
    assert not any(".venv/" in name for name in diff_names)
    assert result.venv_bundles == (VenvBundle("downstream", Severity.ERROR, _SITE_PKGS),)
    assert result.has_errors is True
    assert result.is_identical is False


def test_compare_no_venv_is_identical():
    up = _make_zip("https://pypi.example/foo-1.0-py3-none-any.whl", [])
    down = _make_zip("https://rebuild.test/foo-1.0-py3-none-any.whl", [])
    result = compare_wheels(up, down)
    assert result.venv_bundles == ()
    assert result.is_identical is True
    assert result.has_errors is False


def test_compare_symmetric_venv_is_identical():
    """A venv bundled identically on both sides leaves the wheels identical."""
    venv = [f"{_SITE_PKGS}pip/__init__.py"]
    up = _make_zip("https://pypi.example/foo-1.0-py3-none-any.whl", venv)
    down = _make_zip("https://rebuild.test/foo-1.0-py3-none-any.whl", venv)
    result = compare_wheels(up, down)
    assert {b.side for b in result.venv_bundles} == {"upstream", "downstream"}
    # Wheels match each other, but downstream still reproduced the mistake.
    assert result.is_identical is True
    assert result.has_errors is True

"""Tests for bundled virtual environment detection."""

import json

from retread import (
    Analysis,
    Comparison,
    Context,
    Severity,
    VenvBundle,
)
from retread.__main__ import _print_comparison, _print_json
from retread.checker import Pool, VenvChecker

from .conftest import (
    FakeInfo,
    make_comparison,
    make_metadata,
    make_record,
    make_wheel_info,
    run_compare,
)

find_bundled_venvs = VenvChecker.find_bundled_venvs

# --- find_bundled_venvs ---


def test_find_bundled_venvs_none():
    assert find_bundled_venvs(["foo/__init__.py", "foo/lib/data.txt"]) == []


def test_find_bundled_venvs_dot_venv():
    names = [
        "foo/__init__.py",
        ".venv/lib/python3.11/site-packages/pip/__init__.py",
        ".venv/lib/python3.11/site-packages/pip/_internal.py",
    ]
    assert find_bundled_venvs(names) == [".venv/lib/python3.11/site-packages/"]


def test_find_bundled_venvs_at_root():
    """The pattern is matched even at the start of the path."""
    names = ["lib/python3.12/site-packages/wheel/__init__.py"]
    assert find_bundled_venvs(names) == ["lib/python3.12/site-packages/"]


def test_find_bundled_venvs_free_threaded():
    """Free-threaded interpreter directories (python3.13t) match."""
    names = ["env/lib/python3.13t/site-packages/foo.py"]
    assert find_bundled_venvs(names) == ["env/lib/python3.13t/site-packages/"]


def test_find_bundled_venvs_multiple_distinct():
    names = [
        ".venv/lib/python3.11/site-packages/a.py",
        "other/lib/python3.12/site-packages/b.py",
    ]
    assert find_bundled_venvs(names) == [
        ".venv/lib/python3.11/site-packages/",
        "other/lib/python3.12/site-packages/",
    ]


def test_find_bundled_venvs_ignores_non_site_packages():
    """A lib/python3.x directory without site-packages is not a venv."""
    assert find_bundled_venvs(["pkg/lib/python3.11/config/foo.py"]) == []


def test_find_bundled_venvs_ignores_python2():
    assert find_bundled_venvs(["env/lib/python2.7/site-packages/foo.py"]) == []


# --- VenvChecker ---

_SITE_PKGS = ".venv/lib/python3.11/site-packages/"


def _detect_venv_bundles(up_names, down_names):
    """Run VenvChecker on two wheels and return the resulting venv bundles."""
    up = make_wheel_info("foo-1.0-py3-none-any.whl", [FakeInfo(n) for n in up_names], source="up")
    down = make_wheel_info(
        "foo-1.0-py3-none-any.whl", [FakeInfo(n) for n in down_names], source="down"
    )
    comparison = Comparison(
        context=Context.default(), upstream=up, downstream=down, analysis=Analysis()
    )
    VenvChecker().check(comparison, Pool())
    return comparison.analysis.venv_bundles


def test_detect_venv_bundles_none():
    assert _detect_venv_bundles(["foo/__init__.py"], ["foo/__init__.py"]) == []


def test_detect_venv_bundles_upstream_is_notice():
    bundles = _detect_venv_bundles([f"{_SITE_PKGS}pip/__init__.py"], ["foo/__init__.py"])
    assert bundles == [VenvBundle("upstream", Severity.NOTICE, _SITE_PKGS)]


def test_detect_venv_bundles_downstream_is_error():
    bundles = _detect_venv_bundles(["foo/__init__.py"], [f"{_SITE_PKGS}pip/__init__.py"])
    assert bundles == [VenvBundle("downstream", Severity.ERROR, _SITE_PKGS)]


def test_detect_venv_bundles_both_sides():
    """A venv present on both sides is a notice upstream, error downstream."""
    names = [f"{_SITE_PKGS}pip/__init__.py"]
    assert _detect_venv_bundles(names, names) == [
        VenvBundle("upstream", Severity.NOTICE, _SITE_PKGS),
        VenvBundle("downstream", Severity.ERROR, _SITE_PKGS),
    ]


# --- has_errors ---


def _result(*, venv_bundles=()) -> Comparison:
    return make_comparison(
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


# --- collapse behaviour via compare() ---

_DIST_INFO = "foo-1.0.dist-info"
_WHEEL_BYTES = b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"


def _compare_venv(up_extra: list[str], down_extra: list[str]) -> Comparison:
    """Compare two foo-1.0 wheels differing only in *extra* bundled files."""

    def side(extra):
        names = [
            "foo/__init__.py",
            *extra,
            f"{_DIST_INFO}/METADATA",
            f"{_DIST_INFO}/WHEEL",
            f"{_DIST_INFO}/RECORD",
        ]
        infos = {n: FakeInfo(n) for n in names}
        record = make_record({n: 100 for n in names if n != f"{_DIST_INFO}/RECORD"})
        return infos, record

    up_infos, up_record = side(up_extra)
    down_infos, down_record = side(down_extra)
    return run_compare(
        upstream_infos=up_infos,
        downstream_infos=down_infos,
        upstream_metadata=make_metadata("foo", "1.0"),
        downstream_metadata=make_metadata("foo", "1.0"),
        upstream_wheel=_WHEEL_BYTES,
        downstream_wheel=_WHEEL_BYTES,
        upstream_record=up_record,
        downstream_record=down_record,
    )


def test_compare_collapses_downstream_venv_files():
    """Venv files are claimed as hidden entries: absent from every report."""
    venv = [
        f"{_SITE_PKGS}pip/__init__.py",
        f"{_SITE_PKGS}pip-26.0.1.dist-info/entry_points.txt",
        f"{_SITE_PKGS}pip/_vendor/tests/test_it.py",
    ]
    result = _compare_venv([], venv)

    analysis = result.analysis
    # The venv files land in only_downstream (so is_identical sees them) but
    # every one is hidden, so they never surface in the reports.
    venv_entries = [e for e in analysis.only_downstream if ".venv/" in e.filename]
    assert len(venv_entries) == len(venv)
    assert all(e.hidden for e in venv_entries)

    # Nothing venv-related appears in the identical list or the JSON report.
    assert not any(".venv/" in name for name in analysis.identical)
    data = result.to_dict()
    reported = (
        {e["filename"] for e in data["only_upstream"]}
        | {e["filename"] for e in data["only_downstream"]}
        | {d["filename"] for d in data["different"]}
        | set(data["identical"])
    )
    assert not any(".venv/" in name for name in reported)

    assert analysis.venv_bundles == [VenvBundle("downstream", Severity.ERROR, _SITE_PKGS)]
    assert result.has_errors is True
    assert result.is_identical is False


def test_compare_no_venv_is_identical():
    result = _compare_venv([], [])
    assert result.analysis.venv_bundles == []
    assert result.is_identical is True
    assert result.has_errors is False


def test_compare_symmetric_venv_is_identical():
    """A venv bundled identically on both sides leaves the wheels identical."""
    venv = [f"{_SITE_PKGS}pip/__init__.py"]
    result = _compare_venv(venv, venv)
    assert {b.side for b in result.analysis.venv_bundles} == {"upstream", "downstream"}
    # Wheels match each other, but downstream still reproduced the mistake.
    assert result.is_identical is True
    assert result.has_errors is True


def test_compare_symmetric_venv_differing_content_differs():
    """A venv at the same path but differing content makes the wheels differ.

    The venv files are compared individually (as hidden ``different`` entries),
    so a byte-level difference inside a bundled venv is no longer masked by the
    matching path prefix.
    """
    venv_file = f"{_SITE_PKGS}pip/__init__.py"
    names = [
        "foo/__init__.py",
        venv_file,
        f"{_DIST_INFO}/METADATA",
        f"{_DIST_INFO}/WHEEL",
        f"{_DIST_INFO}/RECORD",
    ]
    record = make_record({n: 100 for n in names if n != f"{_DIST_INFO}/RECORD"})
    up_infos = {n: FakeInfo(n) for n in names}
    down_infos = {n: FakeInfo(n) for n in names}
    down_infos[venv_file] = FakeInfo(venv_file, crc=12345)  # differs from upstream
    result = run_compare(
        upstream_infos=up_infos,
        downstream_infos=down_infos,
        upstream_metadata=make_metadata("foo", "1.0"),
        downstream_metadata=make_metadata("foo", "1.0"),
        upstream_wheel=_WHEEL_BYTES,
        downstream_wheel=_WHEEL_BYTES,
        upstream_record=record,
        downstream_record=record,
    )
    venv_diffs = [d for d in result.analysis.different if ".venv/" in d.filename]
    assert [d.hidden for d in venv_diffs] == [True]
    assert result.is_identical is False

"""Tests for retread.__main__ (CLI)."""

import json

import pytest
from packaging.version import Version

from retread.__main__ import (
    _diff_to_dict,
    _entry_to_dict,
    _format_label,
    _print_comparison,
    _print_json,
    main,
)
from retread._compare import (
    Classification,
    FileDiff,
    FileEntry,
    RecordMismatch,
    Severity,
    WheelComparison,
)

# --- _format_label ---


@pytest.mark.parametrize(
    ("classification", "expected"),
    [(c, f" [{c.value}]") for c in Classification],
)
def test_format_label(classification: Classification, expected: str) -> None:
    assert _format_label(classification) == expected


# --- _entry_to_dict / _diff_to_dict ---


def test_entry_to_dict() -> None:
    entry = FileEntry("foo.py", Severity.ERROR, Classification.OTHER)
    assert _entry_to_dict(entry, "upstream") == {
        "filename": "foo.py",
        "side": "upstream",
        "severity": "error",
        "classification": "other",
    }


def test_diff_to_dict() -> None:
    diff = FileDiff(
        "foo.so", 100, 200, 111, 222, Severity.EXPECTED, Classification.EXTENSION_MODULE
    )
    d = _diff_to_dict(diff)
    assert d["filename"] == "foo.so"
    assert d["upstream_size"] == 100
    assert d["downstream_size"] == 200
    assert d["severity"] == "expected"
    assert d["classification"] == "extension module"


# --- _print_comparison ---


def test_print_comparison_identical(capsys, identical_result: WheelComparison) -> None:
    _print_comparison(identical_result)
    out = capsys.readouterr().out
    assert "Wheels are identical." in out
    assert "2 files match" in out


def test_print_comparison_errors_and_notices(capsys, error_result: WheelComparison) -> None:
    _print_comparison(error_result)
    out = capsys.readouterr().out
    assert "Errors (1):" in out
    assert "  upstream only:" in out
    assert "    missing.py" in out
    assert "Notices (1):" in out
    assert "  downstream only:" in out
    assert "[dist-info]" in out
    assert "Expected (1):" in out
    assert "  different:" in out
    assert "[dist-info RECORD]" in out
    assert "ERRORS found" in out


def test_print_comparison_notice_only(capsys, notice_only_result: WheelComparison) -> None:
    _print_comparison(notice_only_result)
    out = capsys.readouterr().out
    assert "Notices (1):" in out
    assert "  upstream only:" in out
    assert "[auditwheel]" in out
    assert "Expected (1):" in out
    assert "  different:" in out
    assert "(1000 -> 2000 bytes)" in out
    assert "[extension module]" in out
    assert "OK (notices only)" in out


@pytest.mark.parametrize(
    ("up_size", "down_size", "expect_bytes"),
    [
        (1000, 2000, True),
        (1000, 1000, False),
    ],
    ids=["different-size", "same-size"],
)
def test_print_comparison_size_info(
    capsys, up_size: int, down_size: int, expect_bytes: bool
) -> None:
    result = WheelComparison(
        upstream="up",
        downstream="down",
        upstream_wheel="foo-1.0-py3-none-any.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
        dist="foo",
        upstream_version=Version("1.0"),
        downstream_version=Version("1.0"),
        only_upstream=(),
        only_downstream=(),
        different=(
            FileDiff(
                "foo.so",
                up_size,
                down_size,
                111,
                222,
                Severity.EXPECTED,
                Classification.EXTENSION_MODULE,
            ),
        ),
        identical=(),
    )
    _print_comparison(result)
    out = capsys.readouterr().out
    assert ("bytes" in out) == expect_bytes


def test_print_comparison_downstream_only(capsys) -> None:
    result = WheelComparison(
        upstream="up",
        downstream="down",
        upstream_wheel="foo-1.0-py3-none-any.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
        dist="foo",
        upstream_version=Version("1.0"),
        downstream_version=Version("1.0"),
        only_upstream=(),
        only_downstream=(FileEntry("extra.py", Severity.ERROR, Classification.OTHER),),
        different=(),
        identical=(),
    )
    _print_comparison(result)
    out = capsys.readouterr().out
    assert "Errors (1):" in out
    assert "  downstream only:" in out
    assert "    extra.py" in out
    assert "ERRORS found" in out


# --- _print_json ---


def test_print_json_errors(capsys, error_result: WheelComparison) -> None:
    _print_json(error_result)
    data = json.loads(capsys.readouterr().out)
    assert data["has_errors"] is True
    assert data["is_identical"] is False
    assert len(data["only_upstream"]) == 1
    assert data["only_upstream"][0]["severity"] == "error"
    assert data["only_upstream"][0]["side"] == "upstream"
    assert len(data["only_downstream"]) == 1
    assert data["only_downstream"][0]["side"] == "downstream"
    assert len(data["different"]) == 1


def test_print_json_notice_only(capsys, notice_only_result: WheelComparison) -> None:
    _print_json(notice_only_result)
    data = json.loads(capsys.readouterr().out)
    assert data["has_errors"] is False
    assert data["is_identical"] is False
    assert data["different"][0]["upstream_size"] == 1000
    assert data["different"][0]["downstream_size"] == 2000


# --- main argument parsing ---


def test_main_no_command() -> None:
    with pytest.raises(SystemExit, match="2"):
        main([])


def test_main_help() -> None:
    with pytest.raises(SystemExit, match="0"):
        main(["--help"])


# --- RECORD mismatches ---


def test_print_comparison_record_mismatches(capsys) -> None:
    result = WheelComparison(
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
        record_mismatches=(
            RecordMismatch("upstream", "file in ZIP but not in RECORD: extra.py"),
            RecordMismatch("downstream", "size mismatch for foo.py: RECORD says 99, ZIP says 100"),
        ),
    )
    _print_comparison(result)
    out = capsys.readouterr().out
    assert "RECORD mismatches (2):" in out
    assert "[upstream] file in ZIP but not in RECORD: extra.py" in out
    assert "[downstream] size mismatch for foo.py: RECORD says 99, ZIP says 100" in out


def test_print_comparison_record_mismatches_with_diffs(capsys) -> None:
    result = WheelComparison(
        upstream="up",
        downstream="down",
        upstream_wheel="foo-1.0-py3-none-any.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
        dist="foo",
        upstream_version=Version("1.0"),
        downstream_version=Version("1.0"),
        only_upstream=(),
        only_downstream=(),
        different=(
            FileDiff(
                "foo-1.0.dist-info/RECORD",
                500,
                600,
                111,
                222,
                Severity.EXPECTED,
                Classification.RECORD,
            ),
        ),
        identical=(),
        record_mismatches=(RecordMismatch("upstream", "file in ZIP but not in RECORD: extra.py"),),
    )
    _print_comparison(result)
    out = capsys.readouterr().out
    assert "RECORD mismatches (1):" in out
    assert "[upstream] file in ZIP but not in RECORD: extra.py" in out


def test_print_json_record_mismatches(capsys) -> None:
    result = WheelComparison(
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
        record_mismatches=(RecordMismatch("upstream", "file in ZIP but not in RECORD: extra.py"),),
    )
    _print_json(result)
    data = json.loads(capsys.readouterr().out)
    assert "record_mismatches" in data
    assert len(data["record_mismatches"]) == 1
    assert data["record_mismatches"][0]["side"] == "upstream"
    assert "extra.py" in data["record_mismatches"][0]["message"]


def test_print_json_no_record_mismatches(capsys) -> None:
    result = WheelComparison(
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
    )
    _print_json(result)
    data = json.loads(capsys.readouterr().out)
    assert data["record_mismatches"] == []

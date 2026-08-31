"""Tests for retread.__main__ (CLI)."""

import json

import pytest

from retread import (
    Classification,
    Comparison,
    FileDiff,
    FileEntry,
    MetadataFieldDiff,
    RecordMismatch,
    ResolutionMismatch,
    Severity,
)
from retread.__main__ import (
    _format_label,
    _print_comparison,
    _print_json,
)

from .conftest import make_comparison

# --- _format_label ---


@pytest.mark.parametrize(
    ("classification", "expected"),
    [(c, f" [{c.value}]") for c in Classification],
)
def test_format_label(classification: Classification, expected: str) -> None:
    assert _format_label(classification) == expected


# --- _print_comparison ---


def test_print_comparison_identical(capsys, identical_result: Comparison) -> None:
    _print_comparison(identical_result)
    out = capsys.readouterr().out
    assert "Wheels are identical." in out
    assert "2 files match" in out


def test_print_comparison_errors_and_notices(capsys, error_result: Comparison) -> None:
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


def test_print_comparison_notice_only(capsys, notice_only_result: Comparison) -> None:
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
    result = make_comparison(
        upstream="up",
        downstream="down",
        upstream_wheel="foo-1.0-py3-none-any.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
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
    result = make_comparison(
        upstream="up",
        downstream="down",
        upstream_wheel="foo-1.0-py3-none-any.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
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


def test_print_json_errors(capsys, error_result: Comparison) -> None:
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


def test_print_json_notice_only(capsys, notice_only_result: Comparison) -> None:
    _print_json(notice_only_result)
    data = json.loads(capsys.readouterr().out)
    assert data["has_errors"] is False
    assert data["is_identical"] is False
    assert data["different"][0]["upstream_size"] == 1000
    assert data["different"][0]["downstream_size"] == 2000


# --- metadata field diffs ---


def _metadata_diff_result() -> Comparison:
    diff = FileDiff(
        "foo-1.0.dist-info/METADATA", 100, 200, 111, 222, Severity.ERROR, Classification.METADATA
    )
    return make_comparison(
        upstream="up",
        downstream="down",
        upstream_wheel="foo-1.0-py3-none-any.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
        only_upstream=(),
        only_downstream=(),
        different=(diff,),
        identical=(),
        metadata_field_diffs=(
            MetadataFieldDiff("Requires-Dist", ("bar>=1.0",), ("bar>=2.0",)),
            MetadataFieldDiff("Provides-Extra", ("docs",), ()),
        ),
    )


def test_print_json_metadata_field_diffs(capsys) -> None:
    _print_json(_metadata_diff_result())
    data = json.loads(capsys.readouterr().out)
    assert data["metadata_field_diffs"] == [
        {
            "field": "Requires-Dist",
            "only_upstream": ["bar>=1.0"],
            "only_downstream": ["bar>=2.0"],
            "ignored": False,
        },
        {
            "field": "Provides-Extra",
            "only_upstream": ["docs"],
            "only_downstream": [],
            "ignored": False,
        },
    ]


def test_print_comparison_metadata_field_diffs(capsys) -> None:
    _print_comparison(_metadata_diff_result())
    out = capsys.readouterr().out
    assert "METADATA field differences:" in out
    # Grouped by side, then by field, then values.
    up = out.index("upstream only:")
    down = out.index("downstream only:")
    assert up < down
    assert out.index("Requires-Dist:", up) < down
    assert out.index("bar>=1.0", up) < down
    assert "docs" in out[up:down]
    assert out.index("bar>=2.0", down) > down


# --- main argument parsing ---


def test_main_no_command(invoke_cli) -> None:
    result = invoke_cli([])
    assert result.exit_code == 2


def test_main_help(invoke_cli) -> None:
    result = invoke_cli(["--help"])
    assert result.exit_code == 0


# --- RECORD mismatches ---


def test_print_comparison_record_mismatches(capsys) -> None:
    result = make_comparison(
        upstream="up",
        downstream="down",
        upstream_wheel="foo-1.0-py3-none-any.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
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
    result = make_comparison(
        upstream="up",
        downstream="down",
        upstream_wheel="foo-1.0-py3-none-any.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
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


def _resolution_result() -> Comparison:
    return make_comparison(
        upstream_wheel="google_crc32c-1.8.0-cp312-cp312-manylinux_2_17_x86_64.whl",
        downstream_wheel="google_crc32c-1.8.0-1-py3-none-any.whl",
        identical=("google_crc32c/__init__.py",),
        resolution_mismatches=(
            ResolutionMismatch(
                message=(
                    "no upstream wheel matches the downstream tags (py3-none-any); "
                    "compared against google_crc32c-1.8.0-cp312-cp312-manylinux_2_17_x86_64.whl"
                ),
                downstream_tags=("py3-none-any",),
                upstream_tags=("cp312-cp312-manylinux_2_17_x86_64",),
            ),
        ),
    )


def test_print_comparison_resolution_mismatch(capsys) -> None:
    """A fallback resolution mismatch is printed and counts as an error."""
    result = _resolution_result()
    assert result.has_errors is True
    _print_comparison(result)
    out = capsys.readouterr().out
    assert "Upstream resolution mismatches (1):" in out
    assert "no upstream wheel matches the downstream tags" in out


def test_print_json_resolution_mismatch(capsys) -> None:
    """Resolution mismatches are serialized in the JSON report."""
    result = _resolution_result()
    _print_json(result)
    data = json.loads(capsys.readouterr().out)
    assert data["has_errors"] is True
    assert len(data["resolution_mismatches"]) == 1
    mismatch = data["resolution_mismatches"][0]
    assert mismatch["downstream_tags"] == ["py3-none-any"]
    assert mismatch["upstream_tags"] == ["cp312-cp312-manylinux_2_17_x86_64"]


def test_print_json_record_mismatches(capsys) -> None:
    result = make_comparison(
        upstream="up",
        downstream="down",
        upstream_wheel="foo-1.0-py3-none-any.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
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
    result = make_comparison(
        upstream="up",
        downstream="down",
        upstream_wheel="foo-1.0-py3-none-any.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
        only_upstream=(),
        only_downstream=(),
        different=(),
        identical=("foo/__init__.py",),
    )
    _print_json(result)
    data = json.loads(capsys.readouterr().out)
    assert data["record_mismatches"] == []

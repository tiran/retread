"""Tests for retread._compare."""

import zipfile

import pytest
from packaging.version import Version

from retread._compare import (
    Classification,
    FileDiff,
    Severity,
    WheelComparison,
    _check_metadata,
    _classify_file,
    _compare,
    _is_auditwheel_lib,
    _is_shared_library,
    _is_url,
    _local_zip_infos,
    _metadata_core_match,
    _parse_name_version,
    _wheel_basename,
    compare_local_wheel,
    compare_wheels,
)
from retread._errors import InvalidWheelError

from .conftest import (
    DOWNSTREAM_URL,
    UPSTREAM_URL,
    FakeInfo,
    FakeRemoteZip,
    make_metadata,
    make_record,
    make_wheel,
)

# --- boolean helpers ---


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("foo.so", True),
        ("foo.so.1", True),
        ("libfoo.so.1.2.3", True),
        ("foo.so.0", True),
        ("foo.py", False),
        ("foo.sol", False),
        ("foo.c", False),
        ("foo", False),
    ],
)
def test_is_shared_library(filename: str, expected: bool) -> None:
    assert _is_shared_library(filename) == expected


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Pillow.libs/libpng16.so.16", True),
        ("foo.libs/bar.so", True),
        ("foo/bar.libs/baz.so", True),
        ("foo/bar.so", False),
        ("foo.lib/bar.so", False),
    ],
)
def test_is_auditwheel_lib(filename: str, expected: bool) -> None:
    assert _is_auditwheel_lib(filename) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://example.com/foo.whl", True),
        ("http://example.com/foo.whl", True),
        ("/home/user/foo.whl", False),
        ("foo.whl", False),
    ],
)
def test_is_url(value: str, expected: bool) -> None:
    assert _is_url(value) == expected


# --- _wheel_basename ---


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://example.com/path/foo-1.0-py3-none-any.whl", "foo-1.0-py3-none-any.whl"),
        (
            "https://example.com/path/foo-1.0-py3-none-any.whl?token=abc#sha256=xyz",
            "foo-1.0-py3-none-any.whl",
        ),
        ("/home/user/foo-1.0-py3-none-any.whl", "foo-1.0-py3-none-any.whl"),
        ("foo-1.0-py3-none-any.whl", "foo-1.0-py3-none-any.whl"),
    ],
)
def test_wheel_basename(source: str, expected: str) -> None:
    assert _wheel_basename(source) == expected


# --- _parse_name_version ---


@pytest.mark.parametrize(
    ("wheel_filename", "expected_name", "expected_version"),
    [
        ("foo-1.0-py3-none-any.whl", "foo", "1.0"),
        ("Pillow-12.3.0-cp312-cp312-linux_x86_64.whl", "Pillow", "12.3.0"),
        ("uv-0.12.5-1-py3-none-linux_x86_64.whl", "uv", "0.12.5"),
        ("my_cool_pkg-2.0.1-cp311-cp311-manylinux_2_28_x86_64.whl", "my_cool_pkg", "2.0.1"),
    ],
)
def test_parse_name_version(
    wheel_filename: str, expected_name: str, expected_version: str
) -> None:
    name, version = _parse_name_version(wheel_filename)
    assert name == expected_name
    assert version == expected_version


def test_parse_name_version_invalid() -> None:
    with pytest.raises(InvalidWheelError):
        _parse_name_version("not-a-wheel.tar.gz")


# --- _classify_file ---

_DIST = "foo"
_VER = "1.0"

EXPECTED = Severity.EXPECTED
NOTICE = Severity.NOTICE
ERROR = Severity.ERROR


@pytest.mark.parametrize(
    ("filename", "missing", "expected_severity", "expected_classification"),
    [
        # dist-info files
        ("foo-1.0.dist-info/RECORD", False, EXPECTED, Classification.RECORD),
        ("foo-1.0.dist-info/RECORD", True, EXPECTED, Classification.RECORD),
        ("foo-1.0.dist-info/WHEEL", False, EXPECTED, Classification.WHEEL),
        ("foo-1.0.dist-info/METADATA", False, NOTICE, Classification.METADATA),
        (
            "foo-1.0.dist-info/sboms/auditwheel.cdx.json",
            False,
            NOTICE,
            Classification.SBOM,
        ),
        ("foo-1.0.dist-info/licenses/LICENSE", False, NOTICE, Classification.LICENSE),
        ("foo-1.0.dist-info/top_level.txt", False, NOTICE, Classification.DIST_INFO),
        # data directory
        ("foo-1.0.data/scripts/mybin", False, EXPECTED, Classification.DATA_SCRIPTS),
        ("foo-1.0.data/scripts/mybin", True, ERROR, Classification.DATA_SCRIPTS),
        ("foo-1.0.data/headers/foo.h", False, NOTICE, Classification.DATA),
        ("foo-1.0.data/data/resource.dat", True, ERROR, Classification.DATA),
        # auditwheel (always NOTICE)
        ("foo.libs/libbar.so.1", False, NOTICE, Classification.AUDITWHEEL),
        ("foo.libs/libbar.so.1", True, NOTICE, Classification.AUDITWHEEL),
        # extension modules
        (
            "foo/_bar.cpython-312-x86_64-linux-gnu.so",
            False,
            EXPECTED,
            Classification.EXTENSION_MODULE,
        ),
        (
            "foo/_bar.cpython-312-x86_64-linux-gnu.so",
            True,
            ERROR,
            Classification.EXTENSION_MODULE,
        ),
        # other (always ERROR)
        ("foo/data.json", False, ERROR, Classification.OTHER),
        ("foo/data.json", True, ERROR, Classification.OTHER),
        # wrong dist name falls through to OTHER
        ("bar-2.0.dist-info/RECORD", False, ERROR, Classification.OTHER),
    ],
    ids=[
        "record-diff",
        "record-missing",
        "wheel",
        "metadata",
        "sbom",
        "license",
        "dist-info-other",
        "data-scripts-diff",
        "data-scripts-missing",
        "data-other-diff",
        "data-other-missing",
        "auditwheel-diff",
        "auditwheel-missing",
        "ext-module-diff",
        "ext-module-missing",
        "other-diff",
        "other-missing",
        "wrong-dist-name",
    ],
)
def test_classify_file(
    filename: str,
    missing: bool,
    expected_severity: Severity,
    expected_classification: Classification,
) -> None:
    severity, classification = _classify_file(filename, dist=_DIST, version=_VER, missing=missing)
    assert severity is expected_severity
    assert classification is expected_classification


# --- _metadata_core_match ---


@pytest.mark.parametrize(
    ("up_args", "down_args", "expected"),
    [
        # identical
        (("foo", "1.0", ["bar>=1.0"]), ("foo", "1.0", ["bar>=1.0"]), True),
        # different name
        (("foo", "1.0"), ("bar", "1.0"), False),
        # different version
        (("foo", "1.0"), ("foo", "2.0"), False),
        # different requires
        (("foo", "1.0", ["bar>=1.0"]), ("foo", "1.0", ["bar>=2.0"]), False),
    ],
    ids=["identical", "different-name", "different-version", "different-requires"],
)
def test_metadata_core_match(up_args: tuple, down_args: tuple, expected: bool) -> None:
    up = make_metadata(*up_args)
    down = make_metadata(*down_args)
    assert _metadata_core_match(up, down) is expected


def test_metadata_core_match_extra_fields_ignored() -> None:
    """Non-core fields (like Description) should not affect the match."""
    up = make_metadata("foo", "1.0")
    down = up + b"\nDescription: something different\n"
    assert _metadata_core_match(up, down) is True


# --- _compare ---


def test_compare_identical() -> None:
    infos = {"foo/__init__.py": FakeInfo("foo/__init__.py", crc=123, file_size=50)}
    result = _compare(
        upstream=UPSTREAM_URL,
        downstream=DOWNSTREAM_URL,
        upstream_infos=infos,
        downstream_infos=infos,
    )
    assert result.is_identical
    assert len(result.identical) == 1
    assert not result.only_upstream
    assert not result.only_downstream
    assert not result.different


def test_compare_different_crc() -> None:
    up = {"foo/__init__.py": FakeInfo("foo/__init__.py", crc=123, file_size=50)}
    down = {"foo/__init__.py": FakeInfo("foo/__init__.py", crc=456, file_size=50)}
    result = _compare(
        upstream=UPSTREAM_URL,
        downstream=DOWNSTREAM_URL,
        upstream_infos=up,
        downstream_infos=down,
    )
    assert not result.is_identical
    assert len(result.different) == 1
    assert result.different[0].filename == "foo/__init__.py"
    assert result.different[0].severity is Severity.ERROR


def test_compare_only_upstream() -> None:
    shared = FakeInfo("foo/__init__.py", crc=123, file_size=50)
    up = {"foo/__init__.py": shared, "foo/extra.py": FakeInfo("foo/extra.py", crc=789)}
    down = {"foo/__init__.py": shared}
    result = _compare(
        upstream=UPSTREAM_URL,
        downstream=DOWNSTREAM_URL,
        upstream_infos=up,
        downstream_infos=down,
    )
    assert len(result.only_upstream) == 1
    assert result.only_upstream[0].filename == "foo/extra.py"


def test_compare_only_downstream() -> None:
    shared = FakeInfo("foo/__init__.py", crc=123, file_size=50)
    up = {"foo/__init__.py": shared}
    down = {"foo/__init__.py": shared, "foo/new.py": FakeInfo("foo/new.py", crc=999)}
    result = _compare(
        upstream=UPSTREAM_URL,
        downstream=DOWNSTREAM_URL,
        upstream_infos=up,
        downstream_infos=down,
    )
    assert len(result.only_downstream) == 1
    assert result.only_downstream[0].filename == "foo/new.py"


def test_compare_dist_info_record_is_expected() -> None:
    """dist-info RECORD differences should be classified as expected."""
    fname = "foo-1.0.dist-info/RECORD"
    up = {fname: FakeInfo(fname, crc=111, file_size=500)}
    down = {fname: FakeInfo(fname, crc=222, file_size=600)}
    result = _compare(
        upstream=UPSTREAM_URL,
        downstream=DOWNSTREAM_URL,
        upstream_infos=up,
        downstream_infos=down,
    )
    assert result.different[0].severity is Severity.EXPECTED
    assert result.different[0].classification is Classification.RECORD


# --- WheelComparison properties ---


def test_wheel_comparison_is_identical(identical_result: WheelComparison) -> None:
    assert identical_result.is_identical
    assert not identical_result.has_errors


def test_wheel_comparison_has_errors(error_result: WheelComparison) -> None:
    assert error_result.has_errors
    assert not error_result.is_identical


def test_wheel_comparison_notice_only(notice_only_result: WheelComparison) -> None:
    assert not notice_only_result.has_errors
    assert not notice_only_result.is_identical


# --- _local_zip_infos ---


def test_local_zip_infos(tmp_path) -> None:
    whl_path = tmp_path / "test-1.0-py3-none-any.whl"
    with zipfile.ZipFile(whl_path, "w") as zf:
        zf.writestr("test/__init__.py", "# init")
        zf.writestr("test/module.py", "x = 1")
        zf.mkdir("test/subdir/")
    infos = _local_zip_infos(whl_path)
    assert "test/__init__.py" in infos
    assert "test/module.py" in infos
    assert not any(name.endswith("/") for name in infos)


# --- _check_metadata ---


@pytest.fixture()
def _metadata_diff_result():
    """A WheelComparison with a single METADATA diff at NOTICE severity."""
    diff = FileDiff(
        filename="foo-1.0.dist-info/METADATA",
        upstream_size=100,
        downstream_size=200,
        upstream_crc32=111,
        downstream_crc32=222,
        severity=Severity.NOTICE,
        classification=Classification.METADATA,
    )
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
        different=(diff,),
        identical=(),
    )


def test_check_metadata_upgrades_severity(_metadata_diff_result) -> None:
    up = make_metadata("foo", "1.0", ["bar>=1.0"])
    down = make_metadata("foo", "1.0", ["bar>=2.0"])
    result = _check_metadata(
        _metadata_diff_result,
        {"foo-1.0.dist-info/METADATA": up}.__getitem__,
        {"foo-1.0.dist-info/METADATA": down}.__getitem__,
    )
    assert result.different[0].severity is Severity.ERROR
    assert result.different[0].classification is Classification.METADATA


def test_check_metadata_keeps_notice(_metadata_diff_result) -> None:
    up = make_metadata("foo", "1.0")
    down = up + b"\nDescription: different\n"
    result = _check_metadata(
        _metadata_diff_result,
        {"foo-1.0.dist-info/METADATA": up}.__getitem__,
        {"foo-1.0.dist-info/METADATA": down}.__getitem__,
    )
    assert result.different[0].severity is Severity.NOTICE


def test_check_metadata_no_metadata_diffs() -> None:
    diff = FileDiff(
        "foo-1.0.dist-info/RECORD",
        100,
        200,
        111,
        222,
        Severity.EXPECTED,
        Classification.RECORD,
    )
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
        different=(diff,),
        identical=(),
    )
    assert _check_metadata(result, {}.get, {}.get) is result


# --- compare_wheels ---


def test_compare_wheels_identical() -> None:
    info = FakeInfo("foo/__init__.py", crc=123, file_size=50)
    upstream = FakeRemoteZip(UPSTREAM_URL, [info])
    downstream = FakeRemoteZip(DOWNSTREAM_URL, [info])
    assert compare_wheels(upstream, downstream).is_identical


def test_compare_wheels_metadata_upgrade() -> None:
    up_meta = make_metadata("foo", "1.0", ["bar>=1.0"])
    down_meta = make_metadata("foo", "2.0")
    upstream = FakeRemoteZip(
        UPSTREAM_URL,
        [FakeInfo("foo-1.0.dist-info/METADATA", crc=111, file_size=len(up_meta))],
        {"foo-1.0.dist-info/METADATA": up_meta},
    )
    downstream = FakeRemoteZip(
        DOWNSTREAM_URL,
        [FakeInfo("foo-1.0.dist-info/METADATA", crc=222, file_size=len(down_meta))],
        {"foo-1.0.dist-info/METADATA": down_meta},
    )
    result = compare_wheels(upstream, downstream)
    assert result.has_errors
    assert result.different[0].severity is Severity.ERROR


# --- compare_local_wheel ---


def test_compare_local_wheel_identical(tmp_path) -> None:
    downstream_path = tmp_path / "foo-1.0-py3-none-any.whl"
    make_wheel(downstream_path, {"foo/__init__.py": "# init"})

    with zipfile.ZipFile(downstream_path) as zf:
        local_info = zf.getinfo("foo/__init__.py")
    info = FakeInfo("foo/__init__.py", crc=local_info.CRC, file_size=local_info.file_size)
    upstream = FakeRemoteZip(UPSTREAM_URL, [info])
    assert compare_local_wheel(upstream, downstream_path).is_identical


def test_compare_local_wheel_different(tmp_path) -> None:
    downstream_path = tmp_path / "foo-1.0-py3-none-any.whl"
    make_wheel(downstream_path, {"foo/__init__.py": "# downstream"})

    upstream = FakeRemoteZip(UPSTREAM_URL, [FakeInfo("foo/__init__.py", crc=99999, file_size=50)])
    result = compare_local_wheel(upstream, downstream_path)
    assert not result.is_identical
    assert result.has_errors


# --- compare_wheels with RECORD ---


def test_compare_wheels_record_mismatches() -> None:
    """compare_wheels populates record_mismatches on mismatch."""
    # RECORD lists wrong size
    record_bytes = make_record({"foo/__init__.py": 999})
    info = FakeInfo("foo/__init__.py", crc=123, file_size=50)
    record_info = FakeInfo("foo-1.0.dist-info/RECORD", crc=111, file_size=len(record_bytes))
    upstream = FakeRemoteZip(
        UPSTREAM_URL,
        [info, record_info],
        {"foo-1.0.dist-info/RECORD": record_bytes},
    )
    downstream = FakeRemoteZip(
        DOWNSTREAM_URL,
        [info, record_info],
        {"foo-1.0.dist-info/RECORD": record_bytes},
    )
    result = compare_wheels(upstream, downstream)
    assert len(result.record_mismatches) > 0
    assert any("size mismatch" in w.message for w in result.record_mismatches)


def test_compare_wheels_no_record_mismatches() -> None:
    """compare_wheels with consistent RECORD produces no warnings."""
    record_bytes = make_record({"foo/__init__.py": 50})
    info = FakeInfo("foo/__init__.py", crc=123, file_size=50)
    record_info = FakeInfo("foo-1.0.dist-info/RECORD", crc=111, file_size=len(record_bytes))
    upstream = FakeRemoteZip(
        UPSTREAM_URL,
        [info, record_info],
        {"foo-1.0.dist-info/RECORD": record_bytes},
    )
    downstream = FakeRemoteZip(
        DOWNSTREAM_URL,
        [info, record_info],
        {"foo-1.0.dist-info/RECORD": record_bytes},
    )
    result = compare_wheels(upstream, downstream)
    assert result.record_mismatches == ()

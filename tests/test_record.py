"""Tests for retread._record."""

from packaging.version import Version

from retread._compare import WheelComparison
from retread._record import _check_record, _parse_record, check_records

from .conftest import (
    DOWNSTREAM_URL,
    UPSTREAM_URL,
    FakeInfo,
    make_record,
)

_RECORD_PATH = "foo-1.0.dist-info/RECORD"

# --- _parse_record ---


def test_parse_record_basic() -> None:
    data = make_record({"foo/__init__.py": 100, "foo/module.py": 200})
    entries, errors = _parse_record(data, _RECORD_PATH)
    assert not errors
    assert entries["foo/__init__.py"] == 100
    assert entries["foo/module.py"] == 200
    # RECORD itself has None size
    assert entries[_RECORD_PATH] is None


def test_parse_record_empty_size() -> None:
    data = b"foo-1.0.dist-info/RECORD,,\n"
    entries, errors = _parse_record(data, _RECORD_PATH)
    assert not errors
    assert entries[_RECORD_PATH] is None


def test_parse_record_empty_input() -> None:
    entries, errors = _parse_record(b"", _RECORD_PATH)
    assert entries == {}
    assert not errors


def test_parse_record_short_row() -> None:
    data = b"short\n"
    entries, errors = _parse_record(data, _RECORD_PATH)
    assert entries == {}
    assert len(errors) == 1
    assert "expected 3 fields" in errors[0]


def test_parse_record_empty_filename() -> None:
    data = b",sha256=abc,100\n"
    entries, errors = _parse_record(data, _RECORD_PATH)
    assert entries == {}
    assert len(errors) == 1
    assert "empty filename" in errors[0]


def test_parse_record_missing_hash() -> None:
    data = b"foo/__init__.py,,100\n"
    entries, errors = _parse_record(data, _RECORD_PATH)
    assert entries["foo/__init__.py"] == 100
    assert len(errors) == 1
    assert "missing hash" in errors[0]


def test_parse_record_invalid_hash_format() -> None:
    data = b"foo/__init__.py,noequalssign,100\n"
    entries, errors = _parse_record(data, _RECORD_PATH)
    assert entries["foo/__init__.py"] == 100
    assert len(errors) == 1
    assert "invalid hash format" in errors[0]


def test_parse_record_missing_size() -> None:
    data = b"foo/__init__.py,sha256=abc,\n"
    entries, errors = _parse_record(data, _RECORD_PATH)
    assert entries["foo/__init__.py"] is None
    assert len(errors) == 1
    assert "missing size" in errors[0]


def test_parse_record_invalid_size() -> None:
    data = b"foo/__init__.py,sha256=abc,notanumber\n"
    entries, errors = _parse_record(data, _RECORD_PATH)
    assert entries["foo/__init__.py"] is None
    assert len(errors) == 1
    assert "invalid size" in errors[0]


def test_parse_record_signature_files_allowed_empty() -> None:
    """RECORD.p7s and RECORD.jws may have empty hash and size."""
    data = (
        b"foo-1.0.dist-info/RECORD,,\n"
        b"foo-1.0.dist-info/RECORD.p7s,,\n"
        b"foo-1.0.dist-info/RECORD.jws,,\n"
    )
    entries, errors = _parse_record(data, _RECORD_PATH)
    assert not errors
    assert len(entries) == 3


def test_parse_record_regular_file_empty_hash_size_errors() -> None:
    """Regular files with empty hash+size are reported as errors."""
    data = b"foo/__init__.py,,\n"
    _entries, errors = _parse_record(data, _RECORD_PATH)
    assert len(errors) == 2
    assert any("missing hash" in e for e in errors)
    assert any("missing size" in e for e in errors)


# --- _check_record ---


def test_check_record_all_match() -> None:
    record_files = {"foo/__init__.py": 100, "foo-1.0.dist-info/RECORD": None}
    zip_infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py", file_size=100),
        "foo-1.0.dist-info/RECORD": FakeInfo("foo-1.0.dist-info/RECORD", file_size=50),
    }
    warnings = _check_record("upstream", record_files, zip_infos, "foo-1.0.dist-info/RECORD")
    assert warnings == []


def test_check_record_file_in_zip_not_record() -> None:
    record_files = {"foo/__init__.py": 100, "foo-1.0.dist-info/RECORD": None}
    zip_infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py", file_size=100),
        "foo/extra.py": FakeInfo("foo/extra.py", file_size=50),
        "foo-1.0.dist-info/RECORD": FakeInfo("foo-1.0.dist-info/RECORD", file_size=50),
    }
    warnings = _check_record("upstream", record_files, zip_infos, "foo-1.0.dist-info/RECORD")
    assert len(warnings) == 1
    assert "file in ZIP but not in RECORD" in warnings[0].message
    assert "foo/extra.py" in warnings[0].message


def test_check_record_file_in_record_not_zip() -> None:
    record_files = {
        "foo/__init__.py": 100,
        "foo/missing.py": 200,
        "foo-1.0.dist-info/RECORD": None,
    }
    zip_infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py", file_size=100),
        "foo-1.0.dist-info/RECORD": FakeInfo("foo-1.0.dist-info/RECORD", file_size=50),
    }
    warnings = _check_record("upstream", record_files, zip_infos, "foo-1.0.dist-info/RECORD")
    assert len(warnings) == 1
    assert "file in RECORD but not in ZIP" in warnings[0].message
    assert "foo/missing.py" in warnings[0].message


def test_check_record_size_mismatch() -> None:
    record_files = {"foo/__init__.py": 99, "foo-1.0.dist-info/RECORD": None}
    zip_infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py", file_size=100),
        "foo-1.0.dist-info/RECORD": FakeInfo("foo-1.0.dist-info/RECORD", file_size=50),
    }
    warnings = _check_record("upstream", record_files, zip_infos, "foo-1.0.dist-info/RECORD")
    assert len(warnings) == 1
    assert "size mismatch" in warnings[0].message
    assert "RECORD says 99" in warnings[0].message
    assert "ZIP says 100" in warnings[0].message


def test_check_record_record_itself_excluded() -> None:
    """RECORD file should not be flagged as missing from RECORD."""
    record_files = {"foo/__init__.py": 100}
    zip_infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py", file_size=100),
        "foo-1.0.dist-info/RECORD": FakeInfo("foo-1.0.dist-info/RECORD", file_size=50),
    }
    warnings = _check_record("upstream", record_files, zip_infos, "foo-1.0.dist-info/RECORD")
    assert warnings == []


def test_check_record_none_size_skipped() -> None:
    """When RECORD has None size, size comparison should be skipped."""
    record_files = {"foo/__init__.py": None, "foo-1.0.dist-info/RECORD": None}
    zip_infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py", file_size=100),
        "foo-1.0.dist-info/RECORD": FakeInfo("foo-1.0.dist-info/RECORD", file_size=50),
    }
    warnings = _check_record("upstream", record_files, zip_infos, "foo-1.0.dist-info/RECORD")
    assert warnings == []


# --- check_records ---


def test_check_records_consistent_no_warnings() -> None:
    """Consistent RECORD produces no warnings."""
    record_bytes = make_record({"foo/__init__.py": 100})
    infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py", file_size=100),
        "foo-1.0.dist-info/RECORD": FakeInfo("foo-1.0.dist-info/RECORD", file_size=50),
    }
    result = WheelComparison(
        upstream=UPSTREAM_URL,
        downstream=DOWNSTREAM_URL,
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
    checked = check_records(
        result,
        upstream_infos=infos,
        downstream_infos=infos,
        upstream_record=record_bytes,
        downstream_record=record_bytes,
    )
    assert checked.record_mismatches == ()


def test_check_records_missing_record_errors() -> None:
    """Missing RECORD produces an error."""
    infos = {"foo/__init__.py": FakeInfo("foo/__init__.py", file_size=100)}
    result = WheelComparison(
        upstream=UPSTREAM_URL,
        downstream=DOWNSTREAM_URL,
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
    checked = check_records(
        result,
        upstream_infos=infos,
        downstream_infos=infos,
    )
    assert len(checked.record_mismatches) == 2
    assert all("missing RECORD" in m.message for m in checked.record_mismatches)


def test_check_records_malformed_record_errors() -> None:
    """Malformed RECORD (not valid UTF-8) produces an error."""
    infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py", file_size=100),
        "foo-1.0.dist-info/RECORD": FakeInfo("foo-1.0.dist-info/RECORD", file_size=50),
    }
    result = WheelComparison(
        upstream=UPSTREAM_URL,
        downstream=DOWNSTREAM_URL,
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
    checked = check_records(
        result,
        upstream_infos=infos,
        downstream_infos=infos,
        upstream_record=b"\xff\xfe",
        downstream_record=b"\xff\xfe",
    )
    assert len(checked.record_mismatches) == 2
    assert all("malformed RECORD" in m.message for m in checked.record_mismatches)


def test_check_records_empty_record_errors() -> None:
    """Empty RECORD (no valid entries) produces an error."""
    infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py", file_size=100),
        "foo-1.0.dist-info/RECORD": FakeInfo("foo-1.0.dist-info/RECORD", file_size=50),
    }
    result = WheelComparison(
        upstream=UPSTREAM_URL,
        downstream=DOWNSTREAM_URL,
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
    checked = check_records(
        result,
        upstream_infos=infos,
        downstream_infos=infos,
        upstream_record=b"",
        downstream_record=b"",
    )
    assert len(checked.record_mismatches) == 2
    assert all("empty RECORD" in m.message for m in checked.record_mismatches)


def test_check_records_mismatch_warns() -> None:
    """Mismatched RECORD produces warnings."""
    # RECORD says size 99 but ZIP says 100
    record_bytes = make_record({"foo/__init__.py": 99})
    infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py", file_size=100),
        "foo-1.0.dist-info/RECORD": FakeInfo("foo-1.0.dist-info/RECORD", file_size=50),
    }
    result = WheelComparison(
        upstream=UPSTREAM_URL,
        downstream=DOWNSTREAM_URL,
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
    checked = check_records(
        result,
        upstream_infos=infos,
        downstream_infos=infos,
        upstream_record=record_bytes,
        downstream_record=record_bytes,
    )
    assert len(checked.record_mismatches) > 0
    assert any("size mismatch" in w.message for w in checked.record_mismatches)

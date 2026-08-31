"""Tests for :class:`WheelSource` index metadata and its reporting."""

from __future__ import annotations

import datetime
import json

import pytest

from retread import Analysis, Comparison, Context, WheelSource
from retread.__main__ import _format_size, _print_wheel_line
from retread._wheel import _wheel_tag_string
from tests.conftest import make_wheel_info


@pytest.mark.parametrize(
    ("num_bytes", "expected"),
    [
        (0, "0 bytes"),
        (999, "999 bytes"),
        (1000, "1.0 kB"),
        (1234, "1.2 kB"),
        (5_000_000, "5.0 MB"),
        (2_500_000_000, "2.5 GB"),
    ],
)
def test_format_size(num_bytes, expected) -> None:
    assert _format_size(num_bytes) == expected


class FakeIndexPkg:
    """Stand-in for pypi_simple.DistributionPackage with release metadata."""

    def __init__(
        self,
        filename,
        version,
        *,
        package_type="wheel",
        url="",
        upload_time=None,
        provenance_url=None,
    ):
        self.filename = filename
        self.version = version
        self.package_type = package_type
        self.url = url or f"https://pypi.example/{filename}"
        self.upload_time = upload_time
        self.provenance_url = provenance_url


class FakeIndexPage:
    """Stand-in for pypi_simple.ProjectPage."""

    def __init__(self, packages):
        self.packages = packages


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("foo-1.0-py3-none-any.whl", "py3-none-any"),
        ("foo-1.0-cp312-cp312-manylinux_2_17_x86_64.whl", "cp312-cp312-manylinux_2_17_x86_64"),
        ("foo-1.0-1-cp312-abi3-macosx_11_0_arm64.whl", "cp312-abi3-macosx_11_0_arm64"),
        ("foo-1.0.tar.gz", None),
        ("not-a-wheel.whl", None),
    ],
)
def test_wheel_tag_string(filename, expected) -> None:
    assert _wheel_tag_string(filename) == expected


def test_from_package_collects_release_facts() -> None:
    when = datetime.datetime(2024, 1, 15, 8, 30, tzinfo=datetime.UTC)
    matched = FakeIndexPkg(
        "foo-1.0-cp312-cp312-manylinux_2_17_x86_64.whl",
        "1.0",
        upload_time=when,
        provenance_url="https://pypi.example/foo-1.0.provenance",
    )
    page = FakeIndexPage(
        [
            matched,
            FakeIndexPkg("foo-1.0-py3-none-any.whl", "1.0"),
            FakeIndexPkg("foo-1.0.tar.gz", "1.0", package_type="sdist"),
            # A different release must not leak into the facts.
            FakeIndexPkg("foo-0.9-py3-none-any.whl", "0.9"),
        ]
    )
    source = WheelSource.from_package(matched, page)
    assert source.source == matched.url
    assert source.upload_time == when
    assert source.provenance_url == "https://pypi.example/foo-1.0.provenance"
    assert source.has_sdist is True
    assert source.has_wheels is True
    assert source.wheel_tags == ("cp312-cp312-manylinux_2_17_x86_64", "py3-none-any")


def test_from_package_no_sdist_no_provenance() -> None:
    matched = FakeIndexPkg("foo-1.0-py3-none-any.whl", "1.0")
    page = FakeIndexPage([matched])
    source = WheelSource.from_package(matched, page)
    assert source.has_sdist is False
    assert source.has_wheels is True
    assert source.provenance_url is None
    assert source.upload_time is None


def test_local_source_is_source_only() -> None:
    source = WheelSource.local("/tmp/foo-1.0-py3-none-any.whl")
    assert source.source == "/tmp/foo-1.0-py3-none-any.whl"
    assert source.upload_time is None
    assert source.has_sdist is False
    assert source.has_wheels is False
    assert source.wheel_tags == ()


def test_to_dict_serializes_origin() -> None:
    when = datetime.datetime(2024, 3, 1, 12, 0, tzinfo=datetime.UTC)
    origin = WheelSource(
        source="https://pypi.example/foo-1.0-py3-none-any.whl",
        upload_time=when,
        provenance_url="https://pypi.example/foo-1.0.provenance",
        has_sdist=True,
        has_wheels=True,
        wheel_tags=("py3-none-any",),
    )
    upstream = make_wheel_info("foo-1.0-py3-none-any.whl", [], origin=origin, file_size=4096)
    downstream = make_wheel_info("foo-1.0-py3-none-any.whl", [], source="/tmp/foo.whl")
    result = Comparison(
        context=Context.default(),
        upstream=upstream,
        downstream=downstream,
        analysis=Analysis(),
    )
    data = result.to_dict()
    assert data["upstream"] == origin.source
    up = data["upstream_source"]
    assert up["file_size"] == 4096
    assert up["upload_time"] == when.isoformat()
    assert up["provenance_url"] == origin.provenance_url
    assert up["has_sdist"] is True
    assert up["wheel_tags"] == ["py3-none-any"]
    # A source-only downstream carries no index metadata.
    assert data["downstream_source"]["upload_time"] is None
    # Datetime is serialized to a string, so the dict round-trips through JSON.
    assert json.loads(json.dumps(data)) == data


def test_cli_prints_upload_date_without_time(capsys) -> None:
    when = datetime.datetime(2024, 1, 15, 8, 30, 45, tzinfo=datetime.UTC)
    origin = WheelSource(
        source="https://pypi.example/foo-1.0-py3-none-any.whl",
        upload_time=when,
        has_wheels=True,
        wheel_tags=("py3-none-any",),
    )
    wheel = make_wheel_info("foo-1.0-py3-none-any.whl", [], origin=origin, file_size=1234)
    _print_wheel_line("Upstream:   ", wheel)
    out = capsys.readouterr().out
    assert "foo-1.0-py3-none-any.whl" in out
    assert "uploaded 2024-01-15" in out
    assert "08:30" not in out  # no time-of-day
    assert "1.2 kB" in out  # human-readable SI size

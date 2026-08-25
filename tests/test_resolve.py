"""Tests for retread._resolve."""

import pytest
from packaging.tags import Tag

from retread._errors import InvalidWheelError, WheelNotFoundError
from retread._resolve import (
    _extract_arch,
    _tags_compatible,
    _wheels_compatible,
    find_matching_wheel,
    parse_wheel_spec,
)

from .conftest import FakePage, FakePkg

# --- parse_wheel_spec ---


@pytest.mark.parametrize(
    ("filename", "expected_name", "expected_version"),
    [
        ("requests-2.32.3-py3-none-any.whl", "requests", "2.32.3"),
        ("uv-0.12.5-1-py3-none-linux_x86_64.whl", "uv", "0.12.5"),
        ("Pillow-12.3.0-cp312-cp312-manylinux_2_28_x86_64.whl", "pillow", "12.3.0"),
    ],
    ids=["pure-python", "build-tag", "platform"],
)
def test_parse_wheel_spec(filename: str, expected_name: str, expected_version: str) -> None:
    spec = parse_wheel_spec(filename)
    assert str(spec.name) == expected_name
    assert str(spec.version) == expected_version
    assert spec.filename == filename


def test_parse_wheel_spec_no_build_tag() -> None:
    spec = parse_wheel_spec("requests-2.32.3-py3-none-any.whl")
    assert not spec.build


def test_parse_wheel_spec_with_build_tag() -> None:
    spec = parse_wheel_spec("uv-0.12.5-1-py3-none-linux_x86_64.whl")
    assert spec.build


def test_parse_wheel_spec_invalid() -> None:
    with pytest.raises(InvalidWheelError, match=r"not-a-wheel\.tar\.gz"):
        parse_wheel_spec("not-a-wheel.tar.gz")


# --- _extract_arch ---


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("linux_x86_64", "x86_64"),
        ("linux_aarch64", "aarch64"),
        ("linux_s390x", "s390x"),
        ("linux_ppc64le", "ppc64le"),
        ("manylinux_2_28_x86_64", "x86_64"),
        ("manylinux_2_17_aarch64", "aarch64"),
        # unsupported or non-linux → None
        ("linux_ia64", None),
        ("musllinux_1_1_x86_64", None),
        ("any", None),
        ("win_amd64", None),
    ],
)
def test_extract_arch(platform: str, expected: str | None) -> None:
    assert _extract_arch(platform) == expected


# --- _tags_compatible ---


@pytest.mark.parametrize(
    ("ds_tag", "us_tag", "expected"),
    [
        # exact match
        (Tag("cp312", "cp312", "linux_x86_64"), Tag("cp312", "cp312", "linux_x86_64"), True),
        # manylinux arch match
        (
            Tag("cp312", "cp312", "linux_x86_64"),
            Tag("cp312", "cp312", "manylinux_2_28_x86_64"),
            True,
        ),
        # different arch
        (Tag("cp312", "cp312", "linux_x86_64"), Tag("cp312", "cp312", "linux_aarch64"), False),
        # different interpreter
        (Tag("cp311", "cp311", "linux_x86_64"), Tag("cp312", "cp312", "linux_x86_64"), False),
        # pure python
        (Tag("py3", "none", "any"), Tag("py3", "none", "any"), True),
        # abi3 ↔ concrete (newer concrete)
        (Tag("cp310", "abi3", "linux_x86_64"), Tag("cp312", "cp312", "linux_x86_64"), True),
        # abi3 ↔ concrete (older concrete — incompatible)
        (Tag("cp312", "abi3", "linux_x86_64"), Tag("cp310", "cp310", "linux_x86_64"), False),
        # both abi3
        (Tag("cp310", "abi3", "linux_x86_64"), Tag("cp312", "abi3", "linux_x86_64"), True),
        # abi3 with non-CPython interpreter
        (Tag("pp310", "abi3", "linux_x86_64"), Tag("cp312", "cp312", "linux_x86_64"), False),
        # abi3 ↔ concrete with mismatched ABI (cp312 interp but 'none' abi)
        (Tag("cp310", "abi3", "linux_x86_64"), Tag("cp312", "none", "linux_x86_64"), False),
    ],
    ids=[
        "exact-match",
        "manylinux-arch",
        "different-arch",
        "different-interp",
        "pure-python",
        "abi3-newer-concrete",
        "abi3-older-concrete",
        "both-abi3",
        "abi3-non-cpython",
        "abi3-abi-mismatch",
    ],
)
def test_tags_compatible(ds_tag: Tag, us_tag: Tag, expected: bool) -> None:
    assert _tags_compatible(ds_tag, us_tag) is expected


def test_tags_compatible_abi3_symmetric() -> None:
    """abi3 compatibility should work regardless of which side is abi3."""
    abi3 = Tag("cp310", "abi3", "linux_x86_64")
    concrete = Tag("cp312", "cp312", "linux_x86_64")
    assert _tags_compatible(abi3, concrete) is True
    assert _tags_compatible(concrete, abi3) is True


# --- _wheels_compatible ---


@pytest.mark.parametrize(
    ("ds_tags", "us_tags", "expected"),
    [
        (
            frozenset({Tag("cp312", "cp312", "linux_x86_64")}),
            frozenset({Tag("cp312", "cp312", "manylinux_2_28_x86_64")}),
            True,
        ),
        (
            frozenset({Tag("cp312", "cp312", "linux_x86_64")}),
            frozenset({Tag("cp312", "cp312", "linux_aarch64")}),
            False,
        ),
    ],
    ids=["matching", "no-match"],
)
def test_wheels_compatible(
    ds_tags: frozenset[Tag], us_tags: frozenset[Tag], expected: bool
) -> None:
    assert _wheels_compatible(ds_tags, us_tags) is expected


# --- find_matching_wheel ---


def test_find_matching_wheel() -> None:
    spec = parse_wheel_spec("foo-1.0-cp312-cp312-linux_x86_64.whl")
    page = FakePage(
        [
            FakePkg("foo-1.0.tar.gz"),
            FakePkg("foo-1.0-cp312-cp312-manylinux_2_28_x86_64.whl"),
        ]
    )
    pkg = find_matching_wheel(page, spec, index="https://pypi.org/simple/")
    assert pkg.filename == "foo-1.0-cp312-cp312-manylinux_2_28_x86_64.whl"


def test_find_matching_wheel_not_found() -> None:
    spec = parse_wheel_spec("foo-1.0-cp312-cp312-linux_x86_64.whl")
    page = FakePage(
        [
            FakePkg("foo-1.0.tar.gz"),
            FakePkg("bar-1.0-cp312-cp312-manylinux_2_28_x86_64.whl"),
        ]
    )
    with pytest.raises(WheelNotFoundError):
        find_matching_wheel(page, spec, index="https://pypi.org/simple/")


def test_find_matching_wheel_skips_invalid() -> None:
    """Invalid wheel filenames on the page should be skipped."""
    spec = parse_wheel_spec("foo-1.0-cp312-cp312-linux_x86_64.whl")
    page = FakePage(
        [
            FakePkg("foo-1.0-invalid.whl"),
            FakePkg("foo-1.0-cp312-cp312-manylinux_2_28_x86_64.whl"),
        ]
    )
    pkg = find_matching_wheel(page, spec, index="https://pypi.org/simple/")
    assert pkg.filename == "foo-1.0-cp312-cp312-manylinux_2_28_x86_64.whl"

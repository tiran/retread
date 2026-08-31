"""Tests for retread._resolve."""

import pytest
from packaging.tags import Tag
from packaging.version import Version

from retread import Context
from retread._api import _apply_resolution_mismatch, _resolution_mismatch
from retread._errors import (
    InvalidWheelError,
    NoWheelsError,
    VersionNotFoundError,
    WheelNotFoundError,
)
from retread._policy import PackagePolicy, VersionPolicy
from retread._resolve import (
    Resolution,
    ResolutionStatus,
    _best_tag_score,
    _extract_arch,
    _tag_match_score,
    _tags_compatible,
    _version_match,
    _wheel_tag_string,
    _wheels_compatible,
    find_matching_wheel,
    parse_wheel_spec,
    resolve_upstream,
)

from .conftest import FakePage, FakePkg, make_comparison

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


def test_find_matching_wheel_prefers_exact_tags() -> None:
    """Exact tag match should be preferred over abi3 compatibility."""
    spec = parse_wheel_spec("foo-1.0-cp312-cp312-linux_x86_64.whl")
    page = FakePage(
        [
            FakePkg("foo-1.0-cp39-abi3-manylinux_2_17_x86_64.whl"),
            FakePkg("foo-1.0-cp312-cp312-manylinux_2_28_x86_64.whl"),
        ]
    )
    pkg = find_matching_wheel(page, spec, index="https://pypi.org/simple/")
    assert pkg.filename == "foo-1.0-cp312-cp312-manylinux_2_28_x86_64.whl"


def test_find_matching_wheel_prefers_closer_abi3() -> None:
    """When both are abi3, prefer the one with matching minimum version."""
    spec = parse_wheel_spec("foo-1.0-cp312-abi3-linux_x86_64.whl")
    page = FakePage(
        [
            FakePkg("foo-1.0-cp39-abi3-manylinux_2_17_x86_64.whl"),
            FakePkg("foo-1.0-cp312-abi3-manylinux_2_28_x86_64.whl"),
        ]
    )
    pkg = find_matching_wheel(page, spec, index="https://pypi.org/simple/")
    assert pkg.filename == "foo-1.0-cp312-abi3-manylinux_2_28_x86_64.whl"


def test_find_matching_wheel_prefers_abi3_over_abi3t() -> None:
    """Prefer exact abi match over compound abi3.abi3t tag."""
    spec = parse_wheel_spec("foo-1.0-cp39-abi3-linux_x86_64.whl")
    # The compound tag "abi3.abi3t" expands to two tags: one with abi3 and
    # one with abi3t.  The abi3 tag matches, but cp315 != cp39 so the
    # compound wheel scores lower than the exact cp39-abi3 match.
    page = FakePage(
        [
            FakePkg("foo-1.0-cp315-abi3.abi3t-manylinux_2_17_x86_64.whl"),
            FakePkg("foo-1.0-cp39-abi3-manylinux_2_17_x86_64.whl"),
        ]
    )
    pkg = find_matching_wheel(page, spec, index="https://pypi.org/simple/")
    assert pkg.filename == "foo-1.0-cp39-abi3-manylinux_2_17_x86_64.whl"


# --- _tag_match_score / _best_tag_score ---


@pytest.mark.parametrize(
    ("ds_tag", "us_tag", "expected"),
    [
        # exact match → 2
        (Tag("cp312", "cp312", "linux_x86_64"), Tag("cp312", "cp312", "linux_x86_64"), 2),
        # exact abi3 match → 2
        (Tag("cp39", "abi3", "linux_x86_64"), Tag("cp39", "abi3", "linux_x86_64"), 2),
        # abi3 ↔ concrete → 1
        (Tag("cp39", "abi3", "linux_x86_64"), Tag("cp312", "cp312", "linux_x86_64"), 1),
        # both abi3 different versions → 1
        (Tag("cp39", "abi3", "linux_x86_64"), Tag("cp312", "abi3", "linux_x86_64"), 1),
        # incompatible → 0
        (Tag("cp312", "cp312", "linux_x86_64"), Tag("cp311", "cp311", "linux_x86_64"), 0),
    ],
    ids=["exact", "exact-abi3", "abi3-concrete", "both-abi3-diff", "incompatible"],
)
def test_tag_match_score(ds_tag: Tag, us_tag: Tag, expected: int) -> None:
    assert _tag_match_score(ds_tag, us_tag) == expected


def test_best_tag_score() -> None:
    ds_tags = frozenset({Tag("cp312", "cp312", "linux_x86_64")})
    # upstream has both an abi3 and an exact match tag
    us_tags = frozenset(
        {
            Tag("cp39", "abi3", "linux_x86_64"),
            Tag("cp312", "cp312", "linux_x86_64"),
        }
    )
    assert _best_tag_score(ds_tags, us_tags) == 2


# --- _version_match ---


@pytest.mark.parametrize(
    ("upstream", "downstream", "expected"),
    [
        ("1.0", "1.0", True),
        ("1.0", "2.0", False),
        ("1.5.0", "1.5.0+rhaiv.5", True),
        ("1.5.0", "1.5.0+cpu", True),
        ("1.5.0+cpu", "1.5.0+cpu", True),
        ("1.5.0+cpu", "1.5.0+rhaiv.5", True),
        ("2.0", "1.5.0+rhaiv.5", False),
    ],
    ids=[
        "exact",
        "different",
        "local-rhaiv",
        "local-cpu",
        "same-local",
        "different-local",
        "local-wrong-base",
    ],
)
def test_version_match(upstream: str, downstream: str, expected: bool) -> None:
    assert _version_match(Version(upstream), Version(downstream)) is expected


# --- find_matching_wheel with local version ---


def test_find_matching_wheel_strips_local_version() -> None:
    """Downstream with +local suffix should find upstream base version."""
    spec = parse_wheel_spec("foo-1.5.0+rhaiv.5-1-py3-none-any.whl")
    page = FakePage(
        [
            FakePkg("foo-1.5.0-py3-none-any.whl"),
        ]
    )
    pkg = find_matching_wheel(page, spec, index="https://pypi.org/simple/")
    assert pkg.filename == "foo-1.5.0-py3-none-any.whl"


def test_find_matching_wheel_local_version_no_match() -> None:
    """Local version with wrong base should not match."""
    spec = parse_wheel_spec("foo-1.5.0+rhaiv.5-1-py3-none-any.whl")
    page = FakePage(
        [
            FakePkg("foo-2.0-py3-none-any.whl"),
        ]
    )
    with pytest.raises(WheelNotFoundError):
        find_matching_wheel(page, spec, index="https://pypi.org/simple/")


# --- _wheel_tag_string ---


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("foo-1.0-py3-none-any.whl", "py3-none-any"),
        ("foo-1.0-cp312-cp312-manylinux_2_28_x86_64.whl", "cp312-cp312-manylinux_2_28_x86_64"),
        ("foo-1.0-1-cp312-cp312-win_amd64.whl", "cp312-cp312-win_amd64"),
    ],
    ids=["pure", "manylinux", "build-tag"],
)
def test_wheel_tag_string(filename: str, expected: str) -> None:
    assert _wheel_tag_string(filename) == expected


# --- resolve_upstream ---


def test_resolve_upstream_matched() -> None:
    """A tag-compatible wheel resolves as MATCHED."""
    spec = parse_wheel_spec("foo-1.0-cp312-cp312-linux_x86_64.whl")
    page = FakePage(
        [
            FakePkg("foo-1.0.tar.gz"),
            FakePkg("foo-1.0-cp312-cp312-manylinux_2_28_x86_64.whl"),
        ]
    )
    resolution = resolve_upstream(page, spec, index="https://pypi.org/simple/")
    assert resolution.status is ResolutionStatus.MATCHED
    assert resolution.package.filename == "foo-1.0-cp312-cp312-manylinux_2_28_x86_64.whl"
    assert resolution.available_tags == ()


def test_resolve_upstream_fallback_purelib_vs_manylinux() -> None:
    """A purelib downstream against manylinux upstream falls back to a cp3x wheel.

    Mirrors the google_crc32c case: downstream is ``py3-none-any`` but upstream
    ships only compiled wheels, so no tag matches.  The fallback prefers the
    manylinux x86_64 wheel with the highest CPython version and lists all
    available upstream tags.
    """
    spec = parse_wheel_spec("google_crc32c-1.8.0-1-py3-none-any.whl")
    page = FakePage(
        [
            FakePkg("google_crc32c-1.8.0.tar.gz"),
            FakePkg("google_crc32c-1.8.0-cp311-cp311-manylinux_2_17_x86_64.whl"),
            FakePkg("google_crc32c-1.8.0-cp312-cp312-manylinux_2_17_x86_64.whl"),
            FakePkg("google_crc32c-1.8.0-cp312-cp312-macosx_11_0_arm64.whl"),
            FakePkg("google_crc32c-1.8.0-cp312-cp312-win_amd64.whl"),
        ]
    )
    resolution = resolve_upstream(page, spec, index="https://pypi.org/simple/")
    assert resolution.status is ResolutionStatus.FALLBACK
    assert (
        resolution.package.filename == "google_crc32c-1.8.0-cp312-cp312-manylinux_2_17_x86_64.whl"
    )
    assert "cp311-cp311-manylinux_2_17_x86_64" in resolution.available_tags
    assert "cp312-cp312-win_amd64" in resolution.available_tags


def test_resolve_upstream_no_version() -> None:
    """A missing version raises VersionNotFoundError listing available versions."""
    spec = parse_wheel_spec("foo-9.9.9-py3-none-any.whl")
    page = FakePage(
        [
            FakePkg("foo-1.0.tar.gz"),
            FakePkg("foo-1.0-py3-none-any.whl"),
            FakePkg("foo-2.0-py3-none-any.whl"),
        ]
    )
    with pytest.raises(VersionNotFoundError) as excinfo:
        resolve_upstream(page, spec, index="https://pypi.org/simple/")
    assert excinfo.value.available_versions == ("1.0", "2.0")
    assert "9.9.9" in str(excinfo.value)


def test_resolve_upstream_no_wheels() -> None:
    """A version published only as an sdist raises NoWheelsError."""
    spec = parse_wheel_spec("foo-1.0-py3-none-any.whl")
    page = FakePage([FakePkg("foo-1.0.tar.gz")])
    with pytest.raises(NoWheelsError):
        resolve_upstream(page, spec, index="https://pypi.org/simple/")


# --- _api resolution-mismatch helpers ---


def test_resolution_mismatch_fallback() -> None:
    """A fallback resolution produces a mismatch carrying both sides' tags."""
    spec = parse_wheel_spec("foo-1.0-py3-none-any.whl")
    resolution = Resolution(
        ResolutionStatus.FALLBACK,
        FakePkg("foo-1.0-cp312-cp312-manylinux_2_17_x86_64.whl"),
        spec,
        "https://pypi.example/",
        available_tags=("cp311-cp311-manylinux_2_17_x86_64", "cp312-cp312-manylinux_2_17_x86_64"),
    )
    mismatch = _resolution_mismatch(resolution, "foo-1.0-py3-none-any.whl")
    assert mismatch is not None
    assert mismatch.downstream_tags == ("py3-none-any",)
    assert mismatch.upstream_tags == resolution.available_tags
    assert "py3-none-any" in mismatch.message
    assert "foo-1.0-cp312-cp312-manylinux_2_17_x86_64.whl" in mismatch.message


def test_resolution_mismatch_matched_is_none() -> None:
    """A matched resolution (or no resolution) produces no mismatch."""
    spec = parse_wheel_spec("foo-1.0-cp312-cp312-linux_x86_64.whl")
    matched = Resolution(
        ResolutionStatus.MATCHED,
        FakePkg("foo-1.0-cp312-cp312-manylinux_2_17_x86_64.whl"),
        spec,
        "https://pypi.example/",
    )
    assert _resolution_mismatch(matched, "foo-1.0-cp312-cp312-linux_x86_64.whl") is None
    assert _resolution_mismatch(None, "foo-1.0-py3-none-any.whl") is None


def test_apply_resolution_mismatch_appends() -> None:
    """_apply_resolution_mismatch appends a fallback mismatch to the comparison analysis."""
    result = make_comparison(
        upstream_wheel="foo-1.0-cp312-cp312-manylinux_2_17_x86_64.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
    )
    spec = parse_wheel_spec("foo-1.0-py3-none-any.whl")
    resolution = Resolution(
        ResolutionStatus.FALLBACK,
        FakePkg("foo-1.0-cp312-cp312-manylinux_2_17_x86_64.whl"),
        spec,
        "https://pypi.example/",
        available_tags=("cp312-cp312-manylinux_2_17_x86_64",),
    )
    _apply_resolution_mismatch(result, resolution)
    assert len(result.analysis.resolution_mismatches) == 1
    assert result.has_errors is True


def _cross_platform_policy(allow: bool) -> dict[str, PackagePolicy]:
    """A policy for ``foo`` toggling cross-platform validation."""
    vp = VersionPolicy(
        description="test",
        ignore_differences=(),
        ignore_missing_downstream=(),
        ignore_extra_downstream=(),
        platlib=False,
        allow_cross_platform=allow,
    )
    return {"foo": PackagePolicy(dist_name="foo", versions={"*": vp})}


@pytest.mark.parametrize("allow", [True, False])
def test_apply_resolution_mismatch_allow_cross_platform(allow: bool) -> None:
    """allow_cross_platform marks the fallback mismatch ignored (not an error)."""
    result = make_comparison(
        upstream_wheel="foo-1.0-cp312-cp312-win_amd64.whl",
        downstream_wheel="foo-1.0-cp312-cp312-linux_x86_64.whl",
        context=Context.default(policy=_cross_platform_policy(allow)),
    )
    spec = parse_wheel_spec("foo-1.0-cp312-cp312-linux_x86_64.whl")
    resolution = Resolution(
        ResolutionStatus.FALLBACK,
        FakePkg("foo-1.0-cp312-cp312-win_amd64.whl"),
        spec,
        "https://pypi.example/",
        available_tags=("cp312-cp312-win_amd64",),
    )
    _apply_resolution_mismatch(result, resolution)
    assert len(result.analysis.resolution_mismatches) == 1
    assert result.analysis.resolution_mismatches[0].ignored is allow
    # The mismatch is still reported, but only counts as an error when the
    # policy does not accept the cross-platform comparison.
    assert result.has_errors is (not allow)

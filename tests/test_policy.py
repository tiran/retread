"""Tests for retread._policy."""

import pathlib
import textwrap

import pytest

from retread import (
    Analysis,
    Classification,
    Comparison,
    Context,
    FileDiff,
    FileEntry,
    MetadataFieldDiff,
    Severity,
)
from retread._errors import PolicyError
from retread._findings import NO_SHARED_LIBS_WARNING, PlatformWarning
from retread._policy import (
    PackagePolicy,
    VersionPolicy,
    _matches_any_pattern,
    _validate_filename,
    load_policy_dir,
    lookup_policy,
)
from retread.checker._policy import apply_policy

from .conftest import make_comparison, make_wheel_info

# --- _validate_filename ---


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("cmake", "cmake"),
        ("scikit_learn", "scikit-learn"),
        ("numpy", "numpy"),
        ("a1b2c3", "a1b2c3"),
    ],
    ids=["simple", "underscore", "single", "alphanumeric"],
)
def test_validate_filename_valid(stem: str, expected: str, tmp_path: pathlib.Path) -> None:
    path = tmp_path / f"{stem}.toml"
    assert _validate_filename(path) == expected


@pytest.mark.parametrize(
    "stem",
    ["CMAKE", "scikit-learn", "Foo.Bar"],
    ids=["uppercase", "hyphen", "dot"],
)
def test_validate_filename_invalid(stem: str, tmp_path: pathlib.Path) -> None:
    path = tmp_path / f"{stem}.toml"
    with pytest.raises(PolicyError, match="not normalized"):
        _validate_filename(path)


# --- _matches_any_pattern ---


@pytest.mark.parametrize(
    ("filename", "patterns", "expected"),
    [
        ("foo/bar.py", ("foo/bar.py",), True),
        ("foo/bar.py", ("foo/baz.py",), False),
        ("cmake/data/bin/cmake", ("cmake/data/bin/*",), True),
        ("cmake/data/bin/sub/cmake", ("cmake/data/bin/*",), True),
        ("other/file.py", ("cmake/data/bin/*",), False),
        ("torch/lib/libcuda.so", ("torch/lib/lib*.so",), True),
        ("foo.py", (), False),
    ],
    ids=[
        "exact",
        "no-match",
        "wildcard",
        "wildcard-nested",
        "wildcard-miss",
        "prefix-glob",
        "empty",
    ],
)
def test_matches_any_pattern(filename: str, patterns: tuple[str, ...], expected: bool) -> None:
    assert _matches_any_pattern(filename, patterns) is expected


# --- load_policy_dir ---


def test_load_policy_dir_valid(tmp_path: pathlib.Path) -> None:
    (tmp_path / "cmake.toml").write_text(
        textwrap.dedent("""\
        ["*"]
        description = "test"
        ignore_missing_downstream = ["cmake/data/bin/*"]
    """)
    )
    policies = load_policy_dir(tmp_path)
    assert "cmake" in policies
    assert policies["cmake"].versions["*"].ignore_missing_downstream == ("cmake/data/bin/*",)
    assert policies["cmake"].versions["*"].description == "test"


def test_load_policy_dir_empty(tmp_path: pathlib.Path) -> None:
    policies = load_policy_dir(tmp_path)
    assert policies == {}


def test_load_policy_dir_invalid_filename(tmp_path: pathlib.Path) -> None:
    (tmp_path / "Bad-Name.toml").write_text('["*"]\n')
    with pytest.raises(PolicyError, match="not normalized"):
        load_policy_dir(tmp_path)


def test_load_policy_dir_invalid_toml(tmp_path: pathlib.Path) -> None:
    (tmp_path / "foo.toml").write_text("not valid toml [[[")
    with pytest.raises(PolicyError, match="invalid TOML"):
        load_policy_dir(tmp_path)


def test_load_policy_dir_unknown_key(tmp_path: pathlib.Path) -> None:
    (tmp_path / "foo.toml").write_text('["*"]\nbogus = true\n')
    with pytest.raises(PolicyError, match="unknown keys"):
        load_policy_dir(tmp_path)


def test_load_policy_dir_wrong_type(tmp_path: pathlib.Path) -> None:
    (tmp_path / "foo.toml").write_text('["*"]\nignore_differences = "not a list"\n')
    with pytest.raises(PolicyError, match="must be a list"):
        load_policy_dir(tmp_path)


def test_load_policy_dir_top_level_not_table(tmp_path: pathlib.Path) -> None:
    (tmp_path / "foo.toml").write_text('x = "not a table"\n')
    with pytest.raises(PolicyError, match="must be a table"):
        load_policy_dir(tmp_path)


# --- lookup_policy ---


def test_lookup_policy_found() -> None:
    vp = VersionPolicy(
        description="test",
        ignore_differences=("foo.py",),
        ignore_missing_downstream=(),
        ignore_extra_downstream=(),
        platlib=False,
    )
    policies = {"cmake": PackagePolicy(dist_name="cmake", versions={"*": vp})}
    assert lookup_policy(policies, "cmake", "4.4.2") is vp


def test_lookup_policy_canonical_name() -> None:
    vp = VersionPolicy(
        description="",
        ignore_differences=(),
        ignore_missing_downstream=(),
        ignore_extra_downstream=(),
        platlib=False,
    )
    policies = {"scikit-learn": PackagePolicy(dist_name="scikit-learn", versions={"*": vp})}
    assert lookup_policy(policies, "scikit_learn", "1.0") is vp


def test_lookup_policy_missing_package() -> None:
    assert lookup_policy({}, "cmake", "4.4.2") is None


def test_lookup_policy_no_wildcard() -> None:
    policies = {
        "cmake": PackagePolicy(
            dist_name="cmake",
            versions={
                ">=5.0": VersionPolicy(
                    description="",
                    ignore_differences=(),
                    ignore_missing_downstream=(),
                    ignore_extra_downstream=(),
                    platlib=False,
                )
            },
        )
    }
    assert lookup_policy(policies, "cmake", "4.4.2") is None


# --- apply_policy ---


def _make_result(
    only_upstream: tuple[FileEntry, ...] = (),
    only_downstream: tuple[FileEntry, ...] = (),
    different: tuple[FileDiff, ...] = (),
    metadata_field_diffs: tuple[MetadataFieldDiff, ...] = (),
    platform_warnings: tuple[PlatformWarning, ...] = (),
) -> Comparison:
    return make_comparison(
        only_upstream=only_upstream,
        only_downstream=only_downstream,
        different=different,
        metadata_field_diffs=metadata_field_diffs,
        platform_warnings=platform_warnings,
    )


def test_apply_policy_ignore_missing_downstream() -> None:
    entry = FileEntry("cmake/data/bin/ccmake", Severity.ERROR, Classification.OTHER)
    result = _make_result(only_upstream=(entry,))
    policy = VersionPolicy(
        description="test",
        ignore_differences=(),
        ignore_missing_downstream=("cmake/data/bin/*",),
        ignore_extra_downstream=(),
        platlib=False,
    )
    apply_policy(result, policy)
    assert result.analysis.only_upstream[0].severity is Severity.IGNORED
    assert not result.has_errors


def test_apply_policy_ignore_differences() -> None:
    diff = FileDiff(
        "cmake/data/bin/cmake", 100, 200, 111, 222, Severity.ERROR, Classification.OTHER
    )
    result = _make_result(different=(diff,))
    policy = VersionPolicy(
        description="test",
        ignore_differences=("cmake/data/bin/cmake",),
        ignore_missing_downstream=(),
        ignore_extra_downstream=(),
        platlib=False,
    )
    apply_policy(result, policy)
    assert result.analysis.different[0].severity is Severity.IGNORED
    assert not result.has_errors


def test_apply_policy_no_match() -> None:
    entry = FileEntry("other/file.py", Severity.ERROR, Classification.OTHER)
    result = _make_result(only_upstream=(entry,))
    policy = VersionPolicy(
        description="test",
        ignore_differences=(),
        ignore_missing_downstream=("cmake/data/bin/*",),
        ignore_extra_downstream=(),
        platlib=False,
    )
    apply_policy(result, policy)
    assert result.analysis.only_upstream[0].severity is Severity.ERROR  # unchanged
    assert result.has_errors


def test_apply_policy_empty() -> None:
    entry = FileEntry("file.py", Severity.ERROR, Classification.OTHER)
    result = _make_result(only_upstream=(entry,))
    policy = VersionPolicy(
        description="test",
        ignore_differences=(),
        ignore_missing_downstream=(),
        ignore_extra_downstream=(),
        platlib=False,
    )
    apply_policy(result, policy)
    assert result.analysis.only_upstream[0].severity is Severity.ERROR


# --- apply_policy: metadata field diffs ---


def _vp(**kwargs: object) -> VersionPolicy:
    """Build a VersionPolicy with empty defaults, overriding via kwargs."""
    base = {
        "description": "test",
        "ignore_differences": (),
        "ignore_missing_downstream": (),
        "ignore_extra_downstream": (),
        "platlib": False,
    }
    base.update(kwargs)
    return VersionPolicy(**base)


def test_apply_policy_ignore_dependency_metadata() -> None:
    """The flag marks both Requires-Dist and Provides-Extra diffs ignored."""
    diffs = (
        MetadataFieldDiff("Requires-Dist", ("torch>=2.0", "numpy>=1.20"), ("torch>=2.0.post1",)),
        MetadataFieldDiff("Provides-Extra", ("cuda",), ()),
    )
    result = _make_result(metadata_field_diffs=diffs)
    apply_policy(result, _vp(ignore_dependency_metadata=True))
    # Diffs are still reported (not dropped), but flagged as ignored and
    # their entries preserved.
    field_diffs = result.analysis.metadata_field_diffs
    assert len(field_diffs) == 2
    assert all(d.ignored for d in field_diffs)
    assert field_diffs[0].only_upstream == ("torch>=2.0", "numpy>=1.20")


def test_apply_policy_ignore_dependency_metadata_leaves_other_fields() -> None:
    """Only dependency fields are marked ignored; other field diffs are untouched."""
    diffs = (
        MetadataFieldDiff("Requires-Dist", ("torch>=2.0",), ()),
        MetadataFieldDiff("Classifier", ("Private :: Do Not Upload",), ()),
    )
    result = _make_result(metadata_field_diffs=diffs)
    apply_policy(result, _vp(ignore_dependency_metadata=True))
    by_field = {d.field: d for d in result.analysis.metadata_field_diffs}
    assert by_field["Requires-Dist"].ignored is True
    assert by_field["Classifier"].ignored is False


def test_apply_policy_ignore_dependency_metadata_disabled() -> None:
    """Without the flag, dependency diffs are left untouched."""
    diff = MetadataFieldDiff("Requires-Dist", ("torch>=2.0",), ())
    result = _make_result(metadata_field_diffs=(diff,))
    apply_policy(result, _vp(ignore_dependency_metadata=False))
    assert result.analysis.metadata_field_diffs[0].ignored is False


def test_apply_policy_ignore_dependency_metadata_idempotent() -> None:
    """Re-applying the flag to already-ignored diffs is a no-op."""
    diff = MetadataFieldDiff("Requires-Dist", ("torch>=2.0",), (), ignored=True)
    result = _make_result(metadata_field_diffs=(diff,))
    apply_policy(result, _vp(ignore_dependency_metadata=True))
    assert result.analysis.metadata_field_diffs[0].ignored is True


def _metadata_comparison(up_meta: bytes, down_meta: bytes, meta_diff: FileDiff) -> Comparison:
    """Build a Comparison whose wheels carry METADATA and one METADATA FileDiff."""
    up = make_wheel_info("foo-1.0-py3-none-any.whl", [], source="up", metadata=up_meta)
    down = make_wheel_info("foo-1.0-py3-none-any.whl", [], source="down", metadata=down_meta)
    return Comparison(
        context=Context.default(),
        upstream=up,
        downstream=down,
        analysis=Analysis(different=[meta_diff]),
    )


def test_apply_policy_ignore_dependency_metadata_downgrades_error() -> None:
    """A METADATA file error is downgraded to IGNORED when Name/Version match."""
    meta_diff = FileDiff(
        "foo-1.0.dist-info/METADATA", 10, 20, 1, 2, Severity.ERROR, Classification.DIST_INFO
    )
    result = _metadata_comparison(
        b"Metadata-Version: 2.1\nName: foo\nVersion: 1.0\nRequires-Dist: a\n",
        b"Metadata-Version: 2.1\nName: Foo\nVersion: 1.0\nRequires-Dist: b\n",
        meta_diff,
    )
    apply_policy(result, _vp(ignore_dependency_metadata=True))
    assert result.analysis.different[0].severity is Severity.IGNORED
    assert not result.has_errors


def test_apply_policy_ignore_dependency_metadata_keeps_version_mismatch() -> None:
    """A METADATA file error survives when Name/Version genuinely differ."""
    meta_diff = FileDiff(
        "foo-1.0.dist-info/METADATA", 10, 20, 1, 2, Severity.ERROR, Classification.DIST_INFO
    )
    result = _metadata_comparison(
        b"Metadata-Version: 2.1\nName: foo\nVersion: 1.0\n",
        b"Metadata-Version: 2.1\nName: foo\nVersion: 2.0\n",
        meta_diff,
    )
    apply_policy(result, _vp(ignore_dependency_metadata=True))
    assert result.analysis.different[0].severity is Severity.ERROR
    assert result.has_errors


# --- apply_policy: platlib ---


def test_apply_policy_platlib_removes_no_shared_libs_warning() -> None:
    """platlib drops the canonical no-shared-libraries warning only."""
    keep = PlatformWarning("upstream", "some other platform warning")
    drop = PlatformWarning("downstream", NO_SHARED_LIBS_WARNING)
    result = _make_result(platform_warnings=(keep, drop))
    apply_policy(result, _vp(platlib=True))
    assert result.analysis.platform_warnings == [keep]


def test_apply_policy_platlib_no_matching_warning() -> None:
    """platlib leaves unrelated warnings and returns the result unchanged."""
    keep = PlatformWarning("upstream", "some other platform warning")
    result = _make_result(platform_warnings=(keep,))
    apply_policy(result, _vp(platlib=True))
    assert result.analysis.platform_warnings == [keep]


def test_load_policy_dir_dependency_metadata_key(tmp_path: pathlib.Path) -> None:
    (tmp_path / "foo.toml").write_text(
        textwrap.dedent("""\
        ["*"]
        description = "test"
        ignore_dependency_metadata = true
    """)
    )
    vp = load_policy_dir(tmp_path)["foo"].versions["*"]
    assert vp.ignore_dependency_metadata is True


def test_policy_dependency_metadata_must_be_bool(tmp_path: pathlib.Path) -> None:
    (tmp_path / "foo.toml").write_text(
        textwrap.dedent("""\
        ["*"]
        description = "test"
        ignore_dependency_metadata = ["torch"]
    """)
    )
    with pytest.raises(PolicyError, match="ignore_dependency_metadata"):
        load_policy_dir(tmp_path)


# --- example policies ---


def test_load_example_policies() -> None:
    """All example policy files in examples/policies/ should load without error."""
    policy_dir = pathlib.Path(__file__).resolve().parent.parent / "examples" / "policies"
    if not policy_dir.is_dir():
        pytest.skip("examples/policies/ not found")
    policies = load_policy_dir(policy_dir)
    assert len(policies) > 0
    for _name, pkg in policies.items():
        assert "*" in pkg.versions
        assert pkg.versions["*"].description

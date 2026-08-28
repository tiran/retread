"""Tests for retread._platform (platform and ABI checks)."""

import json

import pytest
from packaging.tags import Tag
from packaging.version import Version

from retread.__main__ import _print_comparison, _print_json
from retread._compare import WheelComparison
from retread._platform import (
    PlatformWarning,
    _check_single_wheel,
    _expand_compound_tags,
    _parse_wheel_tags,
    check_platform_abi,
)

from .conftest import FakeInfo, make_wheel_file

# --- _parse_wheel_tags ---


def test_parse_wheel_tags_purelib_true():
    wheel_bytes = make_wheel_file(True, ["cp312-cp312-linux_x86_64"])
    root_is_purelib, tags = _parse_wheel_tags(wheel_bytes)
    assert root_is_purelib is True
    assert tags == ["cp312-cp312-linux_x86_64"]


def test_parse_wheel_tags_purelib_false():
    wheel_bytes = make_wheel_file(False, ["cp312-cp312-linux_x86_64"])
    root_is_purelib, _tags = _parse_wheel_tags(wheel_bytes)
    assert root_is_purelib is False


def test_parse_wheel_tags_missing_purelib():
    """Missing Root-Is-Purelib defaults to false."""
    wheel_bytes = b"Wheel-Version: 1.0\nTag: py3-none-any\n"
    root_is_purelib, tags = _parse_wheel_tags(wheel_bytes)
    assert root_is_purelib is False
    assert tags == ["py3-none-any"]


def test_parse_wheel_tags_multiple_tags():
    wheel_bytes = make_wheel_file(False, ["cp312-cp312-linux_x86_64", "cp312-abi3-linux_x86_64"])
    _, tags = _parse_wheel_tags(wheel_bytes)
    assert len(tags) == 2
    assert "cp312-cp312-linux_x86_64" in tags
    assert "cp312-abi3-linux_x86_64" in tags


# --- _check_single_wheel ---

_LINUX_TAGS = frozenset({Tag("cp312", "cp312", "linux_x86_64")})
_PURE_TAGS = frozenset({Tag("py3", "none", "any")})
_ABI3_TAGS = frozenset({Tag("cp312", "abi3", "linux_x86_64")})


def test_pure_python_no_warnings():
    """Pure python wheel with no .so files produces no warnings."""
    infos = {"foo/__init__.py": FakeInfo("foo/__init__.py")}
    wheel_bytes = make_wheel_file(True, ["py3-none-any"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _PURE_TAGS, "foo", "1.0")
    assert warnings == []


def test_wheel_tags_match_filename_tags():
    """Matching WHEEL Tag entries and filename tags produce no warning."""
    infos = {"foo/__init__.py": FakeInfo("foo/__init__.py")}
    wheel_bytes = make_wheel_file(False, ["cp312-cp312-linux_x86_64"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _LINUX_TAGS, "foo", "1.0")
    assert not any("Tag entries" in w.message for w in warnings)


def test_wheel_tags_mismatch_filename_tags():
    """Mismatched WHEEL Tag entries and filename tags produce a warning."""
    infos = {"foo/__init__.py": FakeInfo("foo/__init__.py")}
    # WHEEL says cp313 but filename says cp312
    wheel_bytes = make_wheel_file(False, ["cp313-cp313-linux_x86_64"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _LINUX_TAGS, "foo", "1.0")
    tag_warns = [w for w in warnings if "Tag entries" in w.message]
    assert len(tag_warns) == 1
    assert "only in WHEEL: cp313-cp313-linux_x86_64" in tag_warns[0].message
    assert "only in filename: cp312-cp312-linux_x86_64" in tag_warns[0].message


def test_wheel_tags_subset_of_filename_tags():
    """WHEEL with fewer tags than filename produces a warning."""
    infos = {"foo/__init__.py": FakeInfo("foo/__init__.py")}
    multi_tags = frozenset(
        {
            Tag("cp312", "cp312", "linux_x86_64"),
            Tag("cp312", "cp312", "manylinux_2_28_x86_64"),
        }
    )
    # WHEEL only has one of the two tags
    wheel_bytes = make_wheel_file(False, ["cp312-cp312-linux_x86_64"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, multi_tags, "foo", "1.0")
    tag_warns = [w for w in warnings if "Tag entries" in w.message]
    assert len(tag_warns) == 1
    assert "only in filename" in tag_warns[0].message


def test_expand_compound_platform_tags():
    """Compound platform tags should expand into individual tags."""
    tags = ["cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64"]
    assert _expand_compound_tags(tags) == {
        "cp312-cp312-manylinux_2_17_x86_64",
        "cp312-cp312-manylinux2014_x86_64",
    }


def test_expand_compound_interpreter_tags():
    """Compound interpreter tags should expand into individual tags."""
    tags = ["py2.py3-none-any"]
    assert _expand_compound_tags(tags) == {"py2-none-any", "py3-none-any"}


def test_compound_wheel_tags_match_filename():
    """Compound WHEEL tags should match expanded filename tags."""
    infos = {"foo/__init__.py": FakeInfo("foo/__init__.py")}
    multi_tags = frozenset(
        {
            Tag("cp312", "cp312", "manylinux_2_17_x86_64"),
            Tag("cp312", "cp312", "manylinux2014_x86_64"),
        }
    )
    wheel_bytes = make_wheel_file(
        False, ["cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64"]
    )
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, multi_tags, "foo", "1.0")
    assert not any("Tag entries" in w.message for w in warnings)


def test_compound_interpreter_tags_match_filename():
    """Compound interpreter WHEEL tags should match expanded filename tags."""
    infos = {"foo/__init__.py": FakeInfo("foo/__init__.py")}
    multi_tags = frozenset({Tag("py2", "none", "any"), Tag("py3", "none", "any")})
    wheel_bytes = make_wheel_file(True, ["py2.py3-none-any"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, multi_tags, "foo", "1.0")
    assert not any("Tag entries" in w.message for w in warnings)


def test_platlib_no_shared_libs_warning():
    """Platlib wheel with no shared libs produces a warning."""
    infos = {"foo/__init__.py": FakeInfo("foo/__init__.py")}
    wheel_bytes = make_wheel_file(False, ["cp312-cp312-linux_x86_64"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _LINUX_TAGS, "foo", "1.0")
    assert any("platform-specific tags but contains no shared" in w.message for w in warnings)


def test_purelib_platform_specific_no_shared_libs_warning():
    """Purelib wheel with platform-specific tags but no shared libs produces a warning."""
    infos = {"foo/__init__.py": FakeInfo("foo/__init__.py")}
    manylinux_tags = frozenset({Tag("py3", "none", "manylinux_2_28_x86_64")})
    wheel_bytes = make_wheel_file(True, ["py3-none-manylinux_2_28_x86_64"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, manylinux_tags, "foo", "1.0")
    assert any("platform-specific tags but contains no shared" in w.message for w in warnings)


def test_purelib_any_no_shared_libs_no_warning():
    """Purelib wheel with 'any' tag and no shared libs produces no platlib warning."""
    infos = {"foo/__init__.py": FakeInfo("foo/__init__.py")}
    wheel_bytes = make_wheel_file(True, ["py3-none-any"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _PURE_TAGS, "foo", "1.0")
    assert not any("platform-specific tags" in w.message for w in warnings)


def test_shared_lib_purelib_warning():
    """Shared lib in a purelib wheel produces a warning."""
    infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py"),
        "foo/bar.so": FakeInfo("foo/bar.so"),
    }
    wheel_bytes = make_wheel_file(True, ["cp312-cp312-linux_x86_64"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _LINUX_TAGS, "foo", "1.0")
    assert any("Root-Is-Purelib" in w.message for w in warnings)


def test_shared_lib_platform_any_warning():
    """Shared lib with platform tag 'any' produces a warning."""
    infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py"),
        "foo/bar.so": FakeInfo("foo/bar.so"),
    }
    wheel_bytes = make_wheel_file(False, ["py3-none-any"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _PURE_TAGS, "foo", "1.0")
    assert any("platform tag 'any'" in w.message for w in warnings)


def test_shared_lib_platlib_no_purelib_warning():
    """Shared lib with platlib and linux tag produces no Root-Is-Purelib warning."""
    infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py"),
        "foo/bar.so": FakeInfo("foo/bar.so"),
    }
    wheel_bytes = make_wheel_file(False, ["cp312-cp312-linux_x86_64"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _LINUX_TAGS, "foo", "1.0")
    assert not any("Root-Is-Purelib" in w.message for w in warnings)
    assert not any("platform tag 'any'" in w.message for w in warnings)


def test_cpython_ext_correct_version():
    """cpython extension with matching version tag produces no warning."""
    infos = {
        "foo/_bar.cpython-312-x86_64-linux-gnu.so": FakeInfo(
            "foo/_bar.cpython-312-x86_64-linux-gnu.so"
        ),
    }
    wheel_bytes = make_wheel_file(False, ["cp312-cp312-linux_x86_64"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _LINUX_TAGS, "foo", "1.0")
    assert not any("cpython-specific" in w.message for w in warnings)


def test_cpython_ext_wrong_version():
    """cpython extension with wrong version tag produces a warning."""
    infos = {
        "foo/_bar.cpython-314-x86_64-linux-gnu.so": FakeInfo(
            "foo/_bar.cpython-314-x86_64-linux-gnu.so"
        ),
    }
    wheel_bytes = make_wheel_file(False, ["cp312-cp312-linux_x86_64"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _LINUX_TAGS, "foo", "1.0")
    assert any("cp314" in w.message for w in warnings)


def test_cpython_ext_free_threaded():
    """cpython free-threaded extension with correct tags produces no version warning."""
    infos = {
        "foo/_bar.cpython-313t-x86_64-linux-gnu.so": FakeInfo(
            "foo/_bar.cpython-313t-x86_64-linux-gnu.so"
        ),
    }
    tags_313t = frozenset({Tag("cp313", "cp313t", "linux_x86_64")})
    wheel_bytes = make_wheel_file(False, ["cp313-cp313t-linux_x86_64"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, tags_313t, "foo", "1.0")
    # The trailing 't' is stripped: "313t" → interpreter "cp313", which matches
    cpython_warns = [w for w in warnings if "cpython-specific" in w.message]
    assert cpython_warns == []


def test_cpython_ext_free_threaded_wrong_version():
    """cpython free-threaded extension with wrong version tag produces a warning."""
    infos = {
        "foo/_bar.cpython-313t-x86_64-linux-gnu.so": FakeInfo(
            "foo/_bar.cpython-313t-x86_64-linux-gnu.so"
        ),
    }
    wheel_bytes = make_wheel_file(False, ["cp312-cp312-linux_x86_64"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _LINUX_TAGS, "foo", "1.0")
    cpython_warns = [w for w in warnings if "cpython-specific" in w.message]
    assert any("cp313" in w.message for w in cpython_warns)


def test_abi3_ext_with_abi3_tag():
    """abi3 extension with abi3 tag produces no abi3-related warning."""
    infos = {
        "foo/_bar.abi3.so": FakeInfo("foo/_bar.abi3.so"),
    }
    wheel_bytes = make_wheel_file(False, ["cp312-abi3-linux_x86_64"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _ABI3_TAGS, "foo", "1.0")
    assert not any("abi3" in w.message for w in warnings)


def test_abi3_ext_without_abi3_tag():
    """abi3 extension without abi3 tag produces a warning."""
    infos = {
        "foo/_bar.abi3.so": FakeInfo("foo/_bar.abi3.so"),
    }
    wheel_bytes = make_wheel_file(False, ["cp312-cp312-linux_x86_64"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _LINUX_TAGS, "foo", "1.0")
    assert any("abi3" in w.message and "ABI" in w.message for w in warnings)


def test_abi3t_ext_with_abi3_tag():
    """abi3t extension with abi3 tag produces no abi3-related warning."""
    infos = {
        "foo/_bar.abi3t.so": FakeInfo("foo/_bar.abi3t.so"),
    }
    wheel_bytes = make_wheel_file(False, ["cp313-abi3-linux_x86_64"])
    abi3_tags = frozenset({Tag("cp313", "abi3", "linux_x86_64")})
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, abi3_tags, "foo", "1.0")
    assert not any("abi3" in w.message for w in warnings)


def test_abi3_ext_with_cpython_ext_no_warning():
    """abi3 extension alongside cpython-specific extension produces no abi3 warning."""
    infos = {
        "foo/_bar.abi3.so": FakeInfo("foo/_bar.abi3.so"),
        "foo/_baz.cpython-312-x86_64-linux-gnu.so": FakeInfo(
            "foo/_baz.cpython-312-x86_64-linux-gnu.so"
        ),
    }
    wheel_bytes = make_wheel_file(False, ["cp312-cp312-linux_x86_64"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _LINUX_TAGS, "foo", "1.0")
    assert not any("abi3" in w.message for w in warnings)


def test_abi3_ext_without_cpython_interp():
    """abi3 extension without CPython interpreter produces a warning."""
    infos = {
        "foo/_bar.abi3.so": FakeInfo("foo/_bar.abi3.so"),
    }
    # pp310 is PyPy, not CPython
    pp_tags = frozenset({Tag("pp310", "abi3", "linux_x86_64")})
    wheel_bytes = make_wheel_file(False, ["pp310-abi3-linux_x86_64"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, pp_tags, "foo", "1.0")
    assert any("CPython interpreter" in w.message for w in warnings)


def test_auditwheel_libs_skipped():
    """Files in .libs/ directories are skipped for extension checks."""
    infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py"),
        "foo.libs/libbar.so.1": FakeInfo("foo.libs/libbar.so.1"),
    }
    wheel_bytes = make_wheel_file(True, ["py3-none-any"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _PURE_TAGS, "foo", "1.0")
    assert warnings == []


def test_large_scripts_heuristic():
    """Large files in data/scripts/ with purelib produces a heuristic warning."""
    infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py"),
        "foo-1.0.data/scripts/mybin": FakeInfo("foo-1.0.data/scripts/mybin", file_size=131072),
    }
    wheel_bytes = make_wheel_file(True, ["py3-none-any"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _PURE_TAGS, "foo", "1.0")
    assert any("heuristic" in w.message for w in warnings)


def test_small_scripts_no_warning():
    """Small files in data/scripts/ produce no heuristic warning."""
    infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py"),
        "foo-1.0.data/scripts/wrapper": FakeInfo("foo-1.0.data/scripts/wrapper", file_size=256),
    }
    wheel_bytes = make_wheel_file(True, ["py3-none-any"])
    warnings = _check_single_wheel("upstream", infos, wheel_bytes, _PURE_TAGS, "foo", "1.0")
    assert warnings == []


def test_no_wheel_file():
    """Missing WHEEL file still checks tag consistency."""
    infos = {
        "foo/bar.so": FakeInfo("foo/bar.so"),
    }
    warnings = _check_single_wheel("upstream", infos, None, _PURE_TAGS, "foo", "1.0")
    # Should warn about platform 'any' but not about Root-Is-Purelib
    assert any("platform tag 'any'" in w.message for w in warnings)
    assert not any("Root-Is-Purelib" in w.message for w in warnings)


# --- version validation ---


def test_normalized_version_no_warning():
    """Normalized version in filename produces no version warning."""
    infos = {"foo/__init__.py": FakeInfo("foo/__init__.py")}
    wheel_bytes = make_wheel_file(True, ["py3-none-any"])
    warnings = _check_single_wheel(
        "upstream",
        infos,
        wheel_bytes,
        _PURE_TAGS,
        "foo",
        "1.0",
        wheel_filename="foo-1.0-py3-none-any.whl",
    )
    assert not any("not normalized" in w.message for w in warnings)


def test_non_normalized_version_warning():
    """Non-normalized version in filename produces a warning."""
    infos = {"foo/__init__.py": FakeInfo("foo/__init__.py")}
    wheel_bytes = make_wheel_file(True, ["py3-none-any"])
    warnings = _check_single_wheel(
        "upstream",
        infos,
        wheel_bytes,
        _PURE_TAGS,
        "foo",
        "1.01.0",
        wheel_filename="foo-1.01.0-py3-none-any.whl",
    )
    ver_warns = [w for w in warnings if "not normalized" in w.message]
    assert len(ver_warns) == 1
    assert "'1.01.0'" in ver_warns[0].message
    assert "'1.1.0'" in ver_warns[0].message


@pytest.mark.parametrize(
    ("dist", "version", "meta_name", "meta_version", "expected"),
    [
        # Name and Version both match the wheel filename
        ("foo", "1.0", "foo", "1.0", None),
        # Version differs from the filename version
        ("foo", "1.0", "foo", "2.0", "METADATA Version"),
        # Name differs from the filename distribution
        ("foo", "1.0", "bar", "1.0", "METADATA Name"),
        # Name matches after normalization (case, underscore vs hyphen)
        ("foo-bar", "1.0", "Foo_Bar", "1.0", None),
        # Missing Version field is an error
        ("foo", "1.0", "foo", None, "METADATA Version"),
        # Missing Name field is an error
        ("foo", "1.0", None, "1.0", "METADATA Name"),
    ],
    ids=[
        "match",
        "version-mismatch",
        "name-mismatch",
        "name-normalized",
        "version-missing",
        "name-missing",
    ],
)
def test_metadata_matches_filename(dist, version, meta_name, meta_version, expected):
    """METADATA Name and Version must be present and match the filename."""
    infos = {"foo/__init__.py": FakeInfo("foo/__init__.py")}
    wheel_bytes = make_wheel_file(True, ["py3-none-any"])
    lines = ["Metadata-Version: 2.1"]
    if meta_name is not None:
        lines.append(f"Name: {meta_name}")
    if meta_version is not None:
        lines.append(f"Version: {meta_version}")
    metadata = "\n".join(lines).encode()
    warnings = _check_single_wheel(
        "upstream",
        infos,
        wheel_bytes,
        _PURE_TAGS,
        dist,
        version,
        metadata_bytes=metadata,
        wheel_filename=f"{dist.replace('-', '_')}-{version}-py3-none-any.whl",
    )
    meta_warns = [
        w for w in warnings if "METADATA Name" in w.message or "METADATA Version" in w.message
    ]
    if expected is None:
        assert meta_warns == []
    else:
        assert len(meta_warns) == 1
        assert expected in meta_warns[0].message


def test_missing_metadata_no_crash():
    """Missing METADATA bytes does not crash version check."""
    infos = {"foo/__init__.py": FakeInfo("foo/__init__.py")}
    wheel_bytes = make_wheel_file(True, ["py3-none-any"])
    warnings = _check_single_wheel(
        "upstream",
        infos,
        wheel_bytes,
        _PURE_TAGS,
        "foo",
        "1.0",
        metadata_bytes=None,
        wheel_filename="foo-1.0-py3-none-any.whl",
    )
    assert not any("METADATA Version" in w.message for w in warnings)


# --- check_platform_abi ---


def _make_result(
    upstream_wheel: str = "foo-1.0-cp312-cp312-linux_x86_64.whl",
    downstream_wheel: str = "foo-1.0-cp312-cp312-linux_x86_64.whl",
) -> WheelComparison:
    return WheelComparison(
        upstream="up",
        downstream="down",
        upstream_wheel=upstream_wheel,
        downstream_wheel=downstream_wheel,
        dist="foo",
        upstream_version=Version("1.0"),
        downstream_version=Version("1.0"),
        only_upstream=(),
        only_downstream=(),
        different=(),
        identical=("foo/__init__.py",),
    )


def test_check_platform_abi_no_warnings():
    """Correctly tagged platlib wheel produces no warnings."""
    result = _make_result()
    infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py"),
        "foo/bar.cpython-312-x86_64-linux-gnu.so": FakeInfo(
            "foo/bar.cpython-312-x86_64-linux-gnu.so"
        ),
    }
    wheel_bytes = make_wheel_file(False, ["cp312-cp312-linux_x86_64"])
    checked = check_platform_abi(
        result,
        upstream_infos=infos,
        downstream_infos=infos,
        upstream_wheel=wheel_bytes,
        downstream_wheel=wheel_bytes,
    )
    assert checked.platform_warnings == ()


def test_check_platform_abi_with_warnings():
    """Purelib wheel with .so file produces platform warnings."""
    result = _make_result(
        upstream_wheel="foo-1.0-py3-none-any.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
    )
    infos = {
        "foo/__init__.py": FakeInfo("foo/__init__.py"),
        "foo/bar.so": FakeInfo("foo/bar.so"),
    }
    wheel_bytes = make_wheel_file(True, ["py3-none-any"])
    checked = check_platform_abi(
        result,
        upstream_infos=infos,
        downstream_infos=infos,
        upstream_wheel=wheel_bytes,
        downstream_wheel=wheel_bytes,
    )
    assert len(checked.platform_warnings) > 0


def test_has_errors_with_platform_warnings():
    """has_errors returns True when platform_warnings are present."""
    result = _make_result()
    infos = {"foo/bar.so": FakeInfo("foo/bar.so")}
    wheel_bytes = make_wheel_file(True, ["py3-none-any"])
    checked = check_platform_abi(
        result,
        upstream_infos=infos,
        downstream_infos={"foo/__init__.py": FakeInfo("foo/__init__.py")},
        upstream_wheel=wheel_bytes,
        downstream_wheel=make_wheel_file(False, ["cp312-cp312-linux_x86_64"]),
    )
    assert checked.has_errors


# --- CLI output ---


def test_print_comparison_platform_warnings(capsys):
    """Platform warnings appear in text output."""
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
        platform_warnings=(
            PlatformWarning("upstream", "Root-Is-Purelib is set but wheel contains shared libs"),
        ),
    )
    _print_comparison(result)
    out = capsys.readouterr().out
    assert "Platform warnings (1):" in out
    assert "[upstream] Root-Is-Purelib is set but wheel contains shared libs" in out


def test_print_json_platform_warnings(capsys):
    """Platform warnings appear in JSON output."""
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
        platform_warnings=(PlatformWarning("downstream", "abi3 extension but no abi3 tag"),),
    )
    _print_json(result)
    data = json.loads(capsys.readouterr().out)
    assert "platform_warnings" in data
    assert len(data["platform_warnings"]) == 1
    assert data["platform_warnings"][0]["side"] == "downstream"
    assert "abi3" in data["platform_warnings"][0]["message"]


def test_print_json_no_platform_warnings(capsys):
    """Empty platform_warnings appears as empty list in JSON."""
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
    assert data["platform_warnings"] == []

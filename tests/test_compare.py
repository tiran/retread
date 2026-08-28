"""Tests for retread._compare."""

import json
import zipfile

import pytest
from packaging.version import Version

from retread._compare import (
    Classification,
    FileDiff,
    FileEntry,
    MetadataFieldDiff,
    Severity,
    VenvBundle,
    WheelComparison,
    _check_metadata,
    _classify_file,
    _compare,
    _extension_stem,
    _find_dist_info_name,
    _is_auditwheel_lib,
    _is_shared_library,
    _local_zip_infos,
    _metadata_core_match,
    _metadata_field_diffs,
    compare_local_wheel,
    compare_wheels,
)
from retread._errors import InvalidWheelError
from retread._resolve import _is_url, _parse_name_version, _wheel_basename

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
        ("https://pkgs.example/foo.whl", True),
        ("http://pkgs.example/foo.whl", True),
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
        ("https://pkgs.example/path/foo-1.0-py3-none-any.whl", "foo-1.0-py3-none-any.whl"),
        (
            "https://pkgs.example/path/foo-1.0-py3-none-any.whl?token=abc#sha256=xyz",
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
        # static archives (lib*.a)
        ("numpy/.dylibs/libopenblas.a", False, EXPECTED, Classification.STATIC_LIBRARY),
        ("numpy/.dylibs/libopenblas.a", True, ERROR, Classification.STATIC_LIBRARY),
        # Java archives (.jar)
        ("org.jpype.jar", False, EXPECTED, Classification.JAR),
        ("org.jpype.jar", True, ERROR, Classification.JAR),
        # version files (always NOTICE)
        ("foo/_version.py", False, NOTICE, Classification.VERSION_FILE),
        ("foo/_version.py", True, NOTICE, Classification.VERSION_FILE),
        ("foo/version.py", False, NOTICE, Classification.VERSION_FILE),
        ("foo/sub/__version__.py", False, NOTICE, Classification.VERSION_FILE),
        ("numpy/__config__.py", False, NOTICE, Classification.VERSION_FILE),
        # namespace package .pth files (always NOTICE)
        (
            "sphinxcontrib_jsmath-1.0.1-py3.7-nspkg.pth",
            False,
            EXPECTED,
            Classification.NAMESPACE_PKG_PTH,
        ),
        (
            "sphinxcontrib_jsmath-1.0.1-py3.12-nspkg.pth",
            True,
            EXPECTED,
            Classification.NAMESPACE_PKG_PTH,
        ),
        # test directories (always NOTICE)
        ("foo/tests/test_bar.py", False, NOTICE, Classification.TEST),
        ("foo/tests/test_bar.py", True, NOTICE, Classification.TEST),
        ("foo/test/conftest.py", True, NOTICE, Classification.TEST),
        ("tests/test_main.py", True, NOTICE, Classification.TEST),
        ("foo/sub/tests/data.json", False, NOTICE, Classification.TEST),
        # protobuf-generated files (always NOTICE)
        ("foo/bar_pb2.py", False, NOTICE, Classification.GENERATED_PROTOBUF),
        ("foo/bar_pb2.py", True, NOTICE, Classification.GENERATED_PROTOBUF),
        ("foo/bar_pb2_grpc.py", False, NOTICE, Classification.GENERATED_PROTOBUF),
        ("foo/bar_pb2.pyi", False, NOTICE, Classification.GENERATED_PROTOBUF),
        ("foo/bar_pb2_grpc.pyi", True, NOTICE, Classification.GENERATED_PROTOBUF),
        # ANTLR-generated files (always NOTICE)
        ("foo/grammar/gen/FooLexer.py", False, NOTICE, Classification.GENERATED_ANTLR),
        ("foo/grammar/gen/FooParser.py", True, NOTICE, Classification.GENERATED_ANTLR),
        ("foo/grammar/gen/FooParserListener.py", False, NOTICE, Classification.GENERATED_ANTLR),
        ("foo/grammar/gen/FooParserVisitor.py", False, NOTICE, Classification.GENERATED_ANTLR),
        # C/C++ source and header files (always NOTICE)
        ("foo/bar.c", False, NOTICE, Classification.GENERATED_C),
        ("foo/bar.c", True, NOTICE, Classification.GENERATED_C),
        ("foo/bar.cpp", False, NOTICE, Classification.GENERATED_C),
        ("foo/bar.h", False, NOTICE, Classification.GENERATED_C),
        ("foo/bar.hpp", True, NOTICE, Classification.GENERATED_C),
        # build configuration files (always NOTICE)
        ("foo/FooConfig.cmake", False, NOTICE, Classification.GENERATED_BUILD_CONFIG),
        ("foo/FooConfig.cmake", True, NOTICE, Classification.GENERATED_BUILD_CONFIG),
        ("foo/libs/tbb.pc", False, NOTICE, Classification.GENERATED_BUILD_CONFIG),
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
        "static-lib-diff",
        "static-lib-missing",
        "jar-diff",
        "jar-missing",
        "version-file-diff",
        "version-file-missing",
        "version-file-alt",
        "version-file-nested",
        "version-file-config",
        "nspkg-pth-diff",
        "nspkg-pth-missing",
        "test-dir-diff",
        "test-dir-missing",
        "test-dir-singular",
        "test-dir-root",
        "test-dir-nested",
        "generated-pb2-diff",
        "generated-pb2-missing",
        "generated-pb2-grpc",
        "generated-pb2-pyi",
        "generated-pb2-grpc-pyi",
        "generated-antlr-lexer",
        "generated-antlr-parser",
        "generated-antlr-listener",
        "generated-antlr-visitor",
        "generated-c-diff",
        "generated-c-missing",
        "generated-cpp",
        "generated-h",
        "generated-hpp",
        "build-config-cmake-diff",
        "build-config-cmake-missing",
        "build-config-pc",
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


# --- _find_dist_info_name ---


def test_find_dist_info_name_exact_match() -> None:
    """When filename-derived name matches dist-info exactly, return it."""
    infos = {"foo-1.0.dist-info/RECORD": None}
    assert _find_dist_info_name(infos, "foo", "1.0") == "foo"


def test_find_dist_info_name_case_mismatch() -> None:
    """Lowercase dist-info for uppercase wheel filename (InquirerPy)."""
    infos = {"inquirerpy-0.3.4.dist-info/METADATA": None}
    assert _find_dist_info_name(infos, "InquirerPy", "0.3.4") == "inquirerpy"


def test_find_dist_info_name_uppercase_dist_info() -> None:
    """Uppercase dist-info for lowercase wheel filename (SCons)."""
    infos = {"SCons-4.5.2.dist-info/WHEEL": None}
    assert _find_dist_info_name(infos, "scons", "4.5.2") == "SCons"


def test_find_dist_info_name_dot_separator() -> None:
    """Dot-separated dist-info name (jaraco.classes)."""
    infos = {"jaraco.classes-3.4.0.dist-info/RECORD": None}
    assert _find_dist_info_name(infos, "jaraco_classes", "3.4.0") == "jaraco.classes"


def test_find_dist_info_name_no_dist_info_fallback() -> None:
    """When no dist-info directory is found, return the original name."""
    infos = {"foo/__init__.py": None}
    assert _find_dist_info_name(infos, "foo", "1.0") == "foo"


def test_find_dist_info_name_ignores_nested_dist_info() -> None:
    """Vendored dist-info nested under a subdirectory should be ignored."""
    infos = {
        "vendor/foo-1.0.dist-info/RECORD": None,
        "foo-1.0.dist-info/RECORD": None,
    }
    assert _find_dist_info_name(infos, "foo", "1.0") == "foo"


def test_find_dist_info_name_ignores_deeply_nested_dist_info() -> None:
    """Deeply nested dist-info (multiple slashes) should be ignored."""
    infos = {"a/b/foo-1.0.dist-info/RECORD": None}
    assert _find_dist_info_name(infos, "foo", "1.0") == "foo"


def test_find_dist_info_name_ignores_different_package() -> None:
    """A dist-info for a different package should be ignored."""
    infos = {"other_pkg-1.0.dist-info/RECORD": None}
    assert _find_dist_info_name(infos, "foo", "1.0") == "foo"


# --- _find_dist_info_name integration with _compare ---


def test_compare_resolves_dist_info_case_mismatch() -> None:
    """_compare() resolves dist-info name when casing differs from filename."""
    up_url = "https://pypi.org/InquirerPy-0.3.4-py3-none-any.whl"
    down_url = "https://rebuild.test/InquirerPy-0.3.4-1-py3-none-any.whl"
    up_infos = {
        "InquirerPy/__init__.py": FakeInfo("InquirerPy/__init__.py", crc=123, file_size=50),
        "inquirerpy-0.3.4.dist-info/RECORD": FakeInfo(
            "inquirerpy-0.3.4.dist-info/RECORD", crc=111, file_size=200
        ),
        "inquirerpy-0.3.4.dist-info/METADATA": FakeInfo(
            "inquirerpy-0.3.4.dist-info/METADATA", crc=333, file_size=100
        ),
    }
    down_infos = {
        "InquirerPy/__init__.py": FakeInfo("InquirerPy/__init__.py", crc=123, file_size=50),
        "inquirerpy-0.3.4.dist-info/RECORD": FakeInfo(
            "inquirerpy-0.3.4.dist-info/RECORD", crc=222, file_size=200
        ),
        "inquirerpy-0.3.4.dist-info/METADATA": FakeInfo(
            "inquirerpy-0.3.4.dist-info/METADATA", crc=444, file_size=100
        ),
    }
    result = _compare(
        upstream=up_url,
        downstream=down_url,
        upstream_infos=up_infos,
        downstream_infos=down_infos,
    )
    assert result.upstream_dist == "inquirerpy"
    assert result.downstream_dist == "inquirerpy"
    # RECORD should be EXPECTED, not ERROR
    record_diffs = [d for d in result.different if d.filename.endswith("/RECORD")]
    assert len(record_diffs) == 1
    assert record_diffs[0].severity is Severity.EXPECTED
    assert record_diffs[0].classification is Classification.RECORD
    # METADATA should be NOTICE, not ERROR
    meta_diffs = [d for d in result.different if d.filename.endswith("/METADATA")]
    assert len(meta_diffs) == 1
    assert meta_diffs[0].severity is Severity.NOTICE
    assert meta_diffs[0].classification is Classification.METADATA


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
        # normalized requires (whitespace)
        (("foo", "1.0", ["bar >= 1.0"]), ("foo", "1.0", ["bar>=1.0"]), True),
        # reordered requires
        (("foo", "1.0", ["a>=1", "b>=2"]), ("foo", "1.0", ["b>=2", "a>=1"]), True),
        # reordered extras
        (
            ("foo", "1.0", ["bar>=1.0"], ["docs", "testing"]),
            ("foo", "1.0", ["bar>=1.0"], ["testing", "docs"]),
            True,
        ),
        # hyphen vs underscore in dep name (PEP 503 equivalence)
        (
            ("foo", "1.0", ["typing-extensions>=3.7"]),
            ("foo", "1.0", ["typing_extensions>=3.7"]),
            True,
        ),
        # hyphen vs underscore in package Name (PEP 503 equivalence)
        (("typing-inspect", "0.9.0"), ("typing_inspect", "0.9.0"), True),
        # PEP 440 pre-release normalization (beta.43 == b43)
        (
            ("foo", "1.0", ["furo (>=2021.8.17-beta.43,<2022.0.0)"]),
            ("foo", "1.0", ["furo (>=2021.8.17b43,<2022.0.0)"]),
            True,
        ),
        # wildcard version specifier (==2.0.*)
        (
            ("foo", "1.0", ["bar==2.0.*"]),
            ("foo", "1.0", ["bar==2.0.*"]),
            True,
        ),
        # wildcard combined with normal specifier
        (
            ("foo", "1.0", ["bar (>=1.0,!=0.41.*)"]),
            ("foo", "1.0", ["bar>=1.0,!=0.41.*"]),
            True,
        ),
        # local version segment (1.5.0 vs 1.5.0+rhaiv.5)
        (("foo", "1.5.0", ["bar>=1.0"]), ("foo", "1.5.0+rhaiv.5", ["bar>=1.0"]), True),
        # different base version with local segment
        (("foo", "1.5.0"), ("foo", "2.0+rhaiv.5"), False),
    ],
    ids=[
        "identical",
        "different-name",
        "different-version",
        "different-requires",
        "normalized-requires",
        "reordered-requires",
        "reordered-extras",
        "normalized-dep-name",
        "normalized-pkg-name",
        "normalized-prerelease",
        "wildcard-version",
        "wildcard-combined",
        "local-version",
        "local-version-different-base",
    ],
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


# --- _metadata_field_diffs ---


@pytest.mark.parametrize(
    ("up", "down", "expected"),
    [
        # equal after normalization (reordered + whitespace)
        (
            (["a>=1", "b >= 2"], ["docs", "testing"]),
            (["b>=2", "a>=1"], ["testing", "docs"]),
            (),
        ),
        # only Requires-Dist differs (shared entry is dropped)
        (
            (["bar>=1.0", "shared==2.0"], []),
            (["baz<3", "shared==2.0"], []),
            (MetadataFieldDiff("Requires-Dist", ("bar>=1.0",), ("baz<3",)),),
        ),
        # only Provides-Extra differs
        (
            ([], ["docs", "shared"]),
            ([], ["shared", "extra-tools"]),
            (MetadataFieldDiff("Provides-Extra", ("docs",), ("extra-tools",)),),
        ),
        # both fields differ (Requires-Dist reported before Provides-Extra)
        (
            (["bar>=1.0"], ["docs"]),
            (["baz<3"], ["testing"]),
            (
                MetadataFieldDiff("Requires-Dist", ("bar>=1.0",), ("baz<3",)),
                MetadataFieldDiff("Provides-Extra", ("docs",), ("testing",)),
            ),
        ),
        # hyphen/underscore dep-name spellings compare equal (PEP 503)
        (
            (["typing-extensions>=3.7"], []),
            (["typing_extensions>=3.7"], []),
            (),
        ),
    ],
    ids=[
        "equal-after-normalization",
        "requires-dist",
        "provides-extra",
        "both-fields",
        "normalized-dep-name",
    ],
)
def test_metadata_field_diffs(up: tuple, down: tuple, expected: tuple) -> None:
    up_bytes = make_metadata("foo", "1.0", up[0], up[1])
    down_bytes = make_metadata("foo", "1.0", down[0], down[1])
    assert _metadata_field_diffs(up_bytes, down_bytes) == expected


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


def test_compare_dist_name_normalization() -> None:
    """upstream_dist is resolved from the actual dist-info directory."""
    up_url = "https://pypi.org/InquirerPy-0.3.4-py3-none-any.whl"
    down_url = "https://rebuild.test/inquirerpy-0.3.4-1-py3-none-any.whl"
    up_infos = {
        "InquirerPy/__init__.py": FakeInfo("InquirerPy/__init__.py", crc=123, file_size=50),
        "inquirerpy-0.3.4.dist-info/RECORD": FakeInfo(
            "inquirerpy-0.3.4.dist-info/RECORD", crc=111, file_size=200
        ),
    }
    down_infos = {
        "InquirerPy/__init__.py": FakeInfo("InquirerPy/__init__.py", crc=123, file_size=50),
        "inquirerpy-0.3.4.dist-info/RECORD": FakeInfo(
            "inquirerpy-0.3.4.dist-info/RECORD", crc=111, file_size=200
        ),
    }
    result = _compare(
        upstream=up_url,
        downstream=down_url,
        upstream_infos=up_infos,
        downstream_infos=down_infos,
    )
    assert result.dist == "inquirerpy"
    # upstream_dist resolved from dist-info, not from wheel filename
    assert result.upstream_dist == "inquirerpy"
    assert result.downstream_dist == "inquirerpy"
    assert result.is_identical


def test_compare_dist_name_normalization_classifies_downstream() -> None:
    """Downstream-only dist-info files use the downstream dist name for classification."""
    up_url = "https://pypi.org/InquirerPy-0.3.4-py3-none-any.whl"
    down_url = "https://rebuild.test/inquirerpy-0.3.4-1-py3-none-any.whl"
    # Upstream has InquirerPy-0.3.4.dist-info/, downstream has inquirerpy-0.3.4.dist-info/
    up_infos = {
        "InquirerPy/__init__.py": FakeInfo("InquirerPy/__init__.py", crc=123, file_size=50),
        "InquirerPy-0.3.4.dist-info/RECORD": FakeInfo(
            "InquirerPy-0.3.4.dist-info/RECORD", crc=111, file_size=200
        ),
    }
    down_infos = {
        "InquirerPy/__init__.py": FakeInfo("InquirerPy/__init__.py", crc=123, file_size=50),
        "inquirerpy-0.3.4.dist-info/RECORD": FakeInfo(
            "inquirerpy-0.3.4.dist-info/RECORD", crc=222, file_size=200
        ),
    }
    result = _compare(
        upstream=up_url,
        downstream=down_url,
        upstream_infos=up_infos,
        downstream_infos=down_infos,
    )
    # dist-info RECORD files should be classified as EXPECTED, not ERROR
    assert len(result.only_upstream) == 1
    assert result.only_upstream[0].classification is Classification.RECORD
    assert result.only_upstream[0].severity is Severity.EXPECTED
    assert len(result.only_downstream) == 1
    assert result.only_downstream[0].classification is Classification.RECORD
    assert result.only_downstream[0].severity is Severity.EXPECTED


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


# --- _extension_stem ---


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("foo/_bar.cpython-312-x86_64-linux-gnu.so", "foo/_bar"),
        ("foo/_bar.abi3.so", "foo/_bar"),
        ("foo/_bar.abi3t.so", "foo/_bar"),
        ("foo/_bar.so", "foo/_bar"),
        ("foo/bar.py", None),
        ("foo/bar.c", None),
        ("foo.libs/libbar.so.1", None),
    ],
    ids=["cpython", "abi3", "abi3t", "bare-so", "python", "c-source", "versioned-so"],
)
def test_extension_stem(filename: str, expected: str | None) -> None:
    assert _extension_stem(filename) == expected


# --- extension module ABI pairing ---


def test_compare_pairs_abi3_with_cpython_extension() -> None:
    """Extension modules with different ABI suffixes should be paired as NOTICE."""
    up_so = "foo/_bar.abi3.so"
    down_so = "foo/_bar.cpython-312-x86_64-linux-gnu.so"
    up = {up_so: FakeInfo(up_so, crc=111, file_size=1000)}
    down = {down_so: FakeInfo(down_so, crc=222, file_size=2000)}
    result = _compare(
        upstream=UPSTREAM_URL,
        downstream=DOWNSTREAM_URL,
        upstream_infos=up,
        downstream_infos=down,
    )
    assert len(result.only_upstream) == 1
    assert result.only_upstream[0].filename == up_so
    assert result.only_upstream[0].severity is Severity.NOTICE
    assert result.only_upstream[0].classification is Classification.EXTENSION_MODULE
    assert len(result.only_downstream) == 1
    assert result.only_downstream[0].filename == down_so
    assert result.only_downstream[0].severity is Severity.NOTICE
    assert result.only_downstream[0].classification is Classification.EXTENSION_MODULE


def test_compare_unpaired_extension_stays_error() -> None:
    """Extension modules with no ABI counterpart should remain ERROR."""
    up_so = "foo/_extra.cpython-312-x86_64-linux-gnu.so"
    up = {up_so: FakeInfo(up_so, crc=111, file_size=1000)}
    down: dict[str, FakeInfo] = {}
    result = _compare(
        upstream=UPSTREAM_URL,
        downstream=DOWNSTREAM_URL,
        upstream_infos=up,
        downstream_infos=down,
    )
    assert len(result.only_upstream) == 1
    assert result.only_upstream[0].severity is Severity.ERROR


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


def test_check_metadata_records_field_diffs(_metadata_diff_result) -> None:
    up = make_metadata("foo", "1.0", ["bar>=1.0"])
    down = make_metadata("foo", "1.0", ["bar>=2.0"])
    result = _check_metadata(
        _metadata_diff_result,
        {"foo-1.0.dist-info/METADATA": up}.__getitem__,
        {"foo-1.0.dist-info/METADATA": down}.__getitem__,
    )
    assert result.metadata_field_diffs == (
        MetadataFieldDiff("Requires-Dist", ("bar>=1.0",), ("bar>=2.0",)),
    )


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


# --- WheelComparison.to_dict ---


def test_to_dict() -> None:
    result = WheelComparison(
        upstream="up",
        downstream="down",
        upstream_wheel="foo-1.0-py3-none-any.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
        dist="foo",
        upstream_version=Version("1.0"),
        downstream_version=Version("1.0"),
        only_upstream=(FileEntry("gone.py", Severity.ERROR, Classification.OTHER),),
        only_downstream=(),
        different=(
            FileDiff(
                "foo.so", 100, 200, 111, 222, Severity.EXPECTED, Classification.EXTENSION_MODULE
            ),
        ),
        identical=("foo/__init__.py",),
        venv_bundles=(
            VenvBundle("downstream", Severity.ERROR, ".venv/lib/python3.11/site-packages/"),
        ),
    )
    data = result.to_dict()
    assert data["upstream_wheel"] == "foo-1.0-py3-none-any.whl"
    assert data["is_identical"] is False
    assert data["has_errors"] is True
    assert data["only_upstream"] == [
        {
            "filename": "gone.py",
            "side": "upstream",
            "severity": "error",
            "classification": "other",
        }
    ]
    assert data["different"][0]["filename"] == "foo.so"
    assert data["different"][0]["classification"] == "extension module"
    assert data["identical"] == ["foo/__init__.py"]
    assert data["venv_bundles"] == [
        {
            "side": "downstream",
            "severity": "error",
            "path": ".venv/lib/python3.11/site-packages/",
        }
    ]
    # The result must be JSON-serializable.
    assert json.loads(json.dumps(data)) == data

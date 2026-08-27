"""Shared test fixtures for retread."""

import zipfile

import pytest
from click.testing import CliRunner
from packaging.version import Version

from retread.__main__ import cli
from retread._compare import (
    Classification,
    FileDiff,
    FileEntry,
    Severity,
    WheelComparison,
)

# --- Fake objects for comparison tests ---


class FakeInfo:
    """Minimal stand-in for ZipInfo / RemoteZipInfo."""

    def __init__(self, filename: str, crc: int = 0, file_size: int = 100) -> None:
        self.filename = filename
        self.CRC = crc
        self.file_size = file_size

    def is_dir(self) -> bool:
        return self.filename.endswith("/")


class FakeRemoteZip:
    """Minimal stand-in for SyncRemoteZip."""

    def __init__(self, url: str, infos: list[FakeInfo], files: dict[str, bytes] | None = None):
        self._url = url
        self._infos = infos
        self._files = files or {}

    @property
    def url(self) -> str:
        return self._url

    def infolist(self) -> list[FakeInfo]:
        return self._infos

    def read(self, name: str) -> bytes:
        return self._files[name]


class FakePkg:
    """Minimal stand-in for pypi_simple.DistributionPackage."""

    def __init__(self, filename: str, url: str = "") -> None:
        self.filename = filename
        self.url = url or f"https://example.com/{filename}"


class FakePage:
    """Minimal stand-in for pypi_simple.ProjectPage."""

    def __init__(self, packages: list[FakePkg]) -> None:
        self.packages = packages


# --- Helper functions ---


def make_metadata(
    name: str,
    version: str,
    requires: list[str] | None = None,
    extras: list[str] | None = None,
) -> bytes:
    """Build a minimal METADATA file as bytes."""
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
    ]
    for extra in extras or []:
        lines.append(f"Provides-Extra: {extra}")
    for req in requires or []:
        lines.append(f"Requires-Dist: {req}")
    return "\n".join(lines).encode()


_FAKE_SHA256 = "A" * 43  # valid-length urlsafe-base64 sha256 digest


def make_record(files: dict[str, int], dist_info: str = "foo-1.0.dist-info") -> bytes:
    """Build a minimal RECORD file as bytes.

    *files* maps filenames to sizes.  The RECORD entry itself is
    appended with empty hash and size columns.
    """
    lines = [f"{fn},sha256={_FAKE_SHA256},{size}" for fn, size in files.items()]
    lines.append(f"{dist_info}/RECORD,,")
    return "\n".join(lines).encode()


def make_wheel(path, files: dict[str, str]) -> None:
    """Create a minimal .whl (zip) file with the given files."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def make_wheel_file(root_is_purelib: bool, tags: list[str]) -> bytes:
    """Build a minimal WHEEL file as bytes."""
    purelib = "true" if root_is_purelib else "false"
    lines = [
        "Wheel-Version: 1.0",
        "Generator: test",
        f"Root-Is-Purelib: {purelib}",
    ]
    for tag in tags:
        lines.append(f"Tag: {tag}")
    return "\n".join(lines).encode()


# --- Reusable fixtures ---

UPSTREAM_URL = "https://pypi.org/foo-1.0-py3-none-any.whl"
DOWNSTREAM_URL = "https://rebuild.example.com/foo-1.0-py3-none-any.whl"


@pytest.fixture()
def identical_result() -> WheelComparison:
    """A WheelComparison where all files are identical."""
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
        different=(),
        identical=("foo/__init__.py", "foo/module.py"),
    )


@pytest.fixture()
def error_result() -> WheelComparison:
    """A WheelComparison with errors and notices."""
    return WheelComparison(
        upstream="up",
        downstream="down",
        upstream_wheel="foo-1.0-py3-none-any.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
        dist="foo",
        upstream_version=Version("1.0"),
        downstream_version=Version("1.0"),
        only_upstream=(FileEntry("missing.py", Severity.ERROR, Classification.OTHER),),
        only_downstream=(
            FileEntry(
                "foo-1.0.dist-info/extra.txt",
                Severity.NOTICE,
                Classification.DIST_INFO,
            ),
        ),
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
        identical=("foo/__init__.py",),
    )


@pytest.fixture()
def notice_only_result() -> WheelComparison:
    """A WheelComparison with only notices (no errors)."""
    return WheelComparison(
        upstream="up",
        downstream="down",
        upstream_wheel="foo-1.0-py3-none-any.whl",
        downstream_wheel="foo-1.0-py3-none-any.whl",
        dist="foo",
        upstream_version=Version("1.0"),
        downstream_version=Version("1.0"),
        only_upstream=(FileEntry("foo.libs/bar.so", Severity.NOTICE, Classification.AUDITWHEEL),),
        only_downstream=(),
        different=(
            FileDiff(
                "foo.so",
                1000,
                2000,
                111,
                222,
                Severity.EXPECTED,
                Classification.EXTENSION_MODULE,
            ),
        ),
        identical=("foo/__init__.py",),
    )


@pytest.fixture()
def invoke_cli():
    """Invoke the retread CLI via CliRunner."""
    runner = CliRunner()

    def _invoke(args):
        return runner.invoke(cli, args)

    return _invoke

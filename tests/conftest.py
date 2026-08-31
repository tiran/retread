"""Shared test fixtures for retread."""

import zipfile

import pytest
from click.testing import CliRunner
from zipwire import SyncRemoteZip
from zipwire.backends import FileReader

from retread import (
    Analysis,
    Classification,
    Comparison,
    Context,
    FileDiff,
    FileEntry,
    Severity,
    WheelInfo,
    WheelSource,
    compare,
)
from retread.__main__ import cli
from retread._resolve import _wheel_basename

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


def make_wheel_info(
    filename: str,
    infos: list[FakeInfo],
    *,
    source: str | None = None,
    file_size: int = 0,
    origin: WheelSource | None = None,
    metadata: bytes | None = None,
    wheel: bytes | None = None,
    record: bytes | None = None,
) -> WheelInfo:
    """Build a :class:`~retread.WheelInfo` from fake central-directory infos.

    *infos* is a list of :class:`FakeInfo` (directories excluded
    automatically).  The dist-info blobs (METADATA/WHEEL/RECORD) are supplied
    directly rather than looked up in *infos*.
    """
    basename = _wheel_basename(filename)
    info_map = {i.filename: i for i in infos if not i.is_dir()}
    blobs: dict[str, bytes] = {}
    if metadata is not None:
        blobs["METADATA"] = metadata
    if wheel is not None:
        blobs["WHEEL"] = wheel
    if record is not None:
        blobs["RECORD"] = record
    if origin is None:
        origin = WheelSource(source=source if source is not None else filename)
    return WheelInfo._assemble(
        origin=origin,
        file_size=file_size,
        filename=basename,
        infos=info_map,
        blobs=blobs,
    )


def make_comparison(
    *,
    upstream: str = "up",
    downstream: str = "down",
    upstream_wheel: str = "foo-1.0-py3-none-any.whl",
    downstream_wheel: str = "foo-1.0-py3-none-any.whl",
    only_upstream=(),
    only_downstream=(),
    different=(),
    identical=(),
    record_mismatches=(),
    platform_warnings=(),
    venv_bundles=(),
    metadata_field_diffs=(),
    context: Context | None = None,
    **_ignored,
) -> Comparison:
    """Build a :class:`Comparison` from flat finding lists.

    Accepts the old flat ``WheelComparison`` keyword arguments so rendering
    and property tests can construct results without a real comparison run.
    Legacy-only keywords (``dist``, ``upstream_version`` ...) are ignored.
    """
    analysis = Analysis(
        only_upstream=list(only_upstream),
        only_downstream=list(only_downstream),
        different=list(different),
        identical=list(identical),
        record_mismatches=list(record_mismatches),
        platform_warnings=list(platform_warnings),
        venv_bundles=list(venv_bundles),
        metadata_field_diffs=list(metadata_field_diffs),
    )
    return Comparison(
        context=context if context is not None else Context.default(),
        upstream=make_wheel_info(upstream_wheel, [], source=upstream),
        downstream=make_wheel_info(downstream_wheel, [], source=downstream),
        analysis=analysis,
    )


def run_compare(
    *,
    upstream: str = "https://pypi.org/foo-1.0-py3-none-any.whl",
    downstream: str = "https://rebuild.test/foo-1.0-py3-none-any.whl",
    upstream_infos: dict | None = None,
    downstream_infos: dict | None = None,
    upstream_metadata: bytes | None = None,
    downstream_metadata: bytes | None = None,
    upstream_wheel: bytes | None = None,
    downstream_wheel: bytes | None = None,
    upstream_record: bytes | None = None,
    downstream_record: bytes | None = None,
    context: Context | None = None,
) -> Comparison:
    """Build two :class:`WheelInfo` from fake infos and run :func:`compare`.

    *upstream_infos* / *downstream_infos* map in-wheel path strings to
    :class:`FakeInfo` (the dict values are used; keys are ignored).
    """
    up = make_wheel_info(
        upstream,
        list((upstream_infos or {}).values()),
        source=upstream,
        metadata=upstream_metadata,
        wheel=upstream_wheel,
        record=upstream_record,
    )
    down = make_wheel_info(
        downstream,
        list((downstream_infos or {}).values()),
        source=downstream,
        metadata=downstream_metadata,
        wheel=downstream_wheel,
        record=downstream_record,
    )
    return compare(context if context is not None else Context.default(), up, down)


class FakePkg:
    """Minimal stand-in for pypi_simple.DistributionPackage."""

    def __init__(self, filename: str, url: str = "") -> None:
        self.filename = filename
        self.url = url or f"https://pkgs.example/{filename}"


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


def load_local_wheel(path) -> WheelInfo:
    """Load a :class:`WheelInfo` from a local wheel via a zipwire FileReader."""
    with SyncRemoteZip(FileReader(str(path))) as zf:
        return WheelInfo.from_sync_remote(zf)


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
DOWNSTREAM_URL = "https://rebuild.test/foo-1.0-py3-none-any.whl"


def _fixture_comparison(analysis: Analysis) -> Comparison:
    """Wrap an :class:`Analysis` in a :class:`Comparison` for two ``foo`` wheels."""
    upstream = make_wheel_info("foo-1.0-py3-none-any.whl", [], source="up")
    downstream = make_wheel_info("foo-1.0-py3-none-any.whl", [], source="down")
    return Comparison(
        context=Context.default(),
        upstream=upstream,
        downstream=downstream,
        analysis=analysis,
    )


@pytest.fixture()
def identical_result() -> Comparison:
    """A Comparison where all files are identical."""
    return _fixture_comparison(
        Analysis(identical=["foo/__init__.py", "foo/module.py"]),
    )


@pytest.fixture()
def error_result() -> Comparison:
    """A Comparison with errors and notices."""
    return _fixture_comparison(
        Analysis(
            only_upstream=[FileEntry("missing.py", Severity.ERROR, Classification.OTHER)],
            only_downstream=[
                FileEntry(
                    "foo-1.0.dist-info/extra.txt",
                    Severity.NOTICE,
                    Classification.DIST_INFO,
                )
            ],
            different=[
                FileDiff(
                    "foo-1.0.dist-info/RECORD",
                    500,
                    600,
                    111,
                    222,
                    Severity.EXPECTED,
                    Classification.RECORD,
                )
            ],
            identical=["foo/__init__.py"],
        )
    )


@pytest.fixture()
def notice_only_result() -> Comparison:
    """A Comparison with only notices (no errors)."""
    return _fixture_comparison(
        Analysis(
            only_upstream=[
                FileEntry("foo.libs/bar.so", Severity.NOTICE, Classification.AUDITWHEEL)
            ],
            different=[
                FileDiff(
                    "foo.so",
                    1000,
                    2000,
                    111,
                    222,
                    Severity.EXPECTED,
                    Classification.EXTENSION_MODULE,
                )
            ],
            identical=["foo/__init__.py"],
        )
    )


@pytest.fixture()
def invoke_cli():
    """Invoke the retread CLI via CliRunner."""
    runner = CliRunner()

    def _invoke(args):
        return runner.invoke(cli, args)

    return _invoke

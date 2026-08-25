"""Wheel comparison logic.

Compares two wheels (upstream vs. downstream rebuild) by examining
their ZIP central directory metadata.  CRC-32 checksums and file
sizes are compared without downloading file contents, making the
initial comparison very efficient.
"""

from __future__ import annotations

import asyncio
import dataclasses
import email.parser
import enum
import logging
import pathlib
import re
import typing
import zipfile
from typing import Any

from retread._errors import InvalidWheelError

if typing.TYPE_CHECKING:
    import collections.abc

    from zipwire import AsyncRemoteZip, SyncRemoteZip

logger = logging.getLogger(__name__)


class Severity(enum.Enum):
    NOTICE = "notice"
    ERROR = "error"


class Classification(enum.Enum):
    METADATA = "dist-info METADATA"
    RECORD = "dist-info RECORD"
    WHEEL = "dist-info WHEEL"
    SBOM = "sbom"
    LICENSE = "license"
    DIST_INFO = "dist-info"
    AUDITWHEEL = "auditwheel"
    DATA = "data"
    DATA_SCRIPTS = "data scripts"
    EXTENSION_MODULE = "extension module"
    OTHER = "other"


_WHEEL_NAME_RE = re.compile(r"^(?P<name>.+?)-(?P<version>.+?)(-\d[^-]*)?-[^-]+-[^-]+-[^-]+\.whl$")
_SHARED_LIB_RE = re.compile(r"\.so(\.[0-9.]+)?$")


def _is_shared_library(filename: str) -> bool:
    """Return True if *filename* looks like a shared library.

    Matches both unversioned (``foo.so``) and versioned
    (``libfoo.so.1.2.3``) shared-object names.
    """
    return _SHARED_LIB_RE.search(filename) is not None


def _is_auditwheel_lib(filename: str) -> bool:
    """Return True if *filename* is inside an auditwheel bundle directory.

    auditwheel vendors external shared libraries into a ``*.libs/``
    directory (e.g. ``Pillow.libs/libpng16-...so.16.58.0``).  These
    files are expected to appear or disappear between upstream and
    downstream builds.
    """
    parts = filename.split("/")
    return any(part.endswith(".libs") for part in parts)


def _parse_name_version(wheel_filename: str) -> tuple[str, str]:
    """Extract distribution name and version from a wheel filename.

    Both values are taken verbatim — no normalisation is applied — so
    they match the directory names inside the wheel
    (``{name}-{version}.dist-info/``, ``{name}-{version}.data/``).
    """
    m = _WHEEL_NAME_RE.match(wheel_filename)
    if m is None:
        raise InvalidWheelError(wheel_filename)
    return m.group("name"), m.group("version")


def _classify_file(
    filename: str, *, dist: str, version: str, missing: bool = False
) -> tuple[Severity, Classification]:
    """Classify a file difference as NOTICE or ERROR with a classification label.

    Expected differences (dist-info metadata, SBOMs, shared libraries)
    are notices; everything else is an error.

    When *missing* is True the file is only present on one side of the
    comparison.  A shared library that is absent is an error unless it
    lives in an auditwheel bundle directory (``*.libs/``).

    *dist* and *version* are the distribution name and version extracted
    verbatim from the wheel filename (not normalised).

    Returns a ``(Severity, Classification)`` tuple.
    """
    dist_info = f"{dist}-{version}.dist-info/"
    data = f"{dist}-{version}.data/"
    # {name}-{version}.dist-info/
    if filename.startswith(dist_info):
        rest = filename[len(dist_info) :]
        if rest == "RECORD":
            return Severity.NOTICE, Classification.RECORD
        if rest == "WHEEL":
            return Severity.NOTICE, Classification.WHEEL
        if rest == "METADATA":
            return Severity.NOTICE, Classification.METADATA
        if rest.startswith("sboms/"):
            return Severity.NOTICE, Classification.SBOM
        if rest.startswith("licenses/"):
            return Severity.NOTICE, Classification.LICENSE
        return Severity.NOTICE, Classification.DIST_INFO
    # {name}-{version}.data/{subdir}/
    if filename.startswith(data):
        rest = filename[len(data) :]
        cls = Classification.DATA_SCRIPTS if rest.startswith("scripts/") else Classification.DATA
        if missing:
            return Severity.ERROR, cls
        return Severity.NOTICE, cls
    # auditwheel-bundled shared libraries (*.libs/)
    if _is_auditwheel_lib(filename):
        return Severity.NOTICE, Classification.AUDITWHEEL
    # Shared library binaries (.so) not in *.libs/
    if _is_shared_library(filename):
        if missing:
            return Severity.ERROR, Classification.EXTENSION_MODULE
        # Binary difference in an .so present on both sides is expected
        return Severity.NOTICE, Classification.EXTENSION_MODULE
    return Severity.ERROR, Classification.OTHER


@dataclasses.dataclass(frozen=True, slots=True)
class FileEntry:
    """A file present only in one side of the comparison."""

    filename: str
    severity: Severity
    classification: Classification


@dataclasses.dataclass(frozen=True, slots=True)
class FileDiff:
    """A file that differs between upstream and downstream wheels."""

    filename: str
    upstream_size: int
    downstream_size: int
    upstream_crc32: int
    downstream_crc32: int
    severity: Severity
    classification: Classification


@dataclasses.dataclass(frozen=True, slots=True)
class WheelComparison:
    """Result of comparing an upstream wheel to a downstream rebuild.

    Attributes:
        upstream: Identifier (URL or path) of the upstream wheel.
        downstream: Identifier (URL or path) of the downstream rebuild.
        upstream_wheel: Wheel filename of the upstream wheel.
        downstream_wheel: Wheel filename of the downstream rebuild.
        only_upstream: Files present only in the upstream wheel.
        only_downstream: Files present only in the downstream rebuild.
        different: Files present in both but with different content.
        identical: Files present in both with matching content.
    """

    upstream: str
    downstream: str
    upstream_wheel: str
    downstream_wheel: str
    only_upstream: tuple[FileEntry, ...]
    only_downstream: tuple[FileEntry, ...]
    different: tuple[FileDiff, ...]
    identical: tuple[str, ...]

    @property
    def is_identical(self) -> bool:
        """Return True if the wheels are identical."""
        return not self.only_upstream and not self.only_downstream and not self.different

    @property
    def has_errors(self) -> bool:
        """Return True if any difference is classified as an error."""
        return (
            any(entry.severity is Severity.ERROR for entry in self.only_upstream)
            or any(entry.severity is Severity.ERROR for entry in self.only_downstream)
            or any(diff.severity is Severity.ERROR for diff in self.different)
        )


def compare_wheels(
    upstream: SyncRemoteZip,
    downstream: SyncRemoteZip,
) -> WheelComparison:
    """Compare two wheels using their ZIP central directory metadata.

    Both ``upstream`` and ``downstream`` must already be open
    (central directory fetched).  Comparison uses CRC-32 checksums
    and uncompressed sizes from the central directory, so no file
    content is downloaded.
    """
    result = _compare(
        upstream=upstream.url,
        downstream=downstream.url,
        upstream_infos={info.filename: info for info in upstream.infolist() if not info.is_dir()},
        downstream_infos={
            info.filename: info for info in downstream.infolist() if not info.is_dir()
        },
    )
    return _check_metadata(result, upstream.read, downstream.read)


async def async_compare_wheels(
    upstream: AsyncRemoteZip,
    downstream: AsyncRemoteZip,
) -> WheelComparison:
    """Async version of :func:`compare_wheels`."""
    result = _compare(
        upstream=upstream.url,
        downstream=downstream.url,
        upstream_infos={info.filename: info for info in upstream.infolist() if not info.is_dir()},
        downstream_infos={
            info.filename: info for info in downstream.infolist() if not info.is_dir()
        },
    )

    metadata_diffs = [
        diff for diff in result.different if diff.filename.endswith(".dist-info/METADATA")
    ]
    if not metadata_diffs:
        return result

    # Pre-fetch all needed metadata files in parallel
    reads = []
    for diff in metadata_diffs:
        reads.append(upstream.read(diff.filename))
        reads.append(downstream.read(diff.filename))
    results = await asyncio.gather(*reads)
    upstream_data: dict[str, bytes] = {}
    downstream_data: dict[str, bytes] = {}
    for i, diff in enumerate(metadata_diffs):
        upstream_data[diff.filename] = results[i * 2]
        downstream_data[diff.filename] = results[i * 2 + 1]

    return _check_metadata(
        result,
        upstream_data.__getitem__,
        downstream_data.__getitem__,
    )


def _compare(
    upstream: str,
    downstream: str,
    upstream_infos: dict[str, Any],
    downstream_infos: dict[str, Any],
) -> WheelComparison:
    upstream_whl = _wheel_basename(upstream)
    downstream_whl = _wheel_basename(downstream)
    dist, dist_version = _parse_name_version(upstream_whl)
    upstream_names = set(upstream_infos)
    downstream_names = set(downstream_infos)

    only_upstream = sorted(upstream_names - downstream_names)
    only_downstream = sorted(downstream_names - upstream_names)

    identical: list[str] = []
    different: list[FileDiff] = []
    for fname in sorted(upstream_names & downstream_names):
        u_info = upstream_infos[fname]
        d_info = downstream_infos[fname]
        if u_info.CRC == d_info.CRC and u_info.file_size == d_info.file_size:
            identical.append(fname)
        else:
            severity, classification = _classify_file(fname, dist=dist, version=dist_version)
            different.append(
                FileDiff(
                    filename=fname,
                    upstream_size=u_info.file_size,
                    downstream_size=d_info.file_size,
                    upstream_crc32=u_info.CRC,
                    downstream_crc32=d_info.CRC,
                    severity=severity,
                    classification=classification,
                )
            )

    def _make_entry(fname: str) -> FileEntry:
        severity, classification = _classify_file(
            fname, dist=dist, version=dist_version, missing=True
        )
        return FileEntry(filename=fname, severity=severity, classification=classification)

    return WheelComparison(
        upstream=upstream,
        downstream=downstream,
        upstream_wheel=upstream_whl,
        downstream_wheel=downstream_whl,
        only_upstream=tuple(_make_entry(fname) for fname in only_upstream),
        only_downstream=tuple(_make_entry(fname) for fname in only_downstream),
        different=tuple(different),
        identical=tuple(identical),
    )


def _metadata_core_match(upstream_bytes: bytes, downstream_bytes: bytes) -> bool:
    """Check whether core metadata fields match between two METADATA files.

    Compares Name, Version (single-value) and Requires-Dist,
    Provides-Extra (multi-value, compared as sorted lists).
    """
    parser = email.parser.BytesParser()
    upstream_meta = parser.parsebytes(upstream_bytes)
    downstream_meta = parser.parsebytes(downstream_bytes)

    for key in ("Name", "Version"):
        if upstream_meta.get(key) != downstream_meta.get(key):
            return False

    for key in ("Requires-Dist", "Provides-Extra"):
        upstream_values = sorted(upstream_meta.get_all(key) or [])
        downstream_values = sorted(downstream_meta.get_all(key) or [])
        if upstream_values != downstream_values:
            return False

    return True


def _check_metadata(
    result: WheelComparison,
    read_upstream: collections.abc.Callable[[str], bytes],
    read_downstream: collections.abc.Callable[[str], bytes],
) -> WheelComparison:
    """Upgrade METADATA diff severity to ERROR if core fields differ."""
    metadata_diffs = [
        (i, diff)
        for i, diff in enumerate(result.different)
        if diff.filename.endswith(".dist-info/METADATA")
    ]
    if not metadata_diffs:
        return result

    new_different = list(result.different)
    for i, diff in metadata_diffs:
        upstream_bytes = read_upstream(diff.filename)
        downstream_bytes = read_downstream(diff.filename)
        if not _metadata_core_match(upstream_bytes, downstream_bytes):
            new_different[i] = dataclasses.replace(diff, severity=Severity.ERROR)

    return dataclasses.replace(result, different=tuple(new_different))


def _local_zip_infos(path: pathlib.Path) -> dict[str, zipfile.ZipInfo]:
    """Extract file info from a local wheel (zipfile)."""
    with zipfile.ZipFile(path) as zf:
        return {info.filename: info for info in zf.infolist() if not info.is_dir()}


def compare_local_wheel(
    upstream: SyncRemoteZip,
    downstream_path: pathlib.Path,
) -> WheelComparison:
    """Compare a remote upstream wheel against a local downstream wheel.

    The local wheel is opened with :mod:`zipfile`.  ``zipfile.ZipInfo``
    provides ``.CRC``, ``.file_size``, ``.filename``, and ``.is_dir()``
    — the same attributes used from ``zipwire.RemoteZipInfo``.
    """
    result = _compare(
        upstream=upstream.url,
        downstream=str(downstream_path),
        upstream_infos={info.filename: info for info in upstream.infolist() if not info.is_dir()},
        downstream_infos=_local_zip_infos(downstream_path),
    )
    with zipfile.ZipFile(downstream_path) as ds_zf:
        result = _check_metadata(result, upstream.read, ds_zf.read)
    return result


async def async_compare_local_wheel(
    upstream: AsyncRemoteZip,
    downstream_path: pathlib.Path,
) -> WheelComparison:
    """Async version of :func:`compare_local_wheel`."""
    result = _compare(
        upstream=upstream.url,
        downstream=str(downstream_path),
        upstream_infos={info.filename: info for info in upstream.infolist() if not info.is_dir()},
        downstream_infos=_local_zip_infos(downstream_path),
    )
    metadata_diffs = [
        diff for diff in result.different if diff.filename.endswith(".dist-info/METADATA")
    ]
    if not metadata_diffs:
        return result

    filenames = [diff.filename for diff in metadata_diffs]
    results = await asyncio.gather(*(upstream.read(f) for f in filenames))
    upstream_data = dict(zip(filenames, results, strict=True))

    with zipfile.ZipFile(downstream_path) as ds_zf:
        result = _check_metadata(result, upstream_data.__getitem__, ds_zf.read)
    return result


def _is_url(value: str) -> bool:
    """Check if a string looks like a URL."""
    return value.startswith(("http://", "https://"))


def _wheel_basename(source: str | pathlib.Path) -> str:
    """Extract wheel filename from a URL, path, or bare filename."""
    s = str(source)
    if _is_url(s):
        # Strip query/fragment, take basename
        path_part = s.split("?", 1)[0].split("#", 1)[0]
        return path_part.rsplit("/", 1)[-1]
    return pathlib.PurePosixPath(s).name

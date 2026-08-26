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

from packaging.version import Version

from retread._errors import InvalidWheelError
from retread._platform import PlatformWarning, check_platform_abi
from retread._record import RecordMismatch, check_records

if typing.TYPE_CHECKING:
    import collections.abc

    from zipwire import AsyncRemoteZip, SyncRemoteZip

logger = logging.getLogger(__name__)


class Severity(enum.Enum):
    EXPECTED = "expected"
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
    """Classify a file difference with a severity and classification label.

    Always-expected differences (RECORD, WHEEL, shared libraries, data
    scripts) are EXPECTED; other known dist-info differences are NOTICE;
    everything else is an ERROR.

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
            return Severity.EXPECTED, Classification.RECORD
        if rest == "WHEEL":
            return Severity.EXPECTED, Classification.WHEEL
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
        if rest.startswith("scripts/"):
            if missing:
                return Severity.ERROR, Classification.DATA_SCRIPTS
            return Severity.EXPECTED, Classification.DATA_SCRIPTS
        if missing:
            return Severity.ERROR, Classification.DATA
        return Severity.NOTICE, Classification.DATA
    # auditwheel-bundled shared libraries (*.libs/)
    if _is_auditwheel_lib(filename):
        return Severity.NOTICE, Classification.AUDITWHEEL
    # Shared library binaries (.so) not in *.libs/
    if _is_shared_library(filename):
        if missing:
            return Severity.ERROR, Classification.EXTENSION_MODULE
        # Binary difference in an .so present on both sides is expected
        return Severity.EXPECTED, Classification.EXTENSION_MODULE
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
        dist: Distribution name extracted from the wheel filenames.
        upstream_version: Version from the upstream wheel filename.
        downstream_version: Version from the downstream wheel filename.
            Usually identical to *upstream_version* but may differ when
            local-version segments differ (e.g. ``1.0+cpu`` vs ``1.0``).
        only_upstream: Files present only in the upstream wheel.
        only_downstream: Files present only in the downstream rebuild.
        different: Files present in both but with different content.
        identical: Files present in both with matching content.
    """

    upstream: str
    downstream: str
    upstream_wheel: str
    downstream_wheel: str
    dist: str
    upstream_version: Version
    downstream_version: Version
    only_upstream: tuple[FileEntry, ...]
    only_downstream: tuple[FileEntry, ...]
    different: tuple[FileDiff, ...]
    identical: tuple[str, ...]
    record_mismatches: tuple[RecordMismatch, ...] = ()
    platform_warnings: tuple[PlatformWarning, ...] = ()

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
            or bool(self.record_mismatches)
            or bool(self.platform_warnings)
        )


def _read_record(
    infos: dict[str, Any],
    read: collections.abc.Callable[[str], bytes],
    record_path: str,
) -> bytes | None:
    """Read a RECORD file if it exists, otherwise return ``None``."""
    if record_path not in infos:
        return None
    return read(record_path)


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
    upstream_infos = {info.filename: info for info in upstream.infolist() if not info.is_dir()}
    downstream_infos = {info.filename: info for info in downstream.infolist() if not info.is_dir()}
    result = _compare(
        upstream=upstream.url,
        downstream=downstream.url,
        upstream_infos=upstream_infos,
        downstream_infos=downstream_infos,
    )
    result = _check_metadata(result, upstream.read, downstream.read)
    up_record_path = f"{result.dist}-{result.upstream_version}.dist-info/RECORD"
    down_record_path = f"{result.dist}-{result.downstream_version}.dist-info/RECORD"
    result = check_records(
        result,
        upstream_infos=upstream_infos,
        downstream_infos=downstream_infos,
        upstream_record=_read_record(upstream_infos, upstream.read, up_record_path),
        downstream_record=_read_record(downstream_infos, downstream.read, down_record_path),
    )
    up_wheel_path = f"{result.dist}-{result.upstream_version}.dist-info/WHEEL"
    down_wheel_path = f"{result.dist}-{result.downstream_version}.dist-info/WHEEL"
    result = check_platform_abi(
        result,
        upstream_infos=upstream_infos,
        downstream_infos=downstream_infos,
        upstream_wheel=_read_record(upstream_infos, upstream.read, up_wheel_path),
        downstream_wheel=_read_record(downstream_infos, downstream.read, down_wheel_path),
    )
    return result


async def async_compare_wheels(
    upstream: AsyncRemoteZip,
    downstream: AsyncRemoteZip,
) -> WheelComparison:
    """Async version of :func:`compare_wheels`."""
    upstream_infos = {info.filename: info for info in upstream.infolist() if not info.is_dir()}
    downstream_infos = {info.filename: info for info in downstream.infolist() if not info.is_dir()}
    result = _compare(
        upstream=upstream.url,
        downstream=downstream.url,
        upstream_infos=upstream_infos,
        downstream_infos=downstream_infos,
    )

    upstream_metadata = f"{result.dist}-{result.upstream_version}.dist-info/METADATA"
    downstream_metadata = f"{result.dist}-{result.downstream_version}.dist-info/METADATA"
    upstream_record = f"{result.dist}-{result.upstream_version}.dist-info/RECORD"
    downstream_record = f"{result.dist}-{result.downstream_version}.dist-info/RECORD"
    upstream_wheel_path = f"{result.dist}-{result.upstream_version}.dist-info/WHEEL"
    downstream_wheel_path = f"{result.dist}-{result.downstream_version}.dist-info/WHEEL"

    # Find METADATA in `different` (same-version case)
    metadata_diffs = [diff for diff in result.different if diff.filename == upstream_metadata]

    # Detect cross-version METADATA (different-version case)
    has_cross_metadata = (
        upstream_metadata != downstream_metadata
        and any(e.filename == upstream_metadata for e in result.only_upstream)
        and any(e.filename == downstream_metadata for e in result.only_downstream)
    )

    # Pre-fetch all needed METADATA, RECORD, and WHEEL files in parallel
    reads: list[collections.abc.Coroutine[Any, Any, bytes]] = []
    metadata_keys: list[tuple[str, str]] = []  # (side, filename)
    for diff in metadata_diffs:
        reads.append(upstream.read(diff.filename))
        metadata_keys.append(("upstream", diff.filename))
        reads.append(downstream.read(diff.filename))
        metadata_keys.append(("downstream", diff.filename))

    if has_cross_metadata:
        reads.append(upstream.read(upstream_metadata))
        metadata_keys.append(("upstream", upstream_metadata))
        reads.append(downstream.read(downstream_metadata))
        metadata_keys.append(("downstream", downstream_metadata))

    record_keys: list[str] = []
    for record_path, infos, rz in [
        (upstream_record, upstream_infos, upstream),
        (downstream_record, downstream_infos, downstream),
    ]:
        if record_path in infos:
            reads.append(rz.read(record_path))
            record_keys.append(record_path)

    wheel_keys: list[str] = []
    for wheel_path, infos, rz in [
        (upstream_wheel_path, upstream_infos, upstream),
        (downstream_wheel_path, downstream_infos, downstream),
    ]:
        if wheel_path in infos:
            reads.append(rz.read(wheel_path))
            wheel_keys.append(wheel_path)

    fetched = await asyncio.gather(*reads) if reads else []

    # Unpack metadata reads
    upstream_meta: dict[str, bytes] = {}
    downstream_meta: dict[str, bytes] = {}
    for idx, (side, fname) in enumerate(metadata_keys):
        if side == "upstream":
            upstream_meta[fname] = fetched[idx]
        else:
            downstream_meta[fname] = fetched[idx]

    if metadata_diffs or has_cross_metadata:
        result = _check_metadata(
            result,
            upstream_meta.__getitem__,
            downstream_meta.__getitem__,
        )

    # Unpack record reads
    record_data: dict[str, bytes] = {}
    offset = len(metadata_keys)
    for idx, fname in enumerate(record_keys):
        record_data[fname] = fetched[offset + idx]

    result = check_records(
        result,
        upstream_infos=upstream_infos,
        downstream_infos=downstream_infos,
        upstream_record=record_data.get(upstream_record),
        downstream_record=record_data.get(downstream_record),
    )

    # Unpack wheel reads
    wheel_data: dict[str, bytes] = {}
    offset = len(metadata_keys) + len(record_keys)
    for idx, fname in enumerate(wheel_keys):
        wheel_data[fname] = fetched[offset + idx]

    result = check_platform_abi(
        result,
        upstream_infos=upstream_infos,
        downstream_infos=downstream_infos,
        upstream_wheel=wheel_data.get(upstream_wheel_path),
        downstream_wheel=wheel_data.get(downstream_wheel_path),
    )
    return result


def _compare(
    upstream: str,
    downstream: str,
    upstream_infos: dict[str, Any],
    downstream_infos: dict[str, Any],
) -> WheelComparison:
    upstream_whl = _wheel_basename(upstream)
    downstream_whl = _wheel_basename(downstream)
    dist, upstream_ver_str = _parse_name_version(upstream_whl)
    downstream_dist, downstream_ver_str = _parse_name_version(downstream_whl)
    if dist != downstream_dist:
        raise InvalidWheelError(
            f"dist name mismatch: upstream {dist!r} != downstream {downstream_dist!r}"
        )
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
            severity, classification = _classify_file(fname, dist=dist, version=upstream_ver_str)
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

    def _make_upstream_entry(fname: str) -> FileEntry:
        severity, classification = _classify_file(
            fname, dist=dist, version=upstream_ver_str, missing=True
        )
        return FileEntry(filename=fname, severity=severity, classification=classification)

    def _make_downstream_entry(fname: str) -> FileEntry:
        severity, classification = _classify_file(
            fname, dist=dist, version=downstream_ver_str, missing=True
        )
        return FileEntry(filename=fname, severity=severity, classification=classification)

    return WheelComparison(
        upstream=upstream,
        downstream=downstream,
        upstream_wheel=upstream_whl,
        downstream_wheel=downstream_whl,
        dist=dist,
        upstream_version=Version(upstream_ver_str),
        downstream_version=Version(downstream_ver_str),
        only_upstream=tuple(_make_upstream_entry(fname) for fname in only_upstream),
        only_downstream=tuple(_make_downstream_entry(fname) for fname in only_downstream),
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
    """Upgrade METADATA diff severity to ERROR if core fields differ.

    Handles two cases:

    * **Same dist-info prefix** (common): both METADATA files share a
      filename and appear in *result.different*.
    * **Different dist-info prefix** (e.g. local-version differs): the
      two METADATA files have different paths and appear in
      *only_upstream* / *only_downstream*.  Core fields are still
      compared and severities updated accordingly.
    """
    upstream_metadata = f"{result.dist}-{result.upstream_version}.dist-info/METADATA"
    downstream_metadata = f"{result.dist}-{result.downstream_version}.dist-info/METADATA"

    if upstream_metadata == downstream_metadata:
        # Same dist-info prefix: METADATA appears in `different`
        metadata_diffs = [
            (i, diff)
            for i, diff in enumerate(result.different)
            if diff.filename == upstream_metadata
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

    # Different dist-info prefixes: METADATA split across only_upstream / only_downstream
    up_idx = next(
        (i for i, e in enumerate(result.only_upstream) if e.filename == upstream_metadata),
        None,
    )
    down_idx = next(
        (i for i, e in enumerate(result.only_downstream) if e.filename == downstream_metadata),
        None,
    )
    if up_idx is None or down_idx is None:
        return result

    upstream_bytes = read_upstream(upstream_metadata)
    downstream_bytes = read_downstream(downstream_metadata)
    severity = (
        Severity.NOTICE
        if _metadata_core_match(upstream_bytes, downstream_bytes)
        else Severity.ERROR
    )

    new_only_upstream = list(result.only_upstream)
    new_only_downstream = list(result.only_downstream)
    new_only_upstream[up_idx] = dataclasses.replace(
        result.only_upstream[up_idx],
        severity=severity,
        classification=Classification.METADATA,
    )
    new_only_downstream[down_idx] = dataclasses.replace(
        result.only_downstream[down_idx],
        severity=severity,
        classification=Classification.METADATA,
    )
    return dataclasses.replace(
        result,
        only_upstream=tuple(new_only_upstream),
        only_downstream=tuple(new_only_downstream),
    )


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
    upstream_infos = {info.filename: info for info in upstream.infolist() if not info.is_dir()}
    downstream_infos = _local_zip_infos(downstream_path)
    result = _compare(
        upstream=upstream.url,
        downstream=str(downstream_path),
        upstream_infos=upstream_infos,
        downstream_infos=downstream_infos,
    )
    up_record_path = f"{result.dist}-{result.upstream_version}.dist-info/RECORD"
    down_record_path = f"{result.dist}-{result.downstream_version}.dist-info/RECORD"
    up_wheel_path = f"{result.dist}-{result.upstream_version}.dist-info/WHEEL"
    down_wheel_path = f"{result.dist}-{result.downstream_version}.dist-info/WHEEL"
    with zipfile.ZipFile(downstream_path) as ds_zf:
        result = _check_metadata(result, upstream.read, ds_zf.read)
        result = check_records(
            result,
            upstream_infos=upstream_infos,
            downstream_infos=downstream_infos,
            upstream_record=_read_record(upstream_infos, upstream.read, up_record_path),
            downstream_record=_read_record(downstream_infos, ds_zf.read, down_record_path),
        )
        result = check_platform_abi(
            result,
            upstream_infos=upstream_infos,
            downstream_infos=downstream_infos,
            upstream_wheel=_read_record(upstream_infos, upstream.read, up_wheel_path),
            downstream_wheel=_read_record(downstream_infos, ds_zf.read, down_wheel_path),
        )
    return result


async def async_compare_local_wheel(
    upstream: AsyncRemoteZip,
    downstream_path: pathlib.Path,
) -> WheelComparison:
    """Async version of :func:`compare_local_wheel`."""
    upstream_infos = {info.filename: info for info in upstream.infolist() if not info.is_dir()}
    downstream_infos = _local_zip_infos(downstream_path)
    result = _compare(
        upstream=upstream.url,
        downstream=str(downstream_path),
        upstream_infos=upstream_infos,
        downstream_infos=downstream_infos,
    )
    upstream_metadata = f"{result.dist}-{result.upstream_version}.dist-info/METADATA"
    downstream_metadata = f"{result.dist}-{result.downstream_version}.dist-info/METADATA"
    upstream_record = f"{result.dist}-{result.upstream_version}.dist-info/RECORD"
    upstream_wheel_path = f"{result.dist}-{result.upstream_version}.dist-info/WHEEL"

    # Find METADATA in `different` (same-version case)
    metadata_diffs = [diff for diff in result.different if diff.filename == upstream_metadata]

    # Detect cross-version METADATA (different-version case)
    has_cross_metadata = (
        upstream_metadata != downstream_metadata
        and any(e.filename == upstream_metadata for e in result.only_upstream)
        and any(e.filename == downstream_metadata for e in result.only_downstream)
    )

    # Pre-fetch upstream METADATA, RECORD, and WHEEL files in parallel
    reads: list[collections.abc.Coroutine[Any, Any, bytes]] = []
    read_keys: list[str] = []
    for diff in metadata_diffs:
        reads.append(upstream.read(diff.filename))
        read_keys.append(diff.filename)

    if has_cross_metadata:
        reads.append(upstream.read(upstream_metadata))
        read_keys.append(upstream_metadata)

    if upstream_record in upstream_infos:
        reads.append(upstream.read(upstream_record))
        read_keys.append(upstream_record)

    if upstream_wheel_path in upstream_infos:
        reads.append(upstream.read(upstream_wheel_path))
        read_keys.append(upstream_wheel_path)

    fetched = await asyncio.gather(*reads) if reads else []
    upstream_data = dict(zip(read_keys, fetched, strict=True))

    down_record_path = f"{result.dist}-{result.downstream_version}.dist-info/RECORD"
    down_wheel_path = f"{result.dist}-{result.downstream_version}.dist-info/WHEEL"
    with zipfile.ZipFile(downstream_path) as ds_zf:
        result = _check_metadata(result, upstream_data.__getitem__, ds_zf.read)
        result = check_records(
            result,
            upstream_infos=upstream_infos,
            downstream_infos=downstream_infos,
            upstream_record=upstream_data.get(upstream_record),
            downstream_record=_read_record(downstream_infos, ds_zf.read, down_record_path),
        )
        result = check_platform_abi(
            result,
            upstream_infos=upstream_infos,
            downstream_infos=downstream_infos,
            upstream_wheel=upstream_data.get(upstream_wheel_path),
            downstream_wheel=_read_record(downstream_infos, ds_zf.read, down_wheel_path),
        )
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

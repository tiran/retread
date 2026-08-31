"""Per-wheel loaded state: :class:`FileStat` and :class:`WheelInfo`.

A :class:`WheelInfo` is *what we loaded* for one wheel: its identity, its ZIP
central directory (as :class:`FileStat` entries keyed by
:class:`~pathlib.PurePosixPath`), and a preloaded, eagerly parsed set of
dist-info files (``METADATA``, ``WHEEL``, ``RECORD``).

The loaders (:meth:`WheelInfo.from_sync_remote`,
:meth:`WheelInfo.from_async_remote`) are the only sync/async-specific code in
the package; everything downstream operates on the resulting frozen object.
Local wheels are loaded through the same loaders by wrapping them in a
``zipwire`` ``FileReader`` / ``AsyncFileReader``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import email.parser
import typing
from pathlib import PurePosixPath
from typing import Any

import packaging.utils
from packaging.metadata import RawMetadata, parse_email
from packaging.utils import InvalidWheelFilename
from packaging.version import InvalidVersion, Version

from retread._errors import InvalidMetadataError
from retread._resolve import _parse_name_version, _wheel_basename
from retread._types import Filename, Url

if typing.TYPE_CHECKING:
    import collections.abc
    from datetime import datetime

    from packaging.tags import Tag
    from pypi_simple import DistributionPackage, ProjectPage
    from zipwire import AsyncRemoteZip, SyncRemoteZip

# The dist-info files preloaded by every loader.  This is the complete set of
# files any current checker reads.
DIST_INFO_FILES: tuple[str, ...] = ("METADATA", "WHEEL", "RECORD")


def _wheel_tag_string(filename: str) -> str | None:
    """Return the compressed ``{python}-{abi}-{platform}`` tag of a wheel.

    This is the tag exactly as written in the filename (e.g. ``py3-none-any``
    or ``cp312-cp312-manylinux_2_17_x86_64``), not the expanded tag set.
    Returns ``None`` when *filename* is not a wheel name.
    """
    if not filename.endswith(".whl"):
        return None
    parts = filename.removesuffix(".whl").split("-")
    if len(parts) < 5:  # name-version-python-abi-platform (build tag optional)
        return None
    return "-".join(parts[-3:])


@dataclasses.dataclass(frozen=True, slots=True)
class WheelSource:
    """Where a wheel came from and what its index knows about the release.

    :attr:`source` is always set (the URL or local path the wheel was loaded
    from).  The remaining fields carry package-index metadata and stay at their
    defaults for wheels loaded from a local file or a direct URL, where no index
    lookup happens.

    Attributes:
        source: URL or local path the wheel was loaded from.
        upload_time: When the wheel was uploaded to the index, if known.
        provenance_url: PEP 740 provenance URL for the wheel, if published.
        has_sdist: The release ships a source distribution on the index.
        has_wheels: The release ships one or more wheels on the index.
        wheel_tags: Distinct compressed wheel tags across the release
            (e.g. ``("cp312-cp312-manylinux_2_17_x86_64", "py3-none-any")``).
    """

    source: Url | Filename
    upload_time: datetime | None = None
    provenance_url: Url | None = None
    has_sdist: bool = False
    has_wheels: bool = False
    wheel_tags: tuple[str, ...] = ()

    @classmethod
    def from_package(cls, pkg: DistributionPackage, page: ProjectPage) -> WheelSource:
        """Build a :class:`WheelSource` from a resolved index package and its page.

        *pkg* is the matched wheel; *page* provides the sibling distributions of
        the same release used to derive :attr:`has_sdist`, :attr:`has_wheels`,
        and :attr:`wheel_tags`.
        """
        has_sdist = False
        has_wheels = False
        tags: set[str] = set()
        for other in page.packages:
            if other.version != pkg.version:
                continue
            if other.package_type == "sdist":
                has_sdist = True
            elif other.filename.endswith(".whl"):
                has_wheels = True
                tag = _wheel_tag_string(other.filename)
                if tag is not None:
                    tags.add(tag)
        return cls(
            source=Url(pkg.url),
            upload_time=pkg.upload_time,
            provenance_url=Url(pkg.provenance_url) if pkg.provenance_url else None,
            has_sdist=has_sdist,
            has_wheels=has_wheels,
            wheel_tags=tuple(sorted(tags)),
        )

    @classmethod
    def local(cls, source: Url | Filename) -> WheelSource:
        """A source-only origin for a local file or direct URL (no index)."""
        return cls(source=source)


@dataclasses.dataclass(frozen=True, slots=True)
class FileStat:
    """Normalized central-directory entry for one file in a wheel.

    Normalizes both :class:`zipfile.ZipInfo` and ``zipwire.RemoteZipInfo``
    (a ``ZipInfo`` subclass), which share ``.filename``, ``.CRC``,
    ``.file_size``, and ``.is_dir()``.
    """

    path: PurePosixPath
    size: int
    crc: int

    @classmethod
    def from_zipinfo(cls, info: Any) -> FileStat:
        """Build a :class:`FileStat` from a ``ZipInfo``-like object."""
        return cls(path=PurePosixPath(info.filename), size=info.file_size, crc=info.CRC)


def _parse_wheel_tags(wheel_bytes: bytes) -> tuple[bool, list[str]]:
    """Parse a WHEEL file and extract Root-Is-Purelib and Tag values.

    Returns ``(root_is_purelib, tags)`` where *tags* is a list of
    tag strings like ``"cp312-cp312-linux_x86_64"``.
    """
    parser = email.parser.BytesParser()
    msg = parser.parsebytes(wheel_bytes)
    root_is_purelib = msg.get("Root-Is-Purelib", "false").strip().lower() == "true"
    tags = msg.get_all("Tag") or []
    return root_is_purelib, [t.strip() for t in tags]


def _validate_metadata(fields: RawMetadata, filename: str) -> None:
    """Validate that a parsed METADATA has usable core fields.

    A wheel whose METADATA is present but missing its ``Name`` or ``Version``,
    or carries an unparseable version, is malformed.  This is a fatal error for
    the wheel rather than something to compare, so it raises
    :class:`~retread._errors.InvalidMetadataError`.
    """
    name = fields.get("name")
    version = fields.get("version")
    if not name:
        raise InvalidMetadataError(f"{filename}: METADATA is missing the Name field")
    if not version:
        raise InvalidMetadataError(f"{filename}: METADATA is missing the Version field")
    try:
        Version(version)
    except InvalidVersion as exc:
        raise InvalidMetadataError(
            f"{filename}: METADATA has an invalid Version {version!r}: {exc}"
        ) from None


def _find_dist_info_name(names: collections.abc.Iterable[str], dist: str, version: str) -> str:
    """Find the distribution name used in the dist-info directory.

    The dist-info directory name inside a wheel may differ in casing
    or punctuation from the wheel filename.  This function scans
    *names* for a root-level ``{name}-{version}.dist-info/`` entry
    whose canonical name matches *dist* and returns the *name* portion
    actually used.

    Examples of real-world mismatches between wheel filename and
    dist-info directory:

    * ``InquirerPy-0.3.4.whl`` -> ``inquirerpy-0.3.4.dist-info/``
    * ``scons-4.5.2.whl`` -> ``SCons-4.5.2.dist-info/``
    * ``jpype1-1.5.0.whl`` -> ``JPype1-1.5.0.dist-info/``
    * ``jaraco_classes-3.4.0.whl`` -> ``jaraco.classes-3.4.0.dist-info/``

    Falls back to *dist* unchanged when no matching directory is found.
    """
    canonical = packaging.utils.canonicalize_name(dist)
    suffix = f"-{version}.dist-info"
    for filename in names:
        if filename.count("/") != 1:
            continue
        dir_part, _rest = filename.split("/", 1)
        if not dir_part.endswith(suffix):
            continue
        name_part = dir_part.removesuffix(suffix)
        if name_part and packaging.utils.canonicalize_name(name_part) == canonical:
            return name_part
    return dist


@dataclasses.dataclass(frozen=True, slots=True)
class WheelInfo:
    """Everything retread loaded for a single wheel.

    Attributes:
        origin: Where the wheel came from and index metadata about the release
            (:class:`WheelSource`).
        filename: The wheel filename (basename).
        file_size: The wheel's size in bytes (from the opened ZIP).
        dist: Distribution name as used in the wheel's dist-info directory
            (may differ in casing/punctuation from the filename).
        canonical: PEP 503 canonical distribution name from the filename.
        version: Version parsed from the filename.
        raw_version: Verbatim version string from the filename (used to
            build in-wheel prefixes, which are not normalized).
        tags: Compatibility tags parsed from the filename.
        dist_info: Root ``{dist}-{raw_version}.dist-info`` directory path.
        files: Central-directory entries, directories excluded, keyed by path.
        di_metadata / di_wheel / di_record: Raw bytes of the preloaded dist-info
            files (``METADATA`` / ``WHEEL`` / ``RECORD``; ``None`` when absent).
        metadata_fields: Parsed METADATA (``packaging.metadata.parse_email``),
            or ``None`` when METADATA is absent.
        wheel_tags: ``Tag`` strings from the WHEEL file.
        root_is_purelib: ``Root-Is-Purelib`` from the WHEEL file.
    """

    origin: WheelSource
    filename: str
    file_size: int
    dist: str
    canonical: packaging.utils.NormalizedName
    version: Version
    raw_version: str
    tags: frozenset[Tag]
    dist_info: PurePosixPath
    files: dict[PurePosixPath, FileStat]
    di_metadata: bytes | None
    di_wheel: bytes | None
    di_record: bytes | None
    metadata_fields: RawMetadata | None
    wheel_tags: list[str]
    root_is_purelib: bool

    @property
    def names(self) -> set[str]:
        """The set of in-wheel file paths as POSIX strings."""
        return {str(p) for p in self.files}

    @classmethod
    def _assemble(
        cls,
        *,
        origin: WheelSource,
        file_size: int,
        filename: str,
        infos: dict[str, Any],
        blobs: dict[str, bytes],
    ) -> WheelInfo:
        """Build a :class:`WheelInfo` from raw central-directory infos.

        *infos* maps in-wheel path strings to ``ZipInfo``-like objects
        (directories already excluded).  *blobs* maps the preloaded
        dist-info basenames (``METADATA``/``WHEEL``/``RECORD``) to bytes.
        """
        raw_dist, raw_version = _parse_name_version(filename)
        canonical = packaging.utils.canonicalize_name(raw_dist)
        version = Version(raw_version)
        try:
            _n, _v, _b, tags = packaging.utils.parse_wheel_filename(filename)
        except InvalidWheelFilename:
            tags = frozenset()
        dist = _find_dist_info_name(infos, raw_dist, raw_version)
        dist_info = PurePosixPath(f"{dist}-{raw_version}.dist-info")

        files = {PurePosixPath(name): FileStat.from_zipinfo(info) for name, info in infos.items()}

        metadata = blobs.get("METADATA")
        wheel = blobs.get("WHEEL")
        record = blobs.get("RECORD")

        metadata_fields: RawMetadata | None = None
        if metadata is not None:
            metadata_fields, _ = parse_email(metadata)
            _validate_metadata(metadata_fields, filename)

        if wheel is not None:
            root_is_purelib, wheel_tags = _parse_wheel_tags(wheel)
        else:
            root_is_purelib, wheel_tags = False, []

        return cls(
            origin=origin,
            filename=filename,
            file_size=file_size,
            dist=dist,
            canonical=canonical,
            version=version,
            raw_version=raw_version,
            tags=tags,
            dist_info=dist_info,
            files=files,
            di_metadata=metadata,
            di_wheel=wheel,
            di_record=record,
            metadata_fields=metadata_fields,
            wheel_tags=wheel_tags,
            root_is_purelib=root_is_purelib,
        )

    @staticmethod
    def _dist_info_paths(infos: dict[str, Any], filename: str) -> dict:
        """Resolve which dist-info files exist and their in-wheel paths.

        Returns a mapping ``{basename: full_path}`` for each
        :data:`DIST_INFO_FILES` entry that is present in *infos*.
        """
        raw_dist, raw_version = _parse_name_version(filename)
        dist = _find_dist_info_name(infos, raw_dist, raw_version)
        prefix = f"{dist}-{raw_version}.dist-info"
        resolved: dict[str, str] = {}
        for name in DIST_INFO_FILES:
            path = f"{prefix}/{name}"
            if path in infos:
                resolved[name] = path
        return resolved

    @classmethod
    def from_sync_remote(
        cls, remote: SyncRemoteZip, origin: WheelSource | None = None
    ) -> WheelInfo:
        """Load a :class:`WheelInfo` from an open sync ``SyncRemoteZip``.

        *origin* carries index metadata; when omitted a source-only
        :class:`WheelSource` is derived from the remote URL.
        """
        infos = {info.filename: info for info in remote.infolist() if not info.is_dir()}
        filename = _wheel_basename(remote.url)
        if origin is None:
            origin = WheelSource(source=Url(remote.url))
        paths = cls._dist_info_paths(infos, filename)
        blobs = {name: remote.read(path) for name, path in paths.items()}
        return cls._assemble(
            origin=origin, file_size=remote.file_size, filename=filename, infos=infos, blobs=blobs
        )

    @classmethod
    async def from_async_remote(
        cls, remote: AsyncRemoteZip, origin: WheelSource | None = None
    ) -> WheelInfo:
        """Load a :class:`WheelInfo` from an open async ``AsyncRemoteZip``.

        *origin* carries index metadata; when omitted a source-only
        :class:`WheelSource` is derived from the remote URL.
        """
        infos = {info.filename: info for info in remote.infolist() if not info.is_dir()}
        filename = _wheel_basename(remote.url)
        if origin is None:
            origin = WheelSource(source=Url(remote.url))
        paths = cls._dist_info_paths(infos, filename)
        names = list(paths)
        datas = await asyncio.gather(*(remote.read(paths[name]) for name in names))
        blobs = dict(zip(names, datas, strict=True))
        return cls._assemble(
            origin=origin, file_size=remote.file_size, filename=filename, infos=infos, blobs=blobs
        )

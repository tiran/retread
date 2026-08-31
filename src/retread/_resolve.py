"""Wheel filename parsing and upstream resolution."""

from __future__ import annotations

import dataclasses
import enum
import pathlib
import re
import typing

import packaging.utils
from packaging.utils import InvalidSdistFilename, InvalidWheelFilename

from retread._errors import (
    InvalidWheelError,
    NoWheelsError,
    VersionNotFoundError,
    WheelNotFoundError,
)

if typing.TYPE_CHECKING:
    from packaging.tags import Tag
    from packaging.version import Version
    from pypi_simple import DistributionPackage, ProjectPage

_SUPPORTED_ARCHS: frozenset[str] = frozenset(
    {"aarch64", "armv7l", "x86_64", "s390x", "ppc64le", "riscv64"}
)

_CPYTHON_RE = re.compile(r"^cp(\d+)$")


def _extract_arch(platform: str) -> str | None:
    """Extract the CPU architecture suffix from a glibc Linux platform tag.

    Only ``linux_*`` and ``manylinux_*`` platforms are considered.
    Returns ``None`` for ``musllinux``, ``any``, or other unrecognized platforms.
    """
    if not (platform.startswith("linux_") or platform.startswith("manylinux_")):
        return None
    for arch in _SUPPORTED_ARCHS:
        if platform.endswith(f"_{arch}"):
            return arch
    return None


def _tags_compatible(downstream_tag: Tag, upstream_tag: Tag) -> bool:
    """Check whether two individual tags are compatible.

    Compatibility rules:
    - Platform: exact match, or both share the same extracted CPU architecture.
    - Interpreter + ABI: exact match on both, **or** the abi3 stable-ABI rule
      applies (``cpXX-abi3`` is compatible with any ``cpYY-cpYY`` where YY >= XX,
      and vice-versa).
    """
    # --- platform ---
    if downstream_tag.platform != upstream_tag.platform:
        ds_arch = _extract_arch(downstream_tag.platform)
        us_arch = _extract_arch(upstream_tag.platform)
        if ds_arch is None or us_arch is None or ds_arch != us_arch:
            return False

    # --- interpreter + abi (including abi3) ---
    if (
        downstream_tag.interpreter == upstream_tag.interpreter
        and downstream_tag.abi == upstream_tag.abi
    ):
        return True

    # abi3 handling: one side has abi3, the other has cpXX-cpXX
    ds_interp = downstream_tag.interpreter
    ds_abi = downstream_tag.abi
    us_interp = upstream_tag.interpreter
    us_abi = upstream_tag.abi

    # Both sides abi3: compatible if both are CPython (minimum version may differ
    # between upstream and a downstream rebuild).
    if ds_abi == "abi3" and us_abi == "abi3":
        return bool(_CPYTHON_RE.match(ds_interp) and _CPYTHON_RE.match(us_interp))

    # Identify the abi3 side and the concrete side
    if ds_abi == "abi3":
        abi3_interp, concrete_interp, concrete_abi = ds_interp, us_interp, us_abi
    elif us_abi == "abi3":
        abi3_interp, concrete_interp, concrete_abi = us_interp, ds_interp, ds_abi
    else:
        # Neither is abi3 -- must match exactly (already checked above)
        return False

    abi3_m = _CPYTHON_RE.match(abi3_interp)
    concrete_m = _CPYTHON_RE.match(concrete_interp)
    if not abi3_m or not concrete_m:
        return False

    # The concrete side must also have cpXX as its ABI
    if concrete_abi != concrete_interp:
        return False

    # concrete cpython version must be >= abi3 minimum version
    return int(concrete_m.group(1)) >= int(abi3_m.group(1))


def _wheels_compatible(downstream_tags: frozenset[Tag], upstream_tags: frozenset[Tag]) -> bool:
    """Return ``True`` if any downstream tag is compatible with any upstream tag."""
    for ds_tag in downstream_tags:
        for us_tag in upstream_tags:
            if _tags_compatible(ds_tag, us_tag):
                return True
    return False


def _tag_match_score(downstream_tag: Tag, upstream_tag: Tag) -> int:
    """Score how closely two individual tags match.

    Higher is better:
    - **2**: exact interpreter+abi match (e.g. ``cp312-cp312`` ↔
      ``cp312-cp312``, or ``cp39-abi3`` ↔ ``cp39-abi3``).
    - **1**: both abi3 with different minimum versions, or abi3 ↔
      concrete where the concrete version >= abi3 minimum.
    - **0**: not compatible (caller should not normally reach this).

    Platform compatibility is not checked here; use
    :func:`_tags_compatible` first.
    """
    if (
        downstream_tag.interpreter == upstream_tag.interpreter
        and downstream_tag.abi == upstream_tag.abi
    ):
        return 2
    if _tags_compatible(downstream_tag, upstream_tag):
        return 1
    return 0


def _best_tag_score(downstream_tags: frozenset[Tag], upstream_tags: frozenset[Tag]) -> int:
    """Return the maximum tag-match score across all compatible tag pairs."""
    best = 0
    for ds_tag in downstream_tags:
        for us_tag in upstream_tags:
            score = _tag_match_score(ds_tag, us_tag)
            if score > best:
                best = score
                if best == 2:
                    return best
    return best


@dataclasses.dataclass(frozen=True, slots=True)
class WheelSpec:
    """Parsed wheel filename components.

    Attributes:
        filename: The original wheel filename.
        name: Canonicalized distribution name.
        version: Package version.
        build: Build tag, or ``None``.
        tags: Frozenset of compatibility tags.
    """

    filename: str
    name: packaging.utils.NormalizedName
    version: Version
    build: tuple[int, str] | None
    tags: frozenset[Tag]


def parse_wheel_spec(filename: str) -> WheelSpec:
    """Parse a wheel filename into a :class:`WheelSpec`.

    Args:
        filename: A wheel filename (e.g. ``"requests-2.32.3-py3-none-any.whl"``).

    Returns:
        A :class:`WheelSpec` with the parsed components.

    Raises:
        InvalidWheelError: If the filename is not a valid wheel name.
    """
    try:
        name, version, build, tags = packaging.utils.parse_wheel_filename(filename)
    except InvalidWheelFilename:
        raise InvalidWheelError(filename) from None
    return WheelSpec(
        filename=filename,
        name=name,
        version=version,
        build=build,
        tags=tags,
    )


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


def _parse_name_version(wheel_filename: str) -> tuple[str, str]:
    """Extract distribution name and version from a wheel filename.

    Both values are taken verbatim — no normalisation is applied — so
    they match the directory names inside the wheel
    (``{name}-{version}.dist-info/``, ``{name}-{version}.data/``).
    """
    if not wheel_filename.endswith(".whl"):
        raise InvalidWheelError(wheel_filename)
    parts = wheel_filename.removesuffix(".whl").split("-")
    # name-version-pytag-abitag-plattag (5 min), or with build tag (6)
    if len(parts) < 4:
        raise InvalidWheelError(wheel_filename)
    return parts[0], parts[1]


def _extract_raw_version(wheel_filename: str) -> str:
    """Extract the raw version string from a wheel filename."""
    return wheel_filename.removesuffix(".whl").split("-")[1]


def _version_match(upstream: Version, downstream: Version) -> bool:
    """Check whether an upstream version matches a downstream version.

    Exact match, or if the downstream version carries a PEP 440 local
    segment (e.g. ``1.5.0+rhaiv.5``), match the public portion so
    that the local rebuild can find its upstream base version.
    """
    if upstream == downstream:
        return True
    # Strip local segment: 1.5.0+rhaiv.5 matches upstream 1.5.0
    if downstream.local is not None:
        return upstream.public == downstream.public
    return False


def _wheel_tag_string(filename: str) -> str:
    """Return the compatibility-tag segment of a wheel filename.

    ``foo-1.0-cp312-cp312-manylinux_2_28_x86_64.whl`` ->
    ``cp312-cp312-manylinux_2_28_x86_64``.  Used verbatim in reports so the
    displayed tags match the published filenames exactly.
    """
    return "-".join(filename.removesuffix(".whl").split("-")[-3:])


def _platform_rank(platform: str) -> int:
    """Rank a platform tag for fallback wheel selection (higher = preferred)."""
    if "manylinux" in platform and platform.endswith("_x86_64"):
        return 5
    if "linux" in platform and platform.endswith("_x86_64"):
        return 4
    if "linux" in platform:
        return 3
    if "macos" in platform or "darwin" in platform:
        return 2
    if "win" in platform:
        return 1
    return 0


def _fallback_rank(tags: frozenset[Tag]) -> tuple[int, int]:
    """Score a wheel's tags for fallback selection: ``(platform, cpython)``."""
    best = (0, 0)
    for tag in tags:
        m = _CPYTHON_RE.match(tag.interpreter)
        rank = (_platform_rank(tag.platform), int(m.group(1)) if m else 0)
        if rank > best:
            best = rank
    return best


class ResolutionStatus(enum.Enum):
    """Outcome of resolving an upstream wheel for a downstream rebuild."""

    MATCHED = "matched"  # a tag-compatible wheel was found
    FALLBACK = "fallback"  # no tag match; compared against another wheel of the version


@dataclasses.dataclass(frozen=True, slots=True)
class Resolution:
    """A successfully resolved upstream wheel plus how it was chosen.

    ``status`` is :attr:`ResolutionStatus.MATCHED` when a tag-compatible wheel
    was found, or :attr:`ResolutionStatus.FALLBACK` when the requested version
    exists but no wheel matches the downstream tags, so a different wheel of the
    same version was chosen to allow a comparison anyway.  In the fallback case
    ``available_tags`` lists the wheel tags published upstream for the version.
    """

    status: ResolutionStatus
    package: DistributionPackage
    spec: WheelSpec
    index: str
    available_tags: tuple[str, ...] = ()


def _project_packages(
    page: ProjectPage, spec: WheelSpec
) -> tuple[list[tuple[DistributionPackage, Version, frozenset[Tag]]], set[Version]]:
    """Collect this project's wheels and all versions it publishes.

    Returns ``(wheels, versions)`` where *wheels* is a list of
    ``(package, version, tags)`` for each matching wheel and *versions* is the
    set of every version seen for the project across wheels and source
    distributions.
    """
    wheels: list[tuple[DistributionPackage, Version, frozenset[Tag]]] = []
    versions: set[Version] = set()
    for pkg in page.packages:
        filename = pkg.filename
        if filename.endswith(".whl"):
            try:
                name, version, _build, tags = packaging.utils.parse_wheel_filename(filename)
            except InvalidWheelFilename:
                continue
            if name != spec.name:
                continue
            versions.add(version)
            wheels.append((pkg, version, tags))
        else:
            try:
                name, version = packaging.utils.parse_sdist_filename(filename)
            except (InvalidSdistFilename, packaging.utils.InvalidName):
                continue
            if name == spec.name:
                versions.add(version)
    return wheels, versions


def _best_compatible(
    wheels: list[tuple[DistributionPackage, frozenset[Tag]]], spec: WheelSpec
) -> DistributionPackage | None:
    """Return the best tag-compatible wheel for *spec*, or ``None``.

    *wheels* pairs each candidate package with its parsed tags.
    """
    best_pkg: DistributionPackage | None = None
    best_score = -1
    for pkg, tags in wheels:
        if not _wheels_compatible(spec.tags, tags):
            continue
        score = _best_tag_score(spec.tags, tags)
        if score > best_score:
            best_score = score
            best_pkg = pkg
            if best_score == 2:
                break
    return best_pkg


def _sorted_version_strings(versions: typing.Iterable[Version]) -> tuple[str, ...]:
    """Return version strings sorted in PEP 440 order."""
    return tuple(str(v) for v in sorted(versions))


def resolve_upstream(page: ProjectPage, spec: WheelSpec, index: str = "") -> Resolution:
    """Resolve the upstream wheel to compare against *spec*.

    Classifies the outcome so callers can produce actionable errors:

    - **no version** -- the requested version is absent; raises
      :class:`~retread._errors.VersionNotFoundError` listing the available
      versions.
    - **no matching wheel** -- the version exists only as a source
      distribution; raises :class:`~retread._errors.NoWheelsError`.
    - **matched** -- a tag-compatible wheel exists; returns a
      :class:`Resolution` with :attr:`ResolutionStatus.MATCHED`.
    - **fallback** -- the version has wheels but none match the downstream
      tags; returns a :class:`Resolution` with
      :attr:`ResolutionStatus.FALLBACK` and the available wheel tags, so the
      caller can compare against a fallback wheel and report the mismatch.

    A missing *project* is detected earlier (when the index is queried) and
    raised as :class:`~retread._errors.ProjectNotFoundError`.
    """
    wheels, versions = _project_packages(page, spec)
    if not any(_version_match(v, spec.version) for v in versions):
        raise VersionNotFoundError(
            spec.filename,
            index,
            str(spec.name),
            str(spec.version),
            _sorted_version_strings(versions),
        )
    version_wheels = [
        (pkg, tags) for (pkg, version, tags) in wheels if _version_match(version, spec.version)
    ]
    if not version_wheels:
        raise NoWheelsError(spec.filename, index, str(spec.name), str(spec.version))

    best = _best_compatible(version_wheels, spec)
    if best is not None:
        return Resolution(ResolutionStatus.MATCHED, best, spec, index)

    fallback_pkg, _ = max(version_wheels, key=lambda pt: (_fallback_rank(pt[1]), pt[0].filename))
    available_tags = tuple(sorted({_wheel_tag_string(pkg.filename) for pkg, _ in version_wheels}))
    return Resolution(ResolutionStatus.FALLBACK, fallback_pkg, spec, index, available_tags)


def find_matching_wheel(
    page: ProjectPage, spec: WheelSpec, index: str = ""
) -> DistributionPackage:
    """Find a wheel on a project page whose name, version, and tags match *spec*.

    A strict, tag-compatible match.  If the downstream version carries a PEP 440
    local segment (e.g. ``1.5.0+rhaiv.5``), the public portion is matched so the
    base version (``1.5.0``) is found on the upstream index.

    Args:
        page: A :class:`pypi_simple.ProjectPage` to search.
        spec: The :class:`WheelSpec` to match against.
        index: Index URL for error reporting.

    Returns:
        The matching :class:`pypi_simple.DistributionPackage`.

    Raises:
        WheelNotFoundError: If no tag-compatible wheel is found.
    """
    wheels, _versions = _project_packages(page, spec)
    version_wheels = [
        (pkg, tags) for (pkg, version, tags) in wheels if _version_match(version, spec.version)
    ]
    best = _best_compatible(version_wheels, spec)
    if best is not None:
        return best
    raise WheelNotFoundError(spec.filename, index)

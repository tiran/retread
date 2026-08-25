"""Wheel filename parsing and upstream resolution."""

from __future__ import annotations

import dataclasses
import re
import typing

import packaging.utils
from packaging.utils import InvalidWheelFilename

from retread._errors import InvalidWheelError, WheelNotFoundError

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


def find_matching_wheel(
    page: ProjectPage, spec: WheelSpec, index: str = ""
) -> DistributionPackage:
    """Find a wheel on a project page matching the given spec.

    Searches for a wheel with the same name, version, and tags.

    Args:
        page: A :class:`pypi_simple.ProjectPage` to search.
        spec: The :class:`WheelSpec` to match against.
        index: Index URL for error reporting.

    Returns:
        The matching :class:`pypi_simple.DistributionPackage`.

    Raises:
        WheelNotFoundError: If no matching wheel is found.
    """
    for pkg in page.packages:
        if not pkg.filename.endswith(".whl"):
            continue
        try:
            name, version, _build, tags = packaging.utils.parse_wheel_filename(pkg.filename)
        except InvalidWheelFilename:
            continue
        if name == spec.name and version == spec.version and _wheels_compatible(spec.tags, tags):
            return pkg
    raise WheelNotFoundError(spec.filename, index)

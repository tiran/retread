"""Platform and ABI consistency checks for wheel files.

Validates that wheel tags, ``Root-Is-Purelib``, and file contents
(extension modules, shared libraries) are internally consistent.
These are per-wheel structural checks, not cross-wheel comparisons.
"""

from __future__ import annotations

import dataclasses
import email.parser
import logging
import re
import typing
from typing import Any

import packaging.utils
from packaging.utils import InvalidWheelFilename

if typing.TYPE_CHECKING:
    from packaging.tags import Tag

    from retread._compare import WheelComparison

logger = logging.getLogger(__name__)

_CPYTHON_EXT_RE = re.compile(r"\.cpython-(\d+\w?)-(.+)\.so$")
_SHARED_LIB_RE = re.compile(r"\.so(\.[0-9.]+)?$")

_LARGE_SCRIPT_THRESHOLD = 8192


@dataclasses.dataclass(frozen=True, slots=True)
class PlatformWarning:
    """A platform or ABI consistency issue found in a wheel."""

    side: str  # "upstream" or "downstream"
    message: str


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


def _check_single_wheel(
    side: str,
    infos: dict[str, Any],
    wheel_bytes: bytes | None,
    filename_tags: frozenset[Tag],
    dist: str,
    version: str,
) -> list[PlatformWarning]:
    """Check a single wheel for platform and ABI consistency.

    Scans the wheel's filenames for shared libraries and extension
    modules, then validates them against the wheel's tags and
    ``Root-Is-Purelib`` property.
    """
    warnings: list[PlatformWarning] = []

    has_shared_libs = False
    cpython_versions: set[str] = set()
    has_abi3 = False
    has_abi3t = False

    for fname in infos:
        if any(part.endswith(".libs") for part in fname.split("/")):
            continue

        m = _CPYTHON_EXT_RE.search(fname)
        if m:
            cpython_versions.add(m.group(1))
            has_shared_libs = True
            continue

        if fname.endswith(".abi3.so"):
            has_abi3 = True
            has_shared_libs = True
            continue

        if fname.endswith(".abi3t.so"):
            has_abi3t = True
            has_shared_libs = True
            continue

        if _SHARED_LIB_RE.search(fname) is not None:
            has_shared_libs = True

    # Scripts heuristic: large files in data/scripts/ suggest platlib
    scripts_prefix = f"{dist}-{version}.data/scripts/"
    has_large_scripts = any(
        info.file_size > _LARGE_SCRIPT_THRESHOLD
        for fname, info in infos.items()
        if fname.startswith(scripts_prefix)
    )

    # Parse WHEEL file if available
    root_is_purelib = False
    wheel_tags: list[str] = []
    if wheel_bytes is not None:
        root_is_purelib, wheel_tags = _parse_wheel_tags(wheel_bytes)

    # Check 0: WHEEL Tag entries must match filename tags
    if wheel_tags and filename_tags:
        filename_tag_strs = {
            f"{tag.interpreter}-{tag.abi}-{tag.platform}" for tag in filename_tags
        }
        wheel_tag_set = set(wheel_tags)
        if wheel_tag_set != filename_tag_strs:
            only_in_wheel = sorted(wheel_tag_set - filename_tag_strs)
            only_in_filename = sorted(filename_tag_strs - wheel_tag_set)
            parts: list[str] = []
            if only_in_wheel:
                parts.append(f"only in WHEEL: {', '.join(only_in_wheel)}")
            if only_in_filename:
                parts.append(f"only in filename: {', '.join(only_in_filename)}")
            warnings.append(
                PlatformWarning(
                    side,
                    f"WHEEL Tag entries don't match filename tags: {'; '.join(parts)}",
                )
            )

    tag_platforms = {tag.platform for tag in filename_tags}
    tag_interpreters = {tag.interpreter for tag in filename_tags}
    tag_abis = {tag.abi for tag in filename_tags}

    # Check: platform-specific wheel with no shared libraries
    if not has_shared_libs and not has_large_scripts:
        has_platform_specific = tag_platforms and tag_platforms != {"any"}
        if has_platform_specific:
            warnings.append(
                PlatformWarning(
                    side,
                    "wheel has platform-specific tags but contains no shared"
                    " libraries or native extensions",
                )
            )
        return warnings

    if has_shared_libs:
        # Check 1: shared libs should not be in a purelib wheel
        if wheel_bytes is not None and root_is_purelib:
            warnings.append(
                PlatformWarning(
                    side,
                    "Root-Is-Purelib is set but wheel contains shared libraries",
                )
            )

        # Check 2: shared libs require a platform-specific tag
        if tag_platforms == {"any"}:
            warnings.append(
                PlatformWarning(
                    side,
                    "wheel contains shared libraries but has platform tag 'any'",
                )
            )

        # Check 3: cpython-specific extensions must match version tags
        for ver in sorted(cpython_versions):
            expected_interp = f"cp{ver}"
            if expected_interp not in tag_interpreters:
                warnings.append(
                    PlatformWarning(
                        side,
                        f"cpython-specific extension for {expected_interp}"
                        f" but wheel tags don't include {expected_interp}",
                    )
                )

        # Check 4: abi3/abi3t extensions require abi3 tag and cpython interpreter
        if has_abi3 or has_abi3t:
            has_abi3_tag = "abi3" in tag_abis
            has_cpython_interp = any(i.startswith("cp3") for i in tag_interpreters)

            if not has_abi3_tag:
                warnings.append(
                    PlatformWarning(
                        side,
                        "abi3 extension found but wheel tags don't include abi3 ABI",
                    )
                )
            if not has_cpython_interp:
                warnings.append(
                    PlatformWarning(
                        side,
                        "abi3 extension found but wheel tags don't include a CPython interpreter",
                    )
                )

    # Check 5: scripts heuristic
    if (
        has_large_scripts
        and not has_shared_libs
        and ((wheel_bytes is not None and root_is_purelib) or tag_platforms == {"any"})
    ):
        warnings.append(
            PlatformWarning(
                side,
                "data/scripts/ contains large files suggesting native"
                " executables, but wheel claims purelib or has platform"
                " tag 'any' (heuristic)",
            )
        )

    return warnings


def check_platform_abi(
    result: WheelComparison,
    *,
    upstream_infos: dict[str, Any],
    downstream_infos: dict[str, Any],
    upstream_wheel: bytes | None = None,
    downstream_wheel: bytes | None = None,
) -> WheelComparison:
    """Validate platform and ABI consistency for both wheels.

    Reads the ``WHEEL`` file from each side and cross-checks
    ``Root-Is-Purelib`` and tag entries against the actual file
    contents of the wheel (shared libraries, extension modules).
    """
    warnings: list[PlatformWarning] = []

    try:
        _, _, _, upstream_tags = packaging.utils.parse_wheel_filename(result.upstream_wheel)
    except InvalidWheelFilename:
        upstream_tags = frozenset()

    try:
        _, _, _, downstream_tags = packaging.utils.parse_wheel_filename(result.downstream_wheel)
    except InvalidWheelFilename:
        downstream_tags = frozenset()

    up_dist = result.dist
    up_version = str(result.upstream_version)
    down_version = str(result.downstream_version)

    upstream_warnings = _check_single_wheel(
        "upstream", upstream_infos, upstream_wheel, upstream_tags, up_dist, up_version
    )
    for w in upstream_warnings:
        logger.warning("platform check [%s]: %s", w.side, w.message)
    warnings.extend(upstream_warnings)

    downstream_warnings = _check_single_wheel(
        "downstream", downstream_infos, downstream_wheel, downstream_tags, up_dist, down_version
    )
    for w in downstream_warnings:
        logger.warning("platform check [%s]: %s", w.side, w.message)
    warnings.extend(downstream_warnings)

    if not warnings:
        return result

    return dataclasses.replace(result, platform_warnings=tuple(warnings))

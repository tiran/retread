"""Platform and ABI consistency checker.

Validates that each wheel's tags, ``Root-Is-Purelib``, and file contents
(extension modules, shared libraries) are internally consistent.  These are
per-wheel structural checks, not cross-wheel comparisons.
"""

from __future__ import annotations

import logging
import re
import typing

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from retread._enums import Side
from retread._findings import NO_SHARED_LIBS_WARNING, PlatformWarning

if typing.TYPE_CHECKING:
    from retread._findings import Comparison
    from retread._wheel import WheelInfo
    from retread.checker._engine import Pool

logger = logging.getLogger(__name__)

_CPYTHON_EXT_RE = re.compile(r"\.cpython-(\d+\w?)-(.+)\.so$")
# Windows CPython extension modules, e.g. ``_foo.cp312-win_amd64.pyd``.
_PYD_EXT_RE = re.compile(r"\.cp(\d+\w?)-[^/]+\.pyd$")
# Linux/macOS shared objects (``.so``, ``libfoo.so.1``), macOS dynamic
# libraries (``.dylib``), and Windows DLLs (``.dll``).
_SHARED_LIB_RE = re.compile(r"(\.so(\.[0-9.]+)?|\.dylib|\.dll)$")


def _expand_compound_tags(tags: list[str]) -> set[str]:
    """Expand compound WHEEL tags into individual tags.

    A WHEEL file may list compound tags where one or more components
    are dot-separated (PEP 425 / PEP 600), e.g.:

    * ``cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64``
      expands to ``cp312-cp312-manylinux_2_17_x86_64`` and
      ``cp312-cp312-manylinux2014_x86_64``
    * ``py2.py3-none-any`` expands to ``py2-none-any`` and
      ``py3-none-any``

    The filename parser (``packaging.utils.parse_wheel_filename``)
    already performs this expansion, so expanding WHEEL tags makes
    the sets comparable.
    """
    expanded: set[str] = set()
    for tag in tags:
        parts = tag.split("-")
        if len(parts) != 3:
            expanded.add(tag)
            continue
        interps = parts[0].split(".")
        abis = parts[1].split(".")
        platforms = parts[2].split(".")
        for interp in interps:
            for abi in abis:
                for platform in platforms:
                    expanded.add(f"{interp}-{abi}-{platform}")
    return expanded


def _check_single_wheel(
    side: Side, wheel: WheelInfo, large_script_threshold: int
) -> list[PlatformWarning]:
    """Check a single wheel for platform and ABI consistency.

    Scans the wheel's filenames for shared libraries and extension
    modules, then validates them against the wheel's tags and
    ``Root-Is-Purelib`` property.  Also checks version normalization
    and that the METADATA Name and Version match the wheel filename.
    """
    warnings: list[PlatformWarning] = []
    dist = wheel.dist
    normalized_ver = str(wheel.version)
    raw_ver = wheel.raw_version
    filename_tags = wheel.tags
    names = wheel.names

    # Version normalization check
    if normalized_ver != raw_ver:
        warnings.append(
            PlatformWarning(
                side,
                f"wheel filename version '{raw_ver}' is not normalized"
                f" (expected '{normalized_ver}')",
            )
        )

    # METADATA Name and Version must be present and match the filename
    if wheel.metadata_fields is not None:
        meta = wheel.metadata_fields
        meta_version = meta.get("version", "")
        # Compare parsed versions so that equivalent spellings (e.g. 1.0 vs
        # 1.0.0) do not produce a spurious mismatch.  An unparseable METADATA
        # version is reported as-is rather than crashing the check.
        try:
            version_matches = Version(meta_version) == wheel.version
        except InvalidVersion:
            version_matches = False
        if not version_matches:
            warnings.append(
                PlatformWarning(
                    side,
                    f"METADATA Version '{meta_version}' does not match"
                    f" filename version '{normalized_ver}'",
                )
            )
        meta_name = meta.get("name", "")
        if canonicalize_name(meta_name) != canonicalize_name(dist):
            warnings.append(
                PlatformWarning(
                    side,
                    f"METADATA Name '{meta_name}' does not match filename distribution '{dist}'",
                )
            )

    has_shared_libs = False
    cpython_versions: set[str] = set()
    has_abi3 = False
    has_abi3t = False

    for fname in names:
        if any(part.endswith(".libs") for part in fname.split("/")):
            continue

        m = _CPYTHON_EXT_RE.search(fname)
        if m:
            cpython_versions.add(m.group(1))
            has_shared_libs = True
            continue

        pyd = _PYD_EXT_RE.search(fname)
        if pyd:
            cpython_versions.add(pyd.group(1))
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
            continue

        # Plain (untagged) Windows extension module, e.g. ``_foo.pyd``.
        if fname.endswith(".pyd"):
            has_shared_libs = True

    # Scripts heuristic: large files in data/scripts/ suggest platlib
    scripts_prefix = f"{dist}-{raw_ver}.data/scripts/"
    has_large_scripts = any(
        stat.size > large_script_threshold
        for path, stat in wheel.files.items()
        if str(path).startswith(scripts_prefix)
    )

    has_wheel = wheel.di_wheel is not None
    root_is_purelib = wheel.root_is_purelib
    wheel_tags = wheel.wheel_tags

    # Check 0: WHEEL Tag entries must match filename tags
    if wheel_tags and filename_tags:
        filename_tag_strs = {
            f"{tag.interpreter}-{tag.abi}-{tag.platform}" for tag in filename_tags
        }
        wheel_tag_set = _expand_compound_tags(wheel_tags)
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
            warnings.append(PlatformWarning(side, NO_SHARED_LIBS_WARNING))
        return warnings

    if has_shared_libs:
        # Check 1: shared libs should not be in a purelib wheel
        if has_wheel and root_is_purelib:
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
            # Strip trailing 't' (free-threaded marker); the 't' belongs
            # to the ABI tag (e.g. cp313t), not the interpreter (cp313).
            expected_interp = f"cp{ver.rstrip('t')}"
            if expected_interp not in tag_interpreters:
                warnings.append(
                    PlatformWarning(
                        side,
                        f"cpython-specific extension for {expected_interp}"
                        f" but wheel tags don't include {expected_interp}",
                    )
                )

        # Check 4: abi3/abi3t extensions require abi3 tag and cpython interpreter.
        # Skip when cpython-specific extensions are also present -- the
        # version-specific tags (e.g. cp312-cp312) are correct for those
        # and the abi3 extensions are compatible with any cpython version.
        if (has_abi3 or has_abi3t) and not cpython_versions:
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
        and ((has_wheel and root_is_purelib) or tag_platforms == {"any"})
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


class PlatformChecker:
    """Validate platform and ABI consistency for both wheels.

    Cross-checks each wheel's ``Root-Is-Purelib`` and WHEEL tag entries
    against its actual file contents (shared libraries, extension modules),
    and validates version normalization and METADATA version consistency.
    """

    name = "platform"
    priority = 220

    def check(self, comparison: Comparison, pool: Pool) -> None:
        warnings: list[PlatformWarning] = []
        threshold = comparison.context.large_script_threshold
        for side, wheel in (
            (Side.UPSTREAM, comparison.upstream),
            (Side.DOWNSTREAM, comparison.downstream),
        ):
            side_warnings = _check_single_wheel(side, wheel, threshold)
            for w in side_warnings:
                logger.info("platform check [%s]: %s", w.side, w.message)
            warnings.extend(side_warnings)
        if warnings:
            comparison.analysis.platform_warnings.extend(warnings)

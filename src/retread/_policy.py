"""Per-package policy configuration.

Loads TOML policy files from a directory and applies them to
comparison results, overriding severities for expected differences.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import pathlib  # noqa: TC003 (used at runtime)
import tomllib

import packaging.utils

from retread._compare import Severity, WheelComparison
from retread._errors import PolicyError
from retread._platform import NO_SHARED_LIBS_WARNING

# The ``platlib`` key suppresses this specific platform warning; matched
# by identity against the canonical message rather than a substring so a
# reworded warning cannot silently disable the policy.
_NO_SHARED_LIBS = NO_SHARED_LIBS_WARNING

_VALID_KEYS = frozenset(
    {
        "description",
        "ignore_dependency_metadata",
        "ignore_differences",
        "ignore_extra_downstream",
        "ignore_missing_downstream",
        "platlib",
    }
)

# Repeated METADATA fields accepted when ``ignore_dependency_metadata`` is set.
_DEPENDENCY_METADATA_FIELDS = frozenset({"Requires-Dist", "Provides-Extra"})


@dataclasses.dataclass(frozen=True, slots=True)
class VersionPolicy:
    """Policy rules for a specific version selector."""

    description: str
    ignore_differences: tuple[str, ...]
    ignore_missing_downstream: tuple[str, ...]
    ignore_extra_downstream: tuple[str, ...]
    platlib: bool
    ignore_dependency_metadata: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class PackagePolicy:
    """Policy for a single distribution package."""

    dist_name: str
    versions: dict[str, VersionPolicy]


def _validate_filename(path: pathlib.Path) -> str:
    """Validate a policy filename and return the canonical dist name.

    The filename (without ``.toml`` suffix) must be a normalized
    distribution name with hyphens replaced by underscores.  Validation
    is done by round-tripping through ``canonicalize_name``.

    Raises :class:`PolicyError` if the filename is not normalized.
    """
    stem = path.stem
    expected = packaging.utils.canonicalize_name(stem).replace("-", "_")
    if stem != expected:
        raise PolicyError(
            f"policy filename {path.name!r} is not normalized (expected {expected!r})"
        )
    return packaging.utils.canonicalize_name(stem)


def _parse_version_table(table: dict, filename: str, version_key: str) -> VersionPolicy:
    """Parse and validate a single version table within a policy file."""
    unknown = set(table) - _VALID_KEYS
    if unknown:
        raise PolicyError(
            f"{filename}: unknown keys in [{version_key!r}]: {', '.join(sorted(unknown))}"
        )

    description = table.get("description", "")
    if not isinstance(description, str):
        raise PolicyError(f"{filename}: 'description' in [{version_key!r}] must be a string")

    for key in ("ignore_differences", "ignore_missing_downstream", "ignore_extra_downstream"):
        value = table.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise PolicyError(
                f"{filename}: '{key}' in [{version_key!r}] must be a list of strings"
            )

    for key in ("platlib", "ignore_dependency_metadata"):
        if not isinstance(table.get(key, False), bool):
            raise PolicyError(f"{filename}: '{key}' in [{version_key!r}] must be a boolean")

    return VersionPolicy(
        description=description,
        ignore_differences=tuple(table.get("ignore_differences", [])),
        ignore_missing_downstream=tuple(table.get("ignore_missing_downstream", [])),
        ignore_extra_downstream=tuple(table.get("ignore_extra_downstream", [])),
        platlib=table.get("platlib", False),
        ignore_dependency_metadata=table.get("ignore_dependency_metadata", False),
    )


def _load_policy_file(path: pathlib.Path) -> PackagePolicy:
    """Load and validate a single policy TOML file."""
    dist_name = _validate_filename(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"{path.name}: invalid TOML: {exc}") from None

    versions: dict[str, VersionPolicy] = {}
    for key, table in data.items():
        if not isinstance(table, dict):
            raise PolicyError(f"{path.name}: top-level key {key!r} must be a table")
        versions[key] = _parse_version_table(table, path.name, key)

    return PackagePolicy(dist_name=dist_name, versions=versions)


def load_policy_dir(policy_dir: pathlib.Path) -> dict[str, PackagePolicy]:
    """Load all policy files from a directory.

    Returns a dict mapping canonical distribution names to their
    policies.

    Raises :class:`PolicyError` on invalid filenames or malformed TOML.
    """
    policies: dict[str, PackagePolicy] = {}
    for path in sorted(policy_dir.glob("*.toml")):
        policy = _load_policy_file(path)
        policies[policy.dist_name] = policy
    return policies


def _matches_any_pattern(filename: str, patterns: tuple[str, ...]) -> bool:
    """Return True if *filename* matches any glob pattern.

    Uses :func:`fnmatch.fnmatch` semantics where ``*`` matches
    across path separators.

    .. todo:: Add ``**`` recursive glob support using
       ``PurePosixPath.full_match()`` (Python 3.13+).
    """
    return any(fnmatch.fnmatch(filename, pat) for pat in patterns)


def lookup_policy(
    policies: dict[str, PackagePolicy],
    dist: str,
    version: str,
) -> VersionPolicy | None:
    """Look up the effective policy for a distribution and version.

    Returns the :class:`VersionPolicy` for the ``"*"`` version
    selector, or ``None`` if no policy exists for the package.

    .. todo:: Support version-range selectors (e.g. ``">=4.0,<5.0"``)
       and merge applicable rules.
    """
    canonical = packaging.utils.canonicalize_name(dist)
    pkg = policies.get(canonical)
    if pkg is None:
        return None
    return pkg.versions.get("*")


def apply_policy(
    result: WheelComparison,
    policy: VersionPolicy,
) -> WheelComparison:
    """Apply a version policy to a comparison result.

    Overrides severity to :attr:`Severity.IGNORED` for:

    - ``only_upstream`` entries matching ``ignore_missing_downstream``
      patterns.
    - ``only_downstream`` entries matching ``ignore_extra_downstream``
      patterns.
    - ``different`` entries matching ``ignore_differences`` patterns.

    When ``platlib`` is set, the "no shared libraries" platform warning
    is removed from
    :attr:`~retread._compare.WheelComparison.platform_warnings`.

    When ``ignore_dependency_metadata`` is set, ``Requires-Dist`` and
    ``Provides-Extra`` differences in
    :attr:`~retread._compare.WheelComparison.metadata_field_diffs` are
    marked :attr:`~retread._compare.MetadataFieldDiff.ignored` so they
    are still reported but not treated as errors.

    Returns the original result unchanged if no rule matched, otherwise a
    new :class:`WheelComparison` via :func:`dataclasses.replace`.
    """
    changed = False

    if policy.ignore_missing_downstream:
        new_only_upstream = list(result.only_upstream)
        for i, entry in enumerate(new_only_upstream):
            if _matches_any_pattern(entry.filename, policy.ignore_missing_downstream):
                new_only_upstream[i] = dataclasses.replace(entry, severity=Severity.IGNORED)
                changed = True
        only_upstream = tuple(new_only_upstream)
    else:
        only_upstream = result.only_upstream

    if policy.ignore_extra_downstream:
        new_only_downstream = list(result.only_downstream)
        for i, entry in enumerate(new_only_downstream):
            if _matches_any_pattern(entry.filename, policy.ignore_extra_downstream):
                new_only_downstream[i] = dataclasses.replace(entry, severity=Severity.IGNORED)
                changed = True
        only_downstream = tuple(new_only_downstream)
    else:
        only_downstream = result.only_downstream

    if policy.ignore_differences:
        new_different = list(result.different)
        for i, diff in enumerate(new_different):
            if _matches_any_pattern(diff.filename, policy.ignore_differences):
                new_different[i] = dataclasses.replace(diff, severity=Severity.IGNORED)
                changed = True
        different = tuple(new_different)
    else:
        different = result.different

    # platlib: suppress the "no shared libraries" platform warning.
    if policy.platlib and result.platform_warnings:
        filtered = tuple(w for w in result.platform_warnings if w.message != _NO_SHARED_LIBS)
        if len(filtered) != len(result.platform_warnings):
            platform_warnings = filtered
            changed = True
        else:
            platform_warnings = result.platform_warnings
    else:
        platform_warnings = result.platform_warnings

    # Accept Requires-Dist / Provides-Extra differences: mark them ignored
    # so they are still reported (and serialized) but not treated as an
    # error, rather than dropping them silently.
    if policy.ignore_dependency_metadata and result.metadata_field_diffs:
        new_field_diffs = list(result.metadata_field_diffs)
        for i, diff in enumerate(new_field_diffs):
            if not diff.ignored and diff.field in _DEPENDENCY_METADATA_FIELDS:
                new_field_diffs[i] = dataclasses.replace(diff, ignored=True)
                changed = True
        metadata_field_diffs = tuple(new_field_diffs)
    else:
        metadata_field_diffs = result.metadata_field_diffs

    if not changed:
        return result

    return dataclasses.replace(
        result,
        only_upstream=only_upstream,
        only_downstream=only_downstream,
        different=different,
        platform_warnings=platform_warnings,
        metadata_field_diffs=metadata_field_diffs,
    )

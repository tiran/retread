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

from retread._errors import PolicyError

_VALID_KEYS = frozenset(
    {
        "allow_cross_platform",
        "description",
        "ignore_dependency_metadata",
        "ignore_differences",
        "ignore_extra_downstream",
        "ignore_missing_downstream",
        "platlib",
    }
)


@dataclasses.dataclass(frozen=True, slots=True)
class VersionPolicy:
    """Policy rules for a specific version selector."""

    description: str
    ignore_differences: tuple[str, ...]
    ignore_missing_downstream: tuple[str, ...]
    ignore_extra_downstream: tuple[str, ...]
    platlib: bool
    ignore_dependency_metadata: bool = False
    allow_cross_platform: bool = False


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

    for key in ("platlib", "ignore_dependency_metadata", "allow_cross_platform"):
        if not isinstance(table.get(key, False), bool):
            raise PolicyError(f"{filename}: '{key}' in [{version_key!r}] must be a boolean")

    return VersionPolicy(
        description=description,
        ignore_differences=tuple(table.get("ignore_differences", [])),
        ignore_missing_downstream=tuple(table.get("ignore_missing_downstream", [])),
        ignore_extra_downstream=tuple(table.get("ignore_extra_downstream", [])),
        platlib=table.get("platlib", False),
        ignore_dependency_metadata=table.get("ignore_dependency_metadata", False),
        allow_cross_platform=table.get("allow_cross_platform", False),
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

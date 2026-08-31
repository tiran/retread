"""Per-package policy checker.

Applies a :class:`~retread._policy.VersionPolicy` to the analysis, downgrading
matching findings to :attr:`~retread._enums.Severity.IGNORED` and marking
accepted METADATA field differences.  Runs last so it can override severities
set by every earlier checker.
"""

from __future__ import annotations

import typing

import packaging.utils
from packaging.version import Version

from retread._enums import Severity
from retread._findings import NO_SHARED_LIBS_WARNING
from retread._policy import _matches_any_pattern, lookup_policy

if typing.TYPE_CHECKING:
    from retread._findings import Comparison
    from retread._policy import VersionPolicy
    from retread._wheel import WheelInfo
    from retread.checker._engine import Pool

# Repeated METADATA fields accepted when ``ignore_dependency_metadata`` is set.
_DEPENDENCY_METADATA_FIELDS = frozenset({"Requires-Dist", "Provides-Extra"})


def _core_identity_matches(up: WheelInfo, down: WheelInfo) -> bool:
    """Return True if both wheels' METADATA agree on Name and Version.

    Used to decide whether a METADATA mismatch is *purely* dependency
    metadata (Requires-Dist / Provides-Extra) and therefore safe to ignore.
    Both metadata sets are validated at load, so the fields are present and the
    versions parse.
    """
    up_meta = up.metadata_fields
    down_meta = down.metadata_fields
    if up_meta is None or down_meta is None:
        return False
    up_name = packaging.utils.canonicalize_name(up_meta["name"])
    down_name = packaging.utils.canonicalize_name(down_meta["name"])
    if up_name != down_name:
        return False
    return Version(up_meta["version"]).public == Version(down_meta["version"]).public


def _ignore_metadata_error(comparison: Comparison) -> None:
    """Downgrade a dependency-only METADATA error to IGNORED.

    Called when ``ignore_dependency_metadata`` is set.  The METADATA file's
    error severity (set by :class:`~retread.checker.MetadataChecker` when the
    core fields differ) is downgraded to IGNORED only when Name and Version
    still match, so that a genuine Name/Version mismatch stays an error.
    """
    if not _core_identity_matches(comparison.upstream, comparison.downstream):
        return
    analysis = comparison.analysis
    up_meta_path = f"{comparison.upstream.dist_info}/METADATA"
    down_meta_path = f"{comparison.downstream.dist_info}/METADATA"
    for diff in analysis.different:
        if diff.filename == up_meta_path and diff.severity is Severity.ERROR:
            diff.severity = Severity.IGNORED
    for entry in analysis.only_upstream:
        if entry.filename == up_meta_path and entry.severity is Severity.ERROR:
            entry.severity = Severity.IGNORED
    for entry in analysis.only_downstream:
        if entry.filename == down_meta_path and entry.severity is Severity.ERROR:
            entry.severity = Severity.IGNORED


def apply_policy(comparison: Comparison, version_policy: VersionPolicy) -> None:
    """Apply *version_policy* to *comparison* in place.

    Overrides severity to :attr:`Severity.IGNORED` for:

    - ``only_upstream`` entries matching ``ignore_missing_downstream``.
    - ``only_downstream`` entries matching ``ignore_extra_downstream``.
    - ``different`` entries matching ``ignore_differences``.

    When ``platlib`` is set, the "no shared libraries" platform warning is
    removed.  When ``ignore_dependency_metadata`` is set, ``Requires-Dist``
    and ``Provides-Extra`` field diffs are marked ignored so they are still
    reported but not treated as errors.
    """
    analysis = comparison.analysis

    if version_policy.ignore_missing_downstream:
        for entry in analysis.only_upstream:
            if _matches_any_pattern(entry.filename, version_policy.ignore_missing_downstream):
                entry.severity = Severity.IGNORED

    if version_policy.ignore_extra_downstream:
        for entry in analysis.only_downstream:
            if _matches_any_pattern(entry.filename, version_policy.ignore_extra_downstream):
                entry.severity = Severity.IGNORED

    if version_policy.ignore_differences:
        for diff in analysis.different:
            if _matches_any_pattern(diff.filename, version_policy.ignore_differences):
                diff.severity = Severity.IGNORED

    # platlib: suppress the "no shared libraries" platform warning.  Matched
    # by identity against the canonical message rather than a substring so a
    # reworded warning cannot silently disable the policy.
    if version_policy.platlib and analysis.platform_warnings:
        analysis.platform_warnings[:] = [
            w for w in analysis.platform_warnings if w.message != NO_SHARED_LIBS_WARNING
        ]

    # Accept Requires-Dist / Provides-Extra differences: mark them ignored
    # so they are still reported (and serialized) but not treated as an error.
    if version_policy.ignore_dependency_metadata:
        for diff in analysis.metadata_field_diffs:
            if not diff.ignored and diff.field in _DEPENDENCY_METADATA_FIELDS:
                diff.ignored = True
        _ignore_metadata_error(comparison)


class PolicyChecker:
    """Apply the matching per-package policy from :attr:`Context.policy`."""

    name = "policy"
    # Runs last of all (after catch-all at 1000) so it can override severities
    # set by every earlier checker.  See the priority bands in ``retread.checker``.
    priority = 2_000

    def check(self, comparison: Comparison, pool: Pool) -> None:
        policy_map = comparison.context.policy
        if not policy_map:
            return
        version_policy = lookup_policy(
            policy_map, comparison.upstream.canonical, str(comparison.upstream.version)
        )
        if version_policy is None:
            return
        apply_policy(comparison, version_policy)

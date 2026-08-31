"""METADATA consistency checker.

Compares the core METADATA fields (Name, Version, Requires-Dist,
Provides-Extra) of the two wheels, upgrading the METADATA finding to ERROR
when they differ after normalization, and records per-field set differences
for the repeated fields.
"""

from __future__ import annotations

import typing

import packaging.utils
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from retread._enums import Classification, Severity
from retread._findings import MetadataFieldDiff

if typing.TYPE_CHECKING:
    from packaging.metadata import RawMetadata

    from retread._findings import Comparison
    from retread.checker._engine import Pool

# Operators for which trailing-zero stripping is unsafe.  ``~=`` (compatible
# release) requires at least two release segments, so stripping ``~=22.0`` to
# ``~=22`` produces an invalid specifier.  ``===`` (arbitrary equality) matches
# the version string verbatim, so stripping would change its meaning.
_NO_STRIP_OPERATORS = frozenset({"~=", "==="})


def _normalize_version_spec(spec: str, operator: str | None = None) -> str:
    """Normalize a single version string, keeping wildcards unchanged.

    Uses :func:`packaging.utils.canonicalize_version`.  Trailing-zero
    release segments are stripped so that equivalent spellings like ``5``
    and ``5.0`` compare equal, but only when *operator* is supplied and
    is not one of ``~=`` or ``===`` (where stripping would produce an
    invalid or semantically different specifier).  PEP 440 wildcard
    specifiers (e.g. ``2.0.*``, ``0.41.*``) that cannot be parsed as
    versions are returned unchanged.
    """
    strip = operator is not None and operator not in _NO_STRIP_OPERATORS
    return packaging.utils.canonicalize_version(spec, strip_trailing_zero=strip)


def _normalize_req(raw: str) -> str:
    """Normalize a Requires-Dist entry for comparison.

    Uses ``Requirement`` to normalize whitespace, quoting, and marker
    formatting, then canonicalizes the distribution name so that
    ``typing-extensions`` and ``typing_extensions`` compare equal.
    Version specifiers are normalized so that PEP 440 equivalent
    spellings like ``<5`` and ``<5.0`` compare equal.  Trailing-zero
    stripping is skipped for the ``~=`` and ``===`` operators, where it
    would produce an invalid or semantically different specifier.
    Wildcard versions (``==2.0.*``) are kept as-is.
    """
    req = Requirement(raw)
    req.name = packaging.utils.canonicalize_name(req.name)
    req.specifier = SpecifierSet(
        ",".join(
            f"{s.operator}{_normalize_version_spec(s.version, s.operator)}" for s in req.specifier
        )
    )
    return str(req)


def _normalize_extra(extra: str) -> str:
    """Normalize a Provides-Extra name for comparison.

    Extra names are canonicalized per PEP 685 (like distribution
    names) so that spellings such as ``code_syntax_highlighting`` and
    ``code-syntax-highlighting`` compare equal.
    """
    return packaging.utils.canonicalize_name(extra)


def _core_match(up: RawMetadata, down: RawMetadata) -> bool:
    """Check whether core metadata fields match between two parsed METADATA sets.

    Compares Name, Version (single-value) and Requires-Dist,
    Provides-Extra (multi-value, compared as sets with normalization).
    Name is canonicalized per PEP 503.  Requires-Dist entries are
    normalized via :func:`_normalize_req` and Provides-Extra names via
    :func:`_normalize_extra` so that cosmetic differences (whitespace,
    quoting, name spelling, version spelling, order) are ignored.
    """
    up_name = packaging.utils.canonicalize_name(up["name"])
    down_name = packaging.utils.canonicalize_name(down["name"])
    if up_name != down_name:
        return False
    # Compare public versions so that local segments (e.g. 1.5.0+rhaiv.5
    # vs 1.5.0) are treated as equivalent.
    if Version(up["version"]).public != Version(down["version"]).public:
        return False

    up_extras = {_normalize_extra(e) for e in up.get("provides_extra") or []}
    down_extras = {_normalize_extra(e) for e in down.get("provides_extra") or []}
    if up_extras != down_extras:
        return False

    up_reqs = {_normalize_req(r) for r in up.get("requires_dist") or []}
    down_reqs = {_normalize_req(r) for r in down.get("requires_dist") or []}
    return up_reqs == down_reqs


def _field_diffs(up: RawMetadata, down: RawMetadata) -> list[MetadataFieldDiff]:
    """Return normalized set differences for repeated METADATA fields.

    Compares ``Requires-Dist`` and ``Provides-Extra`` after the same
    normalization used by :func:`_core_match` and reports the values
    present on only one side.  Fields that match (or are absent on both
    sides) are omitted, so an empty list means the reportable fields agree.
    """
    fields = (
        (
            "Requires-Dist",
            {_normalize_req(r) for r in up.get("requires_dist") or []},
            {_normalize_req(r) for r in down.get("requires_dist") or []},
        ),
        (
            "Provides-Extra",
            {_normalize_extra(e) for e in up.get("provides_extra") or []},
            {_normalize_extra(e) for e in down.get("provides_extra") or []},
        ),
    )

    diffs: list[MetadataFieldDiff] = []
    for field, up_values, down_values in fields:
        only_up = tuple(sorted(up_values - down_values))
        only_down = tuple(sorted(down_values - up_values))
        if only_up or only_down:
            diffs.append(MetadataFieldDiff(field, only_up, only_down))
    return diffs


class MetadataChecker:
    """Upgrade the METADATA finding to ERROR when core fields differ.

    Handles two cases:

    * **Same dist-info prefix** (common): both METADATA files share a
      filename and the difference appears in ``analysis.different``.
    * **Different dist-info prefix** (e.g. local-version differs): the two
      METADATA files have different paths and appear in ``only_upstream`` /
      ``only_downstream``.  Core fields are still compared and severities
      updated accordingly.
    """

    name = "metadata"
    priority = 200

    def check(self, comparison: Comparison, pool: Pool) -> None:
        up = comparison.upstream
        down = comparison.downstream
        analysis = comparison.analysis

        if up.metadata_fields is None or down.metadata_fields is None:
            return

        up_meta_path = f"{up.dist_info}/METADATA"
        down_meta_path = f"{down.dist_info}/METADATA"

        if up_meta_path == down_meta_path:
            diffs = [d for d in analysis.different if d.filename == up_meta_path]
            if not diffs:
                return
            for diff in diffs:
                if not _core_match(up.metadata_fields, down.metadata_fields):
                    diff.severity = Severity.ERROR
            if not analysis.metadata_field_diffs:
                analysis.metadata_field_diffs.extend(
                    _field_diffs(up.metadata_fields, down.metadata_fields)
                )
            return

        # Different dist-info prefixes: METADATA split across only_upstream /
        # only_downstream.
        up_entry = next((e for e in analysis.only_upstream if e.filename == up_meta_path), None)
        down_entry = next(
            (e for e in analysis.only_downstream if e.filename == down_meta_path), None
        )
        if up_entry is None or down_entry is None:
            return

        match = _core_match(up.metadata_fields, down.metadata_fields)
        severity = Severity.NOTICE if match else Severity.ERROR
        for entry in (up_entry, down_entry):
            entry.severity = severity
            entry.classification = Classification.METADATA
        analysis.metadata_field_diffs.extend(
            _field_diffs(up.metadata_fields, down.metadata_fields)
        )

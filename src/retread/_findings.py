"""Findings, the mutable :class:`Analysis`, and the frozen :class:`Comparison`.

Findings are the individual results a checker produces.  They are **mutable**
so checkers can adjust them in place (e.g. upgrade a severity).  :class:`Analysis`
is the evolving bag of findings threaded through the checker pipeline.
:class:`Comparison` is the frozen object returned to callers, wrapping the two
:class:`~retread._wheel.WheelInfo` inputs and the final :class:`Analysis` and
exposing the summary API (:meth:`~Comparison.is_identical`,
:meth:`~Comparison.has_errors`, :meth:`~Comparison.to_dict`).
"""

from __future__ import annotations

import dataclasses
import typing
from typing import Any

from retread._enums import Classification, Severity, Side

if typing.TYPE_CHECKING:
    from retread._context import Context
    from retread._wheel import WheelInfo


# Canonical message for a platform-specific wheel that ships no shared
# libraries or native extensions.  Defined here as the single source of
# truth so the ``platlib`` policy key can match it exactly (see
# ``retread.checker._policy``) without duplicating the wording.
NO_SHARED_LIBS_WARNING = (
    "wheel has platform-specific tags but contains no shared libraries or native extensions"
)


def _origin_dict(wheel: WheelInfo) -> dict[str, Any]:
    """Serialize a wheel's origin and index metadata for :meth:`Comparison.to_dict`."""
    origin = wheel.origin
    return {
        "source": origin.source,
        "file_size": wheel.file_size,
        "upload_time": origin.upload_time.isoformat() if origin.upload_time is not None else None,
        "provenance_url": origin.provenance_url,
        "has_sdist": origin.has_sdist,
        "has_wheels": origin.has_wheels,
        "wheel_tags": list(origin.wheel_tags),
    }


@dataclasses.dataclass(order=True, slots=True)
class FileEntry:
    """A file present only in one side of the comparison.

    Ordered and compared by :attr:`filename` alone, so a list of entries can
    be sorted into path order regardless of severity or classification.

    A *hidden* entry counts towards identity (its presence makes the wheels
    differ) but is omitted from every report and from the error summary.  A
    checker that collapses many files into one summary finding (see
    :class:`VenvBundle`) claims them as hidden entries so they do not flood the
    per-file diff.
    """

    filename: str
    severity: Severity = dataclasses.field(compare=False)
    classification: Classification = dataclasses.field(compare=False)
    hidden: bool = dataclasses.field(default=False, compare=False)


@dataclasses.dataclass(order=True, slots=True)
class FileDiff:
    """A file that differs between upstream and downstream wheels.

    Ordered and compared by :attr:`filename` alone, so a list of diffs can be
    sorted into path order regardless of sizes, CRCs, severity, or class.

    See :attr:`FileEntry.hidden` for the meaning of *hidden*.
    """

    filename: str
    upstream_size: int = dataclasses.field(compare=False)
    downstream_size: int = dataclasses.field(compare=False)
    upstream_crc32: int = dataclasses.field(compare=False)
    downstream_crc32: int = dataclasses.field(compare=False)
    severity: Severity = dataclasses.field(compare=False)
    classification: Classification = dataclasses.field(compare=False)
    hidden: bool = dataclasses.field(default=False, compare=False)


@dataclasses.dataclass(slots=True)
class RecordMismatch:
    """A mismatch between a wheel's RECORD and its ZIP contents."""

    side: Side
    message: str


@dataclasses.dataclass(slots=True)
class PlatformWarning:
    """A platform or ABI consistency issue found in a wheel."""

    side: Side
    message: str


@dataclasses.dataclass(slots=True)
class VenvBundle:
    """A virtual environment accidentally bundled into a wheel.

    A wheel should never contain a virtual environment.  Files under a
    ``lib/python3.*/site-packages/`` directory indicate a ``.venv`` or
    similar directory was swept into the build.  Bundling one upstream
    is a NOTICE (a pre-existing upstream packaging bug); reproducing it
    downstream is an ERROR.
    """

    side: Side
    severity: Severity
    path: str  # the site-packages directory prefix


@dataclasses.dataclass(slots=True)
class MetadataFieldDiff:
    """A normalized set difference for a multi-value METADATA field.

    Reports the entries of a repeated METADATA field (``Requires-Dist``
    or ``Provides-Extra``) that appear on only one side after
    normalization, so that cosmetic differences (whitespace, ordering,
    name spelling) are ignored.
    """

    field: str  # e.g. "Requires-Dist" or "Provides-Extra"
    only_upstream: tuple[str, ...]
    only_downstream: tuple[str, ...]
    ignored: bool = False  # accepted by policy; reported but not an error


@dataclasses.dataclass(slots=True)
class Analysis:
    """The evolving set of findings produced by the checker pipeline.

    All fields are mutable lists that checkers append to (and mutate the
    entries of) in place.  ``identical`` holds the filenames of files
    present on both sides with matching content.
    """

    only_upstream: list[FileEntry] = dataclasses.field(default_factory=list)
    only_downstream: list[FileEntry] = dataclasses.field(default_factory=list)
    different: list[FileDiff] = dataclasses.field(default_factory=list)
    identical: list[str] = dataclasses.field(default_factory=list)
    record_mismatches: list[RecordMismatch] = dataclasses.field(default_factory=list)
    platform_warnings: list[PlatformWarning] = dataclasses.field(default_factory=list)
    venv_bundles: list[VenvBundle] = dataclasses.field(default_factory=list)
    metadata_field_diffs: list[MetadataFieldDiff] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True, slots=True)
class Comparison:
    """Result of comparing an upstream wheel to a downstream rebuild.

    Wraps the immutable inputs (:attr:`context`, :attr:`upstream`,
    :attr:`downstream`) and the final :attr:`analysis`, and exposes the
    summary facade used by callers and the CLI.
    """

    context: Context
    upstream: WheelInfo
    downstream: WheelInfo
    analysis: Analysis

    @property
    def is_identical(self) -> bool:
        """Return True if the wheels have no added, removed, or differing files.

        Hidden entries (see :attr:`FileEntry.hidden`) still count here: a venv
        bundled on only one side, for example, leaves its files in these lists
        as hidden entries, so the wheels are correctly reported as differing.
        """
        analysis = self.analysis
        return not (analysis.only_upstream or analysis.only_downstream or analysis.different)

    @property
    def has_errors(self) -> bool:
        """Return True if any difference is classified as an error.

        Hidden entries are skipped: a checker that collapses files into a
        summary finding (e.g. :class:`VenvBundle`) carries the error signal on
        that finding, not on the hidden per-file entries.

        RECORD mismatches, platform warnings, and bundled virtual environments
        are always errors by design: a malformed RECORD/WHEEL/METADATA or a
        wheel-embedded venv is a packaging bug regardless of policy (only the
        ``platlib`` "no shared libraries" warning is policy-suppressible).
        """
        analysis = self.analysis
        return (
            any(e.severity is Severity.ERROR for e in analysis.only_upstream if not e.hidden)
            or any(e.severity is Severity.ERROR for e in analysis.only_downstream if not e.hidden)
            or any(d.severity is Severity.ERROR for d in analysis.different if not d.hidden)
            or bool(analysis.record_mismatches)
            or bool(analysis.platform_warnings)
            or any(bundle.severity is Severity.ERROR for bundle in analysis.venv_bundles)
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict representation of the comparison."""
        analysis = self.analysis
        return {
            "upstream": self.upstream.origin.source,
            "downstream": self.downstream.origin.source,
            "upstream_wheel": self.upstream.filename,
            "downstream_wheel": self.downstream.filename,
            "upstream_source": _origin_dict(self.upstream),
            "downstream_source": _origin_dict(self.downstream),
            "is_identical": self.is_identical,
            "has_errors": self.has_errors,
            "only_upstream": [
                {
                    "filename": e.filename,
                    "side": Side.UPSTREAM.value,
                    "severity": e.severity.value,
                    "classification": e.classification.value,
                }
                for e in analysis.only_upstream
                if not e.hidden
            ],
            "only_downstream": [
                {
                    "filename": e.filename,
                    "side": Side.DOWNSTREAM.value,
                    "severity": e.severity.value,
                    "classification": e.classification.value,
                }
                for e in analysis.only_downstream
                if not e.hidden
            ],
            "different": [
                {
                    "filename": d.filename,
                    "upstream_size": d.upstream_size,
                    "downstream_size": d.downstream_size,
                    "upstream_crc32": d.upstream_crc32,
                    "downstream_crc32": d.downstream_crc32,
                    "severity": d.severity.value,
                    "classification": d.classification.value,
                }
                for d in analysis.different
                if not d.hidden
            ],
            "identical": list(analysis.identical),
            "record_mismatches": [
                {"side": w.side, "message": w.message} for w in analysis.record_mismatches
            ],
            "platform_warnings": [
                {"side": w.side, "message": w.message} for w in analysis.platform_warnings
            ],
            "venv_bundles": [
                {"side": b.side, "severity": b.severity.value, "path": b.path}
                for b in analysis.venv_bundles
            ],
            "metadata_field_diffs": [
                {
                    "field": d.field,
                    "only_upstream": list(d.only_upstream),
                    "only_downstream": list(d.only_downstream),
                    "ignored": d.ignored,
                }
                for d in analysis.metadata_field_diffs
            ],
        }

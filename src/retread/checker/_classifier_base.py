"""Base classes for single-rule file classifiers.

:class:`ClassifierChecker` provides the shared claim loop: it walks the
unclaimed pool entries and, for every file its :meth:`~ClassifierChecker.classify`
method matches, claims the file and records the corresponding finding.
:class:`SimpleClassifier` and :class:`PresenceClassifier` are declarative
subclasses for the two common cases, so a concrete classifier only has to
declare its label and a :meth:`matches` predicate.  The concrete classifiers
themselves live in :mod:`retread.checker._classify`.
"""

from __future__ import annotations

import typing

from retread._enums import Classification, Severity
from retread._findings import FileDiff, FileEntry

if typing.TYPE_CHECKING:
    from retread._findings import Comparison
    from retread.checker._engine import Pool


class ClassifierChecker:
    """Base for single-rule classifiers.

    Subclasses implement :meth:`classify`; the shared :meth:`check` walks the
    unclaimed pool entries, and for every file the rule matches, claims it and
    appends the corresponding :class:`~retread._findings.FileEntry` /
    :class:`~retread._findings.FileDiff`.
    """

    name: str = ""
    priority: int = 0

    def classify(
        self, filename: str, dist: str, version: str, *, missing: bool
    ) -> tuple[Severity, Classification] | None:
        """Return the ``(severity, classification)`` for a matching file, else ``None``."""
        raise NotImplementedError

    def check(self, comparison: Comparison, pool: Pool) -> None:
        up = comparison.upstream
        down = comparison.downstream
        analysis = comparison.analysis

        for path in list(pool.only_upstream):
            result = self.classify(str(path), up.dist, up.raw_version, missing=True)
            if result is not None:
                severity, classification = result
                analysis.only_upstream.append(FileEntry(str(path), severity, classification))
                del pool.only_upstream[path]

        for path in list(pool.only_downstream):
            result = self.classify(str(path), down.dist, down.raw_version, missing=True)
            if result is not None:
                severity, classification = result
                analysis.only_downstream.append(FileEntry(str(path), severity, classification))
                del pool.only_downstream[path]

        for path in list(pool.different):
            result = self.classify(str(path), up.dist, up.raw_version, missing=False)
            if result is not None:
                severity, classification = result
                u, d = pool.different[path]
                analysis.different.append(
                    FileDiff(str(path), u.size, d.size, u.crc, d.crc, severity, classification)
                )
                del pool.different[path]


class SimpleClassifier(ClassifierChecker):
    """A classifier that maps every matching file to a fixed label.

    Subclasses set :attr:`classification` (and optionally :attr:`severity`,
    which defaults to NOTICE) and implement :meth:`matches`; presence or
    absence of the file on one side does not change the outcome.
    """

    severity: Severity = Severity.NOTICE
    classification: Classification

    @classmethod
    def matches(cls, filename: str) -> bool:
        """Return True if *filename* is claimed by this classifier."""
        raise NotImplementedError

    def classify(
        self, filename: str, dist: str, version: str, *, missing: bool
    ) -> tuple[Severity, Classification] | None:
        if self.matches(filename):
            return self.severity, self.classification
        return None


class PresenceClassifier(ClassifierChecker):
    """A classifier where a one-sided file is an error but a two-sided one is not.

    A matching file missing from one wheel is an ERROR; a matching file present
    on both sides (and thus only differing) is EXPECTED.  Subclasses set
    :attr:`classification` and implement :meth:`matches`.
    """

    classification: Classification

    @classmethod
    def matches(cls, filename: str) -> bool:
        """Return True if *filename* is claimed by this classifier."""
        raise NotImplementedError

    def classify(
        self, filename: str, dist: str, version: str, *, missing: bool
    ) -> tuple[Severity, Classification] | None:
        if self.matches(filename):
            return (Severity.ERROR if missing else Severity.EXPECTED), self.classification
        return None

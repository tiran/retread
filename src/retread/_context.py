"""The :class:`Context`: how retread judges a comparison.

A :class:`Context` bundles the policy, tuning constants, and the ordered tuple
of checkers.  It is frozen and reusable across many comparisons.
"""

from __future__ import annotations

import dataclasses
import typing

from retread.checker import DEFAULT_CHECKERS

if typing.TYPE_CHECKING:
    from retread._policy import PackagePolicy
    from retread.checker import Checker

# Threshold for the large scripts heuristic.  Files in data/scripts/
# above this size suggest native executables rather than interpreted
# scripts.  64 KiB avoids false positives for large Python scripts
# like pdfminer.six pdf2txt.py (~30 KiB), pyelftools readelf.py
# (~23 KiB), and xlrd runxlrd.py (~14 KiB).
LARGE_SCRIPT_THRESHOLD = 65536


@dataclasses.dataclass(frozen=True, slots=True)
class Context:
    """Reusable configuration for comparing wheels.

    Attributes:
        policy: Mapping of canonical distribution name to policy, or ``None``.
        checkers: Ordered checkers to run (ascending priority runs first).
        large_script_threshold: Byte size above which a ``data/scripts/``
            file is treated as a likely native executable.
    """

    policy: dict[str, PackagePolicy] | None = None
    checkers: tuple[Checker, ...] = ()
    large_script_threshold: int = LARGE_SCRIPT_THRESHOLD

    @classmethod
    def default(
        cls,
        *,
        policy: dict[str, PackagePolicy] | None = None,
        checkers: tuple[Checker, ...] | None = None,
        large_script_threshold: int = LARGE_SCRIPT_THRESHOLD,
    ) -> Context:
        """Build a :class:`Context` with the default checker pipeline.

        Pass *checkers* to override the pipeline; otherwise
        :data:`retread.checker.DEFAULT_CHECKERS` is used.
        """
        return cls(
            policy=policy,
            checkers=DEFAULT_CHECKERS if checkers is None else checkers,
            large_script_threshold=large_script_threshold,
        )

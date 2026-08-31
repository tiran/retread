"""Checker protocol, the claim :class:`Pool`, and the comparison engine.

A :class:`Checker` is a pluggable comparison step.  The engine seeds a
:class:`Pool` from the two wheels' central directories and runs every checker
in ascending ``priority`` order.  Classifier checkers *claim* files out of the
pool (turning them into findings on the :class:`~retread._findings.Analysis`);
structural checkers read the wheels and append or adjust findings.
"""

from __future__ import annotations

import typing

from retread._errors import InvalidWheelError
from retread._findings import Analysis, Comparison

if typing.TYPE_CHECKING:
    from pathlib import PurePosixPath

    from retread._context import Context
    from retread._wheel import FileStat, WheelInfo


@typing.runtime_checkable
class Checker(typing.Protocol):
    """A pluggable comparison step.

    Checkers run in ascending ``priority`` order, ties broken by ``name`` so
    checkers that share a priority run in a stable, deterministic order.
    ``check`` inspects the :class:`Pool`, claims files, and/or appends findings
    to ``comparison.analysis``.
    """

    name: str
    priority: int  # ascending = runs first

    def check(self, comparison: Comparison, pool: Pool) -> None:
        """Inspect the pool, claim files, and/or append findings.

        The context is available as ``comparison.context``.
        """
        ...


class Pool:
    """Mutable working state: the files not yet claimed by a checker.

    ``only_upstream`` / ``only_downstream`` map an in-wheel path to its
    :class:`~retread._wheel.FileStat`; ``different`` maps a path present on
    both sides (with differing content) to the ``(upstream, downstream)``
    stats.  Claiming a file means removing it from its bucket.
    """

    __slots__ = ("different", "only_downstream", "only_upstream")

    def __init__(self) -> None:
        self.only_upstream: dict[PurePosixPath, FileStat] = {}
        self.only_downstream: dict[PurePosixPath, FileStat] = {}
        self.different: dict[PurePosixPath, tuple[FileStat, FileStat]] = {}


def compare(context: Context, upstream: WheelInfo, downstream: WheelInfo) -> Comparison:
    """Compare two loaded wheels and return a :class:`Comparison`.

    Seeds the :class:`Pool` with the name-set diff (splitting files present on
    both sides into ``identical`` vs ``different`` by CRC + size), then runs
    ``context.checkers`` in ascending priority order (ties broken by name).
    The per-file finding lists are sorted by filename to give stable output.
    """
    if upstream.canonical != downstream.canonical:
        raise InvalidWheelError(
            f"dist name mismatch: upstream {upstream.dist!r} != downstream {downstream.dist!r}"
        )

    analysis = Analysis()
    comparison = Comparison(
        context=context, upstream=upstream, downstream=downstream, analysis=analysis
    )

    pool = Pool()
    up_files = upstream.files
    down_files = downstream.files
    up_names = set(up_files)
    down_names = set(down_files)
    for path in up_names - down_names:
        pool.only_upstream[path] = up_files[path]
    for path in down_names - up_names:
        pool.only_downstream[path] = down_files[path]
    for path in up_names & down_names:
        u = up_files[path]
        d = down_files[path]
        if u.crc == d.crc and u.size == d.size:
            analysis.identical.append(str(path))
        else:
            pool.different[path] = (u, d)

    for checker in sorted(context.checkers, key=lambda c: (c.priority, c.name)):
        checker.check(comparison, pool)

    analysis.only_upstream.sort(key=lambda e: e.filename)
    analysis.only_downstream.sort(key=lambda e: e.filename)
    analysis.different.sort(key=lambda d: d.filename)
    analysis.identical.sort()
    return comparison

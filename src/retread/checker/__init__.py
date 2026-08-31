"""Pluggable, priority-ordered comparison checkers.

This is the public extension surface.  Implement the :class:`Checker` protocol
and pass a custom tuple to :meth:`retread.Context.default`::

    from retread import Context
    from retread.checker import DEFAULT_CHECKERS

    context = Context.default(checkers=(*DEFAULT_CHECKERS, MyChecker()))

Checkers run in ascending ``priority`` order.  The claiming classifiers run
first (they remove files from the :class:`Pool` and record findings), followed
by the structural checkers (metadata, record, platform) and finally the policy
checker, which downgrades severities according to :attr:`Context.policy`.

Priority bands
--------------

The built-in priorities leave room for third-party checkers to slot in:

===============  ==================================================
Range            Reserved for
===============  ==================================================
``0``-``199``    built-in claiming classifiers (specific file rules)
``200``-``299``  built-in structural checkers (metadata, record, platform)
``300``-``999``  **third-party checkers**
``1000``         built-in catch-all (claims everything still unclaimed)
``1001``-``1999`` third-party checkers that must run after the catch-all
``2000``         built-in policy checker (always last)
===============  ==================================================

A custom classifier that should claim files before the catch-all belongs in the
``300``-``999`` band; the policy checker at ``2000`` always runs last so it can
downgrade any earlier finding.

Implementation lives in private modules (``checker._classify`` etc.); import
only from this package.
"""

from __future__ import annotations

from retread.checker._classifier_base import (
    ClassifierChecker,
    PresenceClassifier,
    SimpleClassifier,
)
from retread.checker._classify import (
    AntlrChecker,
    AuditwheelChecker,
    BuildConfigChecker,
    CatchAllChecker,
    CSourceChecker,
    DataChecker,
    DistInfoChecker,
    ExtensionPairingChecker,
    JarChecker,
    NamespacePkgPthChecker,
    PrefixPairingChecker,
    ProtobufChecker,
    SharedLibraryChecker,
    StaticLibraryChecker,
    TestChecker,
    VenvChecker,
    VersionFileChecker,
)
from retread.checker._engine import Checker, Pool, compare
from retread.checker._metadata import MetadataChecker
from retread.checker._platform import PlatformChecker
from retread.checker._policy import PolicyChecker
from retread.checker._record import RecordChecker

# The default pipeline, in priority order.  The engine sorts by priority, so
# the tuple order here is cosmetic, but kept aligned for readability.
DEFAULT_CHECKERS: tuple[Checker, ...] = (
    VenvChecker(),
    PrefixPairingChecker(),
    ExtensionPairingChecker(),
    DistInfoChecker(),
    DataChecker(),
    AuditwheelChecker(),
    TestChecker(),
    SharedLibraryChecker(),
    StaticLibraryChecker(),
    JarChecker(),
    VersionFileChecker(),
    NamespacePkgPthChecker(),
    ProtobufChecker(),
    AntlrChecker(),
    CSourceChecker(),
    BuildConfigChecker(),
    CatchAllChecker(),
    MetadataChecker(),
    RecordChecker(),
    PlatformChecker(),
    PolicyChecker(),
)

__all__ = [
    "DEFAULT_CHECKERS",
    "AntlrChecker",
    "AuditwheelChecker",
    "BuildConfigChecker",
    "CSourceChecker",
    "CatchAllChecker",
    "Checker",
    "ClassifierChecker",
    "DataChecker",
    "DistInfoChecker",
    "ExtensionPairingChecker",
    "JarChecker",
    "MetadataChecker",
    "NamespacePkgPthChecker",
    "PlatformChecker",
    "PolicyChecker",
    "Pool",
    "PrefixPairingChecker",
    "PresenceClassifier",
    "ProtobufChecker",
    "RecordChecker",
    "SharedLibraryChecker",
    "SimpleClassifier",
    "StaticLibraryChecker",
    "TestChecker",
    "VenvChecker",
    "VersionFileChecker",
    "compare",
]

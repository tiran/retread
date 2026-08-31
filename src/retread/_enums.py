"""Enumerations shared across retread.

:class:`Severity` ranks how meaningful a difference is; :class:`Classification`
labels what kind of file produced it; :class:`Side` names which wheel of a
comparison a finding belongs to.
"""

from __future__ import annotations

import enum


class Side(enum.StrEnum):
    """Which wheel of a comparison a finding refers to.

    A :class:`str` enum, so members compare and serialize as their plain
    ``"upstream"`` / ``"downstream"`` values.
    """

    UPSTREAM = "upstream"
    DOWNSTREAM = "downstream"


class Severity(enum.Enum):
    """How meaningful a difference is for a rebuild comparison."""

    EXPECTED = "expected"
    NOTICE = "notice"
    ERROR = "error"
    IGNORED = "ignored"


class Classification(enum.Enum):
    """The kind of file a difference was found in."""

    METADATA = "dist-info METADATA"
    RECORD = "dist-info RECORD"
    WHEEL = "dist-info WHEEL"
    SBOM = "sbom"
    LICENSE = "license"
    DIST_INFO = "dist-info"
    AUDITWHEEL = "auditwheel"
    DATA = "data"
    DATA_SCRIPTS = "data scripts"
    EXTENSION_MODULE = "extension module"
    STATIC_LIBRARY = "static library"
    JAR = "jar"
    VERSION_FILE = "version file"
    NAMESPACE_PKG_PTH = "namespace pkg pth"
    GENERATED_PROTOBUF = "generated protobuf"
    GENERATED_ANTLR = "generated antlr"
    GENERATED_C = "generated c/c++"  # probably Cython-generated or vendored
    GENERATED_BUILD_CONFIG = "generated build config"
    TEST = "test"
    OTHER = "other"

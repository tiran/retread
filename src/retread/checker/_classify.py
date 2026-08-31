"""File-classification checkers.

These reproduce the legacy first-match-wins cascade as independent,
priority-ordered checkers.  :class:`VenvChecker` and the two pairing checkers
run first and have cross-side logic; the remaining classifiers each own a
single rule and share the claim loop from
:class:`~retread.checker._classifier_base.ClassifierChecker` (via its
:class:`~retread.checker._classifier_base.SimpleClassifier` /
:class:`~retread.checker._classifier_base.PresenceClassifier` bases).
:class:`CatchAllChecker` claims whatever is left.

Every classifier owns its discriminating constant as a class attribute and its
match logic as a :meth:`matches` classmethod, so the rule and its data live in
one place.  :func:`classify_label` delegates to the classifiers in priority
order, giving the pairing checkers labels for the files they claim without a
second copy of the cascade.
"""

from __future__ import annotations

import fnmatch
import logging
import re
import typing

from retread._enums import Classification, Severity, Side
from retread._findings import FileDiff, FileEntry, VenvBundle
from retread.checker._classifier_base import (
    ClassifierChecker,
    PresenceClassifier,
    SimpleClassifier,
)

if typing.TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import PurePosixPath

    from retread._findings import Comparison
    from retread._wheel import FileStat
    from retread.checker._engine import Pool

logger = logging.getLogger(__name__)


class VenvChecker:
    """Collapse accidentally-bundled virtual environments into one finding each.

    Files under a ``lib/python3.*/site-packages/`` directory indicate a virtual
    environment swept into the wheel.  Bundling one upstream is a NOTICE (a
    pre-existing upstream packaging bug); reproducing it downstream is an ERROR.

    The offending files are claimed out of the pool as *hidden* findings: they
    stay in the diff lists so :attr:`Comparison.is_identical` still sees a
    one-sided or differing venv as a real difference, but are omitted from every
    report (the :class:`~retread._findings.VenvBundle` summary stands in for
    them) so they do not flood the per-file diff.  Identical-content venv files
    are dropped from the identical list for the same reason.
    """

    name = "venv"
    priority = 10

    # A ``lib/python3.*/site-packages/`` path component sequence is the hallmark
    # of a virtual environment that was accidentally swept into the wheel (e.g. a
    # ``.venv/`` directory picked up by the build).
    SITE_PACKAGES_RE: re.Pattern[str] = re.compile(r"(?:^|/)lib/python3\.[^/]*/site-packages/")

    @classmethod
    def is_bundled_venv_file(cls, filename: str) -> bool:
        """Return True if *filename* lives inside a bundled virtual environment."""
        return cls.SITE_PACKAGES_RE.search(filename) is not None

    @classmethod
    def find_bundled_venvs(cls, filenames: Iterable[str]) -> list[str]:
        """Find bundled virtual environments among *filenames*.

        Scans an iterable of file names for files under a
        ``lib/python3.*/site-packages/`` directory, which indicates a virtual
        environment was packaged into the wheel by mistake.

        Returns a sorted list of the distinct ``.../site-packages/`` directory
        prefixes found (one per bundled environment).
        """
        prefixes: set[str] = set()
        for fname in filenames:
            m = cls.SITE_PACKAGES_RE.search(fname)
            if m is not None:
                prefixes.add(fname[: m.end()])
        return sorted(prefixes)

    def check(self, comparison: Comparison, pool: Pool) -> None:
        up = comparison.upstream
        down = comparison.downstream
        analysis = comparison.analysis

        for wheel, side, severity in (
            (up, Side.UPSTREAM, Severity.NOTICE),
            (down, Side.DOWNSTREAM, Severity.ERROR),
        ):
            for prefix in self.find_bundled_venvs(wheel.names):
                logger.info("bundled venv [%s]: %s", side, prefix)
                analysis.venv_bundles.append(VenvBundle(side=side, severity=severity, path=prefix))

        # Claim the venv files as hidden findings.  Their severity and
        # classification are never reported (the VenvBundle summary is), so a
        # single neutral label suffices; only their presence in these lists
        # matters, keeping is_identical correct.
        for path in [p for p in pool.only_upstream if self.is_bundled_venv_file(str(p))]:
            analysis.only_upstream.append(
                FileEntry(str(path), Severity.NOTICE, Classification.OTHER, hidden=True)
            )
            del pool.only_upstream[path]
        for path in [p for p in pool.only_downstream if self.is_bundled_venv_file(str(p))]:
            analysis.only_downstream.append(
                FileEntry(str(path), Severity.NOTICE, Classification.OTHER, hidden=True)
            )
            del pool.only_downstream[path]
        for path in [p for p in pool.different if self.is_bundled_venv_file(str(p))]:
            u, d = pool.different[path]
            analysis.different.append(
                FileDiff(
                    str(path),
                    u.size,
                    d.size,
                    u.crc,
                    d.crc,
                    Severity.NOTICE,
                    Classification.OTHER,
                    hidden=True,
                )
            )
            del pool.different[path]

        analysis.identical[:] = [f for f in analysis.identical if not self.is_bundled_venv_file(f)]


class PrefixPairingChecker:
    """Pair dist-info / ``.data`` files that differ only in a directory prefix.

    When upstream and downstream use different prefixes for the same logical
    directory (``ImageHash-4.3.2.data/`` vs ``imagehash-4.3.2.data/``, or
    ``foo-1.0.dist-info/`` vs ``foo-1.0+local.dist-info/``), files sharing the
    same path after the prefix and having identical content are claimed as
    EXPECTED.  Prefix pairs with differing content are left for the classifiers.
    """

    name = "prefix-pairing"
    priority = 20

    def check(self, comparison: Comparison, pool: Pool) -> None:
        up = comparison.upstream
        down = comparison.downstream
        analysis = comparison.analysis

        up_di = f"{up.dist}-{up.raw_version}.dist-info/"
        down_di = f"{down.dist}-{down.raw_version}.dist-info/"
        up_data = f"{up.dist}-{up.raw_version}.data/"
        down_data = f"{down.dist}-{down.raw_version}.data/"

        pairs = self._pairs(pool, up_di, down_di) + self._pairs(pool, up_data, down_data)
        for up_path, down_path in pairs:
            _, up_cls = classify_label(
                str(up_path), dist=up.dist, version=up.raw_version, missing=True
            )
            analysis.only_upstream.append(FileEntry(str(up_path), Severity.EXPECTED, up_cls))
            del pool.only_upstream[up_path]

            _, down_cls = classify_label(
                str(down_path), dist=down.dist, version=down.raw_version, missing=True
            )
            analysis.only_downstream.append(FileEntry(str(down_path), Severity.EXPECTED, down_cls))
            del pool.only_downstream[down_path]

    @staticmethod
    def _pairs(
        pool: Pool, up_prefix: str, down_prefix: str
    ) -> list[tuple[PurePosixPath, PurePosixPath]]:
        """Return ``[(up_path, down_path), ...]`` for identical-content prefix pairs."""
        if up_prefix == down_prefix:
            return []
        up_by_rest: dict[str, tuple[PurePosixPath, FileStat]] = {}
        for path, stat in pool.only_upstream.items():
            s = str(path)
            if s.startswith(up_prefix):
                up_by_rest[s[len(up_prefix) :]] = (path, stat)
        pairs: list[tuple[PurePosixPath, PurePosixPath]] = []
        for path, stat in pool.only_downstream.items():
            s = str(path)
            if not s.startswith(down_prefix):
                continue
            match = up_by_rest.get(s[len(down_prefix) :])
            if match is None:
                continue
            up_path, up_stat = match
            if up_stat.crc == stat.crc and up_stat.size == stat.size:
                pairs.append((up_path, path))
        return pairs


class ExtensionPairingChecker:
    """Pair extension modules that differ only in ABI suffix across sides.

    When upstream ships ``foo.abi3.so`` and downstream ships
    ``foo.cpython-312-x86_64-linux-gnu.so``, they are the same module linked
    against different ABIs.  Paired modules that would otherwise be an ERROR
    (a missing shared library) are downgraded to NOTICE.
    """

    name = "extension-pairing"
    priority = 30

    EXT_SUFFIX_RE: re.Pattern[str] = re.compile(r"(\.cpython-[^/]+\.so|\.abi3t?\.so|\.so)$")

    @classmethod
    def extension_stem(cls, filename: str) -> str | None:
        """Extract the module stem from an extension module filename.

        Returns the path without the ABI-specific suffix, or ``None`` if the
        filename is not a recognized extension module.

        Examples::

            foo/_bar.cpython-312-x86_64-linux-gnu.so  ->  foo/_bar
            foo/_bar.abi3.so                           ->  foo/_bar
            foo/_bar.abi3t.so                          ->  foo/_bar
            foo/_bar.so                                ->  foo/_bar
            foo/bar.py                                 ->  None
        """
        m = cls.EXT_SUFFIX_RE.search(filename)
        if m:
            return filename[: m.start()]
        return None

    def check(self, comparison: Comparison, pool: Pool) -> None:
        up = comparison.upstream
        down = comparison.downstream
        analysis = comparison.analysis

        up_stems: dict[str, PurePosixPath] = {}
        for path in pool.only_upstream:
            stem = self.extension_stem(str(path))
            if stem is not None:
                up_stems[stem] = path

        up_claim: set[PurePosixPath] = set()
        down_claim: set[PurePosixPath] = set()
        for path in pool.only_downstream:
            stem = self.extension_stem(str(path))
            if stem is not None and stem in up_stems:
                up_claim.add(up_stems[stem])
                down_claim.add(path)

        for path in up_claim:
            severity, classification = classify_label(
                str(path), dist=up.dist, version=up.raw_version, missing=True
            )
            if severity is Severity.ERROR:
                severity = Severity.NOTICE
            analysis.only_upstream.append(FileEntry(str(path), severity, classification))
            del pool.only_upstream[path]

        for path in down_claim:
            severity, classification = classify_label(
                str(path), dist=down.dist, version=down.raw_version, missing=True
            )
            if severity is Severity.ERROR:
                severity = Severity.NOTICE
            analysis.only_downstream.append(FileEntry(str(path), severity, classification))
            del pool.only_downstream[path]


class DistInfoChecker(ClassifierChecker):
    """Classify files under the ``{name}-{version}.dist-info/`` directory."""

    name = "dist-info"
    priority = 40

    def classify(
        self, filename: str, dist: str, version: str, *, missing: bool
    ) -> tuple[Severity, Classification] | None:
        prefix = f"{dist}-{version}.dist-info/"
        if not filename.startswith(prefix):
            return None
        rest = filename[len(prefix) :]
        if rest == "RECORD":
            return Severity.EXPECTED, Classification.RECORD
        if rest == "WHEEL":
            return Severity.EXPECTED, Classification.WHEEL
        if rest == "METADATA":
            return Severity.NOTICE, Classification.METADATA
        if rest.startswith("sboms/"):
            return Severity.NOTICE, Classification.SBOM
        if rest.startswith("licenses/"):
            return Severity.NOTICE, Classification.LICENSE
        return Severity.NOTICE, Classification.DIST_INFO


class DataChecker(ClassifierChecker):
    """Classify files under the ``{name}-{version}.data/`` directory."""

    name = "data"
    priority = 50

    def classify(
        self, filename: str, dist: str, version: str, *, missing: bool
    ) -> tuple[Severity, Classification] | None:
        prefix = f"{dist}-{version}.data/"
        if not filename.startswith(prefix):
            return None
        rest = filename[len(prefix) :]
        if rest.startswith("scripts/"):
            severity = Severity.ERROR if missing else Severity.EXPECTED
            return severity, Classification.DATA_SCRIPTS
        severity = Severity.ERROR if missing else Severity.NOTICE
        return severity, Classification.DATA


class AuditwheelChecker(SimpleClassifier):
    """Classify auditwheel-vendored shared libraries in a ``*.libs/`` directory.

    auditwheel vendors external shared libraries as ``lib*.so*`` files directly
    inside a root-level ``{dist}.libs/`` directory
    (e.g. ``torchvision.libs/libcudart.faf08d9a.so.13``).  These files are
    expected to appear or disappear between upstream and downstream builds.

    The match is deliberately strict: only ``lib*.so*`` files sitting directly
    in a top-level ``*.libs/`` directory are claimed.  Other files that happen
    to live in such a directory, or ``.libs`` directories nested under
    subdirectories, fall through to the remaining classifiers.
    """

    name = "auditwheel"
    priority = 60
    classification = Classification.AUDITWHEEL

    LIBS_SUFFIX: str = ".libs"
    LIB_GLOB: str = "lib*.so*"

    @classmethod
    def matches(cls, filename: str) -> bool:
        """Return True for a ``lib*.so*`` file in a root-level ``*.libs/`` dir."""
        parent, sep, base = filename.rpartition("/")
        if not sep or "/" in parent:
            return False
        return parent.endswith(cls.LIBS_SUFFIX) and fnmatch.fnmatchcase(base, cls.LIB_GLOB)


class TestChecker(SimpleClassifier):
    """Classify files inside ``test/`` or ``tests/`` directories."""

    name = "test"
    priority = 70
    classification = Classification.TEST

    TEST_DIRS: tuple[str, ...] = ("test", "tests")

    @classmethod
    def matches(cls, filename: str) -> bool:
        """Return True if *filename* is inside a test directory (any depth)."""
        return any(part in cls.TEST_DIRS for part in filename.split("/"))


class SharedLibraryChecker(PresenceClassifier):
    """Classify shared libraries / extension modules (``.so``).

    Matches both unversioned (``foo.so``) and versioned (``libfoo.so.1.2.3``)
    shared-object names.
    """

    name = "shared-library"
    priority = 80
    classification = Classification.EXTENSION_MODULE

    PATTERN: re.Pattern[str] = re.compile(r"\.so(\.[0-9.]+)?$")

    @classmethod
    def matches(cls, filename: str) -> bool:
        """Return True if *filename* looks like a shared library."""
        return cls.PATTERN.search(filename) is not None


class StaticLibraryChecker(PresenceClassifier):
    """Classify static archive libraries (``lib*.a``)."""

    name = "static-library"
    priority = 90
    classification = Classification.STATIC_LIBRARY

    PATTERN: re.Pattern[str] = re.compile(r"(?:^|/)lib[^/]+\.a$")

    @classmethod
    def matches(cls, filename: str) -> bool:
        """Return True if *filename* looks like a static archive (``lib*.a``)."""
        return cls.PATTERN.search(filename) is not None


class JarChecker(PresenceClassifier):
    """Classify Java archives (``.jar``)."""

    name = "jar"
    priority = 100
    classification = Classification.JAR

    SUFFIX: str = ".jar"

    @classmethod
    def matches(cls, filename: str) -> bool:
        """Return True if *filename* is a Java archive."""
        return filename.endswith(cls.SUFFIX)


class VersionFileChecker(SimpleClassifier):
    """Classify auto-generated version files (``_version.py`` etc.)."""

    name = "version-file"
    priority = 110
    classification = Classification.VERSION_FILE

    BASENAMES: frozenset[str] = frozenset(
        {
            "_version.py",
            "__version__.py",
            "version.py",
            "__config__.py",
        }
    )

    @classmethod
    def matches(cls, filename: str) -> bool:
        """Return True if *filename* is an auto-generated version file."""
        return filename.rsplit("/", 1)[-1] in cls.BASENAMES


class NamespacePkgPthChecker(SimpleClassifier):
    """Classify legacy namespace-package ``*-nspkg.pth`` files.

    Superseded by PEP 420 implicit namespace packages.  The filename embeds the
    build-time Python version, so it always differs between upstream and rebuild.
    """

    name = "namespace-pkg-pth"
    priority = 120
    severity = Severity.EXPECTED
    classification = Classification.NAMESPACE_PKG_PTH

    SUFFIX: str = "-nspkg.pth"

    @classmethod
    def matches(cls, filename: str) -> bool:
        """Return True if *filename* is a legacy namespace-package ``.pth`` file."""
        return filename.endswith(cls.SUFFIX)


class ProtobufChecker(SimpleClassifier):
    """Classify protobuf-generated files (``*_pb2.py`` etc.).

    Matches ``*_pb2.py``, ``*_pb2_grpc.py`` and their ``.pyi`` stubs, which
    differ when rebuilt with a different generator version.
    """

    name = "protobuf"
    priority = 130
    classification = Classification.GENERATED_PROTOBUF

    SUFFIXES: tuple[str, ...] = ("_pb2.py", "_pb2_grpc.py", "_pb2.pyi", "_pb2_grpc.pyi")

    @classmethod
    def matches(cls, filename: str) -> bool:
        """Return True if *filename* looks like a protobuf-generated file."""
        return filename.endswith(cls.SUFFIXES)


class AntlrChecker(SimpleClassifier):
    """Classify ANTLR-generated files under ``grammar/gen/``.

    Matches ``*Lexer.py``, ``*Parser.py``, ``*ParserListener.py``,
    ``*ParserVisitor.py`` inside a ``grammar/gen/`` directory.
    """

    name = "antlr"
    priority = 140
    classification = Classification.GENERATED_ANTLR

    # Matched as a path component (leading slash) so a directory that merely
    # ends in ``grammar`` (e.g. ``mygrammar/gen/``) is not a false positive.
    GEN_DIR: str = "/grammar/gen/"
    SUFFIXES: tuple[str, ...] = ("Lexer.py", "Parser.py", "ParserListener.py", "ParserVisitor.py")

    @classmethod
    def matches(cls, filename: str) -> bool:
        """Return True if *filename* looks like an ANTLR-generated file."""
        if cls.GEN_DIR not in filename:
            return False
        return filename.endswith(cls.SUFFIXES)


class CSourceChecker(SimpleClassifier):
    """Classify C/C++ source and header files.

    These are almost always Cython-generated or vendored sources that differ
    between generator versions.  If this heuristic produces false positives in
    the future, a more precise approach would be to read the file header and
    look for the ``/* Generated by Cython */`` comment.
    """

    name = "c-source"
    priority = 150
    classification = Classification.GENERATED_C

    # C/C++ source and header file suffixes:
    #   .c, .cpp     - C/C++ source (often Cython-generated)
    #   .h, .hpp     - C/C++ headers
    #   .inc         - textual includes (MLIR TableGen, protobuf)
    #   .inl         - C++ inline implementation headers
    #   .cuh         - CUDA C++ headers
    SUFFIXES: tuple[str, ...] = (".c", ".cpp", ".cuh", ".h", ".hpp", ".inc", ".inl")

    @classmethod
    def matches(cls, filename: str) -> bool:
        """Return True if *filename* is a C/C++ source or header file."""
        return filename.endswith(cls.SUFFIXES)


class BuildConfigChecker(SimpleClassifier):
    """Classify build configuration files (``.cmake``, ``.pc``).

    CMake configs (``.cmake``) and pkg-config files (``.pc``) are auto-generated
    during the build and embed build-specific paths that differ between upstream
    and downstream.
    """

    name = "build-config"
    priority = 160
    classification = Classification.GENERATED_BUILD_CONFIG

    SUFFIXES: tuple[str, ...] = (".cmake", ".pc")

    @classmethod
    def matches(cls, filename: str) -> bool:
        """Return True if *filename* is a build configuration file."""
        return filename.endswith(cls.SUFFIXES)


class CatchAllChecker(ClassifierChecker):
    """Claim everything left over as an unexpected difference (ERROR / OTHER)."""

    name = "catch-all"
    # Runs after every specific rule and the reserved third-party band, but
    # before PolicyChecker (2000), so its ERROR/OTHER findings can still be
    # downgraded by policy.  See the priority bands in ``retread.checker``.
    priority = 1_000

    def classify(
        self, filename: str, dist: str, version: str, *, missing: bool
    ) -> tuple[Severity, Classification] | None:
        return Severity.ERROR, Classification.OTHER


# The classifier cascade, in priority order.  ``classify_label`` walks these so
# the pairing checkers reuse the exact same rules instead of a second copy.
_LABEL_CLASSIFIERS: tuple[ClassifierChecker, ...] = (
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
)


def classify_label(
    filename: str, *, dist: str, version: str, missing: bool = False
) -> tuple[Severity, Classification]:
    """Classify a file difference with a severity and classification label.

    Delegates to the classifier checkers in priority order and returns the first
    match.  This is the single source of truth for the first-match-wins cascade,
    used by the pairing checkers to label the files they claim.

    When *missing* is True the file is only present on one side of the
    comparison (this influences DATA / shared-library / static-library
    severities).  *dist* and *version* are the distribution name and version
    extracted verbatim from the wheel filename (not normalised).
    """
    for classifier in _LABEL_CLASSIFIERS:
        result = classifier.classify(filename, dist, version, missing=missing)
        if result is not None:
            return result
    # CatchAllChecker always matches, so this is unreachable; kept as a total
    # fallback for type-checkers and defensive robustness.
    return Severity.ERROR, Classification.OTHER

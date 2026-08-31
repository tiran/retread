"""RECORD validation checker.

Cross-validates each wheel's ``RECORD`` CSV manifest against its ZIP central
directory to detect missing files, extra files, and size mismatches.
"""

from __future__ import annotations

import csv
import logging
import typing

from retread._enums import Side
from retread._findings import RecordMismatch

if typing.TYPE_CHECKING:
    from retread._findings import Comparison
    from retread._wheel import WheelInfo
    from retread.checker._engine import Pool

logger = logging.getLogger(__name__)


def _parse_record(
    record_bytes: bytes, record_path: str
) -> tuple[dict[str, int | None], list[str]]:
    """Parse a RECORD CSV, validating structure.

    Returns ``(entries, errors)`` where *entries* maps filenames to
    sizes (``None`` for RECORD/signature entries) and *errors* lists
    structural problems found during parsing.

    Each row must have exactly three fields.  Only the RECORD file
    itself and its deprecated signatures (``RECORD.p7s``,
    ``RECORD.jws``) may have empty hash and size columns.  For all
    other entries the hash must be ``algorithm=digest`` and the size
    must be an integer.
    """
    entries: dict[str, int | None] = {}
    errors: list[str] = []
    dist_info_prefix = record_path.removesuffix("RECORD")
    self_paths = frozenset(
        {
            record_path,
            f"{dist_info_prefix}RECORD.p7s",
            f"{dist_info_prefix}RECORD.jws",
        }
    )

    reader = csv.reader(record_bytes.decode("utf-8").splitlines())
    for lineno, row in enumerate(reader, 1):
        if not any(row):
            continue  # skip blank lines
        if len(row) != 3:
            errors.append(f"row {lineno}: expected 3 fields, got {len(row)}")
            continue
        filename, hash_col, size_col = row[0], row[1].strip(), row[2].strip()
        if not filename:
            errors.append(f"row {lineno}: empty filename")
            continue

        # RECORD itself and deprecated signatures may have empty hash+size
        if filename in self_paths:
            entries[filename] = None
            continue

        # Validate hash (expected format: "algorithm=digest")
        if not hash_col:
            errors.append(f"row {lineno}: missing hash for {filename}")
        else:
            algo, _, digest = hash_col.partition("=")
            if not algo or not digest:
                errors.append(f"row {lineno}: invalid hash format for {filename}: {hash_col!r}")

        # Validate size
        if not size_col:
            errors.append(f"row {lineno}: missing size for {filename}")
            entries[filename] = None
        else:
            try:
                entries[filename] = int(size_col)
            except ValueError:
                errors.append(f"row {lineno}: invalid size {size_col!r} for {filename}")
                entries[filename] = None

    return entries, errors


def _check_record(
    side: Side,
    record_files: dict[str, int | None],
    zip_sizes: dict[str, int],
    record_path: str,
) -> list[RecordMismatch]:
    """Compare one wheel's parsed RECORD against its ZIP central directory."""
    mismatches: list[RecordMismatch] = []
    zip_names = set(zip_sizes)
    record_names = set(record_files)

    # Files in ZIP but not in RECORD (excluding RECORD itself)
    for fname in sorted(zip_names - record_names):
        if fname == record_path:
            continue
        mismatches.append(RecordMismatch(side, f"file in ZIP but not in RECORD: {fname}"))

    # Files in RECORD but not in ZIP
    for fname in sorted(record_names - zip_names):
        if fname == record_path:
            continue
        mismatches.append(RecordMismatch(side, f"file in RECORD but not in ZIP: {fname}"))

    # Size mismatches for files in both
    for fname in sorted(zip_names & record_names):
        record_size = record_files.get(fname)
        if record_size is None:
            continue
        zip_size = zip_sizes[fname]
        if record_size != zip_size:
            mismatches.append(
                RecordMismatch(
                    side,
                    f"size mismatch for {fname}: RECORD says {record_size}, ZIP says {zip_size}",
                )
            )

    return mismatches


class RecordChecker:
    """Cross-validate RECORD files against ZIP central directory contents."""

    name = "record"
    priority = 210

    def check(self, comparison: Comparison, pool: Pool) -> None:
        mismatches: list[RecordMismatch] = []
        for side, wheel in (
            (Side.UPSTREAM, comparison.upstream),
            (Side.DOWNSTREAM, comparison.downstream),
        ):
            mismatches.extend(self._check_side(side, wheel))
        if mismatches:
            comparison.analysis.record_mismatches.extend(mismatches)

    @staticmethod
    def _check_side(side: Side, wheel: WheelInfo) -> list[RecordMismatch]:
        record_path = f"{wheel.dist_info}/RECORD"
        record_bytes = wheel.di_record
        if record_bytes is None:
            msg = f"missing RECORD: {record_path}"
            logger.info("RECORD check [%s]: %s", side, msg)
            return [RecordMismatch(side, msg)]

        try:
            record_files, parse_errors = _parse_record(record_bytes, record_path)
        except Exception:
            msg = f"malformed RECORD: {record_path}"
            logger.info("RECORD check [%s]: %s", side, msg)
            return [RecordMismatch(side, msg)]

        mismatches: list[RecordMismatch] = []
        for err in parse_errors:
            logger.info("RECORD check [%s]: %s", side, err)
            mismatches.append(RecordMismatch(side, err))
        if not record_files and not parse_errors:
            msg = f"empty RECORD: {record_path}"
            logger.info("RECORD check [%s]: %s", side, msg)
            return [RecordMismatch(side, msg)]

        zip_sizes = {str(path): stat.size for path, stat in wheel.files.items()}
        side_mismatches = _check_record(side, record_files, zip_sizes, record_path)
        for m in side_mismatches:
            logger.info("RECORD check [%s]: %s", side, m.message)
        mismatches.extend(side_mismatches)
        return mismatches

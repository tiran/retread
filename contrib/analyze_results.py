#!/usr/bin/env python3
"""Analyze retread comparison results and report common error patterns.

Reads JSON result files produced by ``test_rhoai_index.py`` and groups
errors into high-level categories.  Useful for identifying systemic
issues in downstream rebuilds.

Usage::

    .venv/bin/python contrib/analyze_results.py
    .venv/bin/python contrib/analyze_results.py data/3.6-EA1/cpu-ubi9-test
"""

import argparse
import collections
import json
import pathlib
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_DATA_DIR = SCRIPT_DIR.parent / "data" / "3.6-EA1" / "cpu-ubi9-test"


def _load_results(
    data_dir: pathlib.Path,
) -> tuple[list[tuple[str, dict]], list[tuple[str, dict]], list[tuple[str, dict]]]:
    """Load and partition results into ok, error, and failure lists."""
    ok: list[tuple[str, dict]] = []
    errors: list[tuple[str, dict]] = []
    failures: list[tuple[str, dict]] = []

    for f in sorted(data_dir.glob("*.json")):
        data = json.loads(f.read_text())
        stem = f.stem
        if "error" in data and "has_errors" not in data:
            failures.append((stem, data))
        elif data.get("has_errors"):
            errors.append((stem, data))
        else:
            ok.append((stem, data))

    return ok, errors, failures


def _classify_error_sources(data: dict) -> dict:
    """Extract error source counts from a single result."""
    counts: dict[str, int] = collections.Counter()

    for side in ("only_upstream", "only_downstream"):
        for entry in data.get(side, []):
            if entry["severity"] == "error":
                counts[f"{side}:{entry['classification']}"] += 1

    for diff in data.get("different", []):
        if diff["severity"] == "error":
            counts[f"different:{diff['classification']}"] += 1

    counts["record_mismatches"] = len(data.get("record_mismatches", []))
    counts["platform_warnings"] = len(data.get("platform_warnings", []))
    counts["resolution_mismatches"] = sum(
        1 for m in data.get("resolution_mismatches", []) if not m.get("ignored")
    )

    return counts


def _categorize_package(counts: dict[str, int]) -> str:
    """Assign a high-level category to a package based on its error sources."""
    up_other = counts.get("only_upstream:other", 0)
    down_other = counts.get("only_downstream:other", 0)
    diff_other = counts.get("different:other", 0)
    diff_meta = counts.get("different:dist-info METADATA", 0)
    up_ext = counts.get("only_upstream:extension module", 0)
    down_ext = counts.get("only_downstream:extension module", 0)
    up_data = counts.get("only_upstream:data", 0)
    down_data = counts.get("only_downstream:data", 0)
    up_scripts = counts.get("only_upstream:data scripts", 0)
    down_scripts = counts.get("only_downstream:data scripts", 0)
    record = counts.get("record_mismatches", 0)
    platform = counts.get("platform_warnings", 0)
    resolution = counts.get("resolution_mismatches", 0)

    file_errors = up_other + down_other + diff_other
    ext_errors = up_ext + down_ext
    data_errors = up_data + down_data + up_scripts + down_scripts
    total_file = file_errors + ext_errors + data_errors

    # Pure categories (single error source)
    if total_file == 0 and diff_meta == 0 and record == 0 and platform == 0 and resolution > 0:
        return "upstream resolution mismatch"
    if total_file == 0 and diff_meta == 0 and record == 0 and resolution == 0 and platform > 0:
        return "platform/ABI warnings only"
    if total_file == 0 and diff_meta > 0 and record == 0 and platform == 0 and resolution == 0:
        return "METADATA core-field diff"
    if (
        file_errors > 0
        and diff_meta == 0
        and ext_errors == 0
        and record == 0
        and platform == 0
        and resolution == 0
    ):
        if up_other > 0 and down_other == 0 and diff_other == 0:
            return "upstream files stripped"
        if down_other > 0 and up_other == 0 and diff_other == 0:
            return "downstream files added"
        if diff_other > 0 and up_other == 0 and down_other == 0:
            return "file content differs"
        return "file set differs"
    if (
        ext_errors > 0
        and file_errors == 0
        and diff_meta == 0
        and record == 0
        and platform == 0
        and resolution == 0
    ):
        return "extension module mismatch"

    # Mixed
    return "mixed"


_MAX_ERROR_FILES = 10


def _error_file_lines(data: dict) -> list[str]:
    """Return human-readable lines for error-severity issues only."""
    lines: list[str] = []
    for entry in data.get("only_upstream", []):
        if entry["severity"] == "error":
            lines.append(f"upstream only: {entry['filename']} [{entry['classification']}]")
    for entry in data.get("only_downstream", []):
        if entry["severity"] == "error":
            lines.append(f"downstream only: {entry['filename']} [{entry['classification']}]")
    for diff in data.get("different", []):
        if diff["severity"] == "error":
            lines.append(
                f"different: {diff['filename']}"
                f" ({diff['upstream_size']} -> {diff['downstream_size']} bytes)"
                f" [{diff['classification']}]"
            )
    for w in data.get("record_mismatches", []):
        lines.append(f"record ({w['side']}): {w['message']}")
    for w in data.get("platform_warnings", []):
        lines.append(f"platform ({w['side']}): {w['message']}")
    for m in data.get("resolution_mismatches", []):
        if not m.get("ignored"):
            lines.append(f"resolution mismatch: {m['message']}")
    return lines


def _collect_file_extensions(
    errors: list[tuple[str, dict]],
) -> collections.Counter:
    """Count file extensions among error-severity 'other' files."""
    ext_counts: collections.Counter = collections.Counter()
    for _, data in errors:
        for side in ("only_upstream", "only_downstream"):
            for entry in data.get(side, []):
                if entry["severity"] != "error" or entry["classification"] != "other":
                    continue
                fname = entry["filename"]
                dot = fname.rfind(".")
                slash = fname.rfind("/")
                if dot > slash:
                    ext_counts["." + fname[dot + 1 :]] += 1
                else:
                    ext_counts["(no extension)"] += 1
        for diff in data.get("different", []):
            if diff["severity"] != "error" or diff["classification"] != "other":
                continue
            fname = diff["filename"]
            dot = fname.rfind(".")
            slash = fname.rfind("/")
            if dot > slash:
                ext_counts["." + fname[dot + 1 :]] += 1
            else:
                ext_counts["(no extension)"] += 1
    return ext_counts


def _collect_test_dir_files(errors: list[tuple[str, dict]]) -> int:
    """Count error-severity files inside test/ or tests/ directories."""
    count = 0
    for _, data in errors:
        for side in ("only_upstream", "only_downstream"):
            for entry in data.get(side, []):
                if entry["severity"] != "error":
                    continue
                parts = entry["filename"].split("/")
                if any(p in ("test", "tests") for p in parts):
                    count += 1
    return count


def _collect_platform_warnings(
    errors: list[tuple[str, dict]],
) -> collections.Counter:
    """Count platform warning messages."""
    counts: collections.Counter = collections.Counter()
    for _, data in errors:
        for w in data.get("platform_warnings", []):
            # Truncate message to first sentence for grouping
            msg = w["message"]
            colon = msg.find(":")
            if colon > 0:
                msg = msg[:colon]
            counts[msg] += 1
    return counts


def _run(data_dir: pathlib.Path) -> None:
    ok, errors, failures = _load_results(data_dir)
    total = len(ok) + len(errors) + len(failures)

    print(f"Results directory: {data_dir}")
    print(f"Total results: {total}")
    print(f"  OK:       {len(ok)}")
    print(f"  Errors:   {len(errors)}")
    print(f"  Failures: {len(failures)}")

    if not errors:
        return

    # Categorize each error package
    categories: dict[str, list[tuple[str, dict]]] = collections.defaultdict(list)
    for stem, data in errors:
        counts = _classify_error_sources(data)
        category = _categorize_package(counts)
        categories[category].append((stem, data))

    print(f"\n{'='*60}")
    print("Error categories")
    print(f"{'='*60}\n")
    for category in sorted(categories, key=lambda c: -len(categories[c])):
        entries = sorted(categories[category], key=lambda e: e[0])
        print(f"{category} ({len(entries)}):")
        for stem, data in entries:
            lines = _error_file_lines(data)
            total = len(lines)
            print(f"  {stem}:")
            for line in lines[:_MAX_ERROR_FILES]:
                print(f"    {line}")
            if total > _MAX_ERROR_FILES:
                print(f"    ... and {total - _MAX_ERROR_FILES} more")
        print()

    # File extension breakdown
    ext_counts = _collect_file_extensions(errors)
    if ext_counts:
        print(f"{'='*60}")
        print("File extensions in 'other' errors (top 15)")
        print(f"{'='*60}\n")
        for ext, count in ext_counts.most_common(15):
            print(f"  {count:7d}  {ext}")
        print()

    # Test directory files
    test_count = _collect_test_dir_files(errors)
    if test_count:
        print(f"Files inside test/tests directories: {test_count}\n")

    # Platform warnings
    pw_counts = _collect_platform_warnings(errors)
    if pw_counts:
        print(f"{'='*60}")
        print("Platform/ABI warnings")
        print(f"{'='*60}\n")
        for msg, count in pw_counts.most_common():
            print(f"  {count:4d}  {msg}")
        print()

    # Failure breakdown
    if failures:
        print(f"{'='*60}")
        print(f"Failures ({len(failures)})")
        print(f"{'='*60}\n")
        failure_reasons: collections.Counter = collections.Counter()
        for stem, data in failures:
            err = data.get("error", "unknown")
            # Group by first line / error type
            first_line = err.split("\n")[0][:80]
            failure_reasons[first_line] += 1
        for reason, count in failure_reasons.most_common():
            print(f"  {count:4d}  {reason}")
        print()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Analyze retread comparison results.",
    )
    ap.add_argument(
        "data_dir",
        nargs="?",
        type=pathlib.Path,
        default=DEFAULT_DATA_DIR,
        help="Directory with JSON result files (default: %(default)s).",
    )
    args = ap.parse_args()

    if not args.data_dir.is_dir():
        print(f"Error: {args.data_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    _run(args.data_dir)


if __name__ == "__main__":
    main()

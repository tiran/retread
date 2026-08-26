"""CLI entry-point: ``python -m retread`` or ``retread``."""

import argparse
import asyncio
import json
import logging
import sys

from retread import backends
from retread._api import async_retread, sync_retread
from retread._compare import (
    Classification,
    FileDiff,
    FileEntry,
    Severity,
    WheelComparison,
)
from retread._errors import RetreadError


def _format_label(classification: Classification) -> str:
    """Return a bracketed classification label for CLI output."""
    return f" [{classification.value}]"


def _severity_label(severity: Severity) -> str:
    """Return a display label for a severity level."""
    if severity is Severity.ERROR:
        return "ERROR"
    if severity is Severity.EXPECTED:
        return "expected"
    return "notice"


def _format_entry(entry: FileEntry) -> str:
    """Format a FileEntry as an indented line."""
    label = _format_label(entry.classification)
    return f"    {entry.filename}{label}"


def _format_diff(diff: FileDiff) -> str:
    """Format a FileDiff as an indented line."""
    size_info = ""
    if diff.upstream_size != diff.downstream_size:
        size_info = f" ({diff.upstream_size} -> {diff.downstream_size} bytes)"
    label = _format_label(diff.classification)
    return f"    {diff.filename}{size_info}{label}"


_SEVERITY_ORDER = (Severity.ERROR, Severity.NOTICE, Severity.EXPECTED)
_SEVERITY_HEADERS = {
    Severity.ERROR: "Errors",
    Severity.NOTICE: "Notices",
    Severity.EXPECTED: "Expected",
}
_TYPE_HEADERS = ("upstream only", "downstream only", "different")


def _print_comparison(result: WheelComparison) -> None:
    """Print a wheel comparison result grouped by severity."""
    print(f"Upstream:   {result.upstream_wheel}")
    print(f"Downstream: {result.downstream_wheel}")
    if result.is_identical:
        print("Wheels are identical.")
        print(f"  {len(result.identical)} files match")
        return

    # Collect items grouped by severity, then by type.
    groups: dict[Severity, dict[str, list[str]]] = {
        s: {t: [] for t in _TYPE_HEADERS} for s in _SEVERITY_ORDER
    }
    for entry in result.only_upstream:
        groups[entry.severity]["upstream only"].append(_format_entry(entry))
    for entry in result.only_downstream:
        groups[entry.severity]["downstream only"].append(_format_entry(entry))
    for diff in result.different:
        groups[diff.severity]["different"].append(_format_diff(diff))

    for severity in _SEVERITY_ORDER:
        type_groups = groups[severity]
        total = sum(len(v) for v in type_groups.values())
        if not total:
            continue
        header = _SEVERITY_HEADERS[severity]
        print(f"\n{header} ({total}):")
        for type_name in _TYPE_HEADERS:
            items = type_groups[type_name]
            if items:
                print(f"  {type_name}:")
                for line in items:
                    print(line)

    print(f"\nIdentical: {len(result.identical)}")

    if result.has_errors:
        print("\nResult: ERRORS found")
    else:
        print("\nResult: OK (notices only)")


def _entry_to_dict(entry: FileEntry, side: str) -> dict[str, object]:
    """Convert a FileEntry to a JSON-serializable dict."""
    return {
        "filename": entry.filename,
        "side": side,
        "severity": entry.severity.value,
        "classification": entry.classification.value,
    }


def _diff_to_dict(diff: FileDiff) -> dict[str, object]:
    """Convert a FileDiff to a JSON-serializable dict."""
    return {
        "filename": diff.filename,
        "upstream_size": diff.upstream_size,
        "downstream_size": diff.downstream_size,
        "upstream_crc32": diff.upstream_crc32,
        "downstream_crc32": diff.downstream_crc32,
        "severity": diff.severity.value,
        "classification": diff.classification.value,
    }


def _print_json(result: WheelComparison) -> None:
    """Print a wheel comparison result as JSON."""
    data: dict[str, object] = {
        "upstream": result.upstream,
        "downstream": result.downstream,
        "upstream_wheel": result.upstream_wheel,
        "downstream_wheel": result.downstream_wheel,
        "is_identical": result.is_identical,
        "has_errors": result.has_errors,
        "only_upstream": [_entry_to_dict(e, "upstream") for e in result.only_upstream],
        "only_downstream": [_entry_to_dict(e, "downstream") for e in result.only_downstream],
        "different": [_diff_to_dict(d) for d in result.different],
        "identical": list(result.identical),
    }
    json.dump(data, sys.stdout, indent=2)
    print()


_SYNC_BACKENDS = {"requests": "RequestsBackend", "httpx2-sync": "Httpx2SyncBackend"}
_ASYNC_BACKENDS = {"httpx2": "Httpx2Backend", "aiohttp": "AiohttpBackend"}


def _run_sync(
    downstream: str,
    backend_name: str,
    downstream_index: str | None,
    upstream_index: str,
) -> WheelComparison:
    backend_cls = getattr(backends, _SYNC_BACKENDS[backend_name])
    with backend_cls() as be:
        return sync_retread(
            downstream,
            downstream_index=downstream_index,
            upstream_index=upstream_index,
            backend=be,
        )


async def _run_async(
    downstream: str,
    backend_name: str,
    downstream_index: str | None,
    upstream_index: str,
) -> WheelComparison:
    backend_cls = getattr(backends, _ASYNC_BACKENDS[backend_name])
    async with backend_cls() as be:
        return await async_retread(
            downstream,
            downstream_index=downstream_index,
            upstream_index=upstream_index,
            backend=be,
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="retread",
        description="Compare downstream rebuilds of upstream wheels.",
    )
    subparsers = parser.add_subparsers(dest="command")

    compare = subparsers.add_parser(
        "compare",
        help="compare a downstream wheel against its upstream source",
    )
    compare.add_argument(
        "downstream",
        help="URL, local file path, or wheel filename",
    )
    compare.add_argument(
        "--downstream-index",
        metavar="URL",
        default=None,
        help="resolve downstream filename from this Simple API index",
    )
    compare.add_argument(
        "--upstream-index",
        metavar="URL",
        default="https://pypi.org/simple/",
        help="upstream Simple API index (default: https://pypi.org/simple/)",
    )
    compare.add_argument(
        "-b",
        "--backend",
        choices=["requests", "httpx2-sync", "httpx2", "aiohttp"],
        default="requests",
        help="HTTP backend to use (default: requests)",
    )
    compare.add_argument(
        "-f",
        "--format",
        choices=["text", "json"],
        default="text",
        dest="output_format",
        help="output format (default: text)",
    )
    compare.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="increase logging verbosity (-v INFO, -vv DEBUG retread, -vvv DEBUG all)",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(2)

    if args.verbose >= 3:
        level = logging.DEBUG
    elif args.verbose >= 1:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(name)s: %(message)s",
        force=True,
    )
    if args.verbose == 2:
        logging.getLogger("retread").setLevel(logging.DEBUG)

    try:
        result = None
        if args.command == "compare":
            if args.backend in _SYNC_BACKENDS:
                result = _run_sync(
                    args.downstream,
                    args.backend,
                    args.downstream_index,
                    args.upstream_index,
                )
            else:
                result = asyncio.run(
                    _run_async(
                        args.downstream,
                        args.backend,
                        args.downstream_index,
                        args.upstream_index,
                    )
                )
    except RetreadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if result is not None:
        if args.output_format == "json":
            _print_json(result)
        else:
            _print_comparison(result)

        if result.has_errors:
            sys.exit(2)


if __name__ == "__main__":  # pragma: no cover
    main()

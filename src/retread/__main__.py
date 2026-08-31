"""CLI entry-point: ``python -m retread`` or ``retread``."""

import asyncio
import difflib
import json
import logging
import pathlib
import sys

import click

from retread import backends
from retread._api import async_diff, async_retread, sync_diff, sync_retread
from retread._enums import Classification, Severity
from retread._errors import PolicyError, RetreadError
from retread._findings import Comparison, FileDiff, FileEntry
from retread._policy import load_policy_dir
from retread._wheel import WheelInfo


def _format_label(classification: Classification) -> str:
    """Return a bracketed classification label for CLI output."""
    return f" [{classification.value}]"


def _severity_label(severity: Severity) -> str:
    """Return a display label for a severity level."""
    if severity is Severity.ERROR:
        return "ERROR"
    if severity is Severity.IGNORED:
        return "ignored by policy"
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


_SEVERITY_ORDER = (Severity.ERROR, Severity.NOTICE, Severity.EXPECTED, Severity.IGNORED)
_SEVERITY_HEADERS = {
    Severity.ERROR: "Errors",
    Severity.NOTICE: "Notices",
    Severity.EXPECTED: "Expected",
    Severity.IGNORED: "Ignored by policy",
}
_TYPE_HEADERS = ("upstream only", "downstream only", "different")


def _print_venv_bundles(result: Comparison) -> None:
    """Print bundled virtual environments, if any."""
    venv_bundles = result.analysis.venv_bundles
    if not venv_bundles:
        return
    print(f"\nBundled virtual environments ({len(venv_bundles)}):")
    for b in venv_bundles:
        print(f"  [{b.side}] {_severity_label(b.severity)}: {b.path}")


def _print_metadata_field_diffs(result: Comparison) -> None:
    """Print normalized METADATA field differences, if any."""
    metadata_field_diffs = result.analysis.metadata_field_diffs
    if not metadata_field_diffs:
        return
    print("\nMETADATA field differences:")
    sides = (("upstream only", "only_upstream"), ("downstream only", "only_downstream"))
    for label, attr in sides:
        side_diffs = [d for d in metadata_field_diffs if getattr(d, attr)]
        if not side_diffs:
            continue
        print(f"  {label}:")
        for d in side_diffs:
            suffix = " (ignored by policy)" if d.ignored else ""
            print(f"    {d.field}{suffix}:")
            for value in getattr(d, attr):
                print(f"      {value}")


def _print_findings_trailer(result: Comparison) -> None:
    """Print the RECORD, platform, venv, and METADATA findings.

    These are shown after both the identical and non-identical summaries, so
    the block lives in one helper called from both branches.
    """
    analysis = result.analysis
    if analysis.resolution_mismatches:
        print(f"\nUpstream resolution mismatches ({len(analysis.resolution_mismatches)}):")
        for m in analysis.resolution_mismatches:
            suffix = " (ignored by policy)" if m.ignored else ""
            print(f"  {m.message}{suffix}")
    if analysis.record_mismatches:
        print(f"\nRECORD mismatches ({len(analysis.record_mismatches)}):")
        for w in analysis.record_mismatches:
            print(f"  [{w.side}] {w.message}")
    if analysis.platform_warnings:
        print(f"\nPlatform warnings ({len(analysis.platform_warnings)}):")
        for w in analysis.platform_warnings:
            print(f"  [{w.side}] {w.message}")
    _print_venv_bundles(result)
    _print_metadata_field_diffs(result)


def _format_size(num_bytes: int) -> str:
    """Format a byte count as a human-readable SI size (kB, MB, ...).

    Uses decimal SI units (powers of 1000).  Sizes below 1 kB are shown as a
    plain byte count.
    """
    if num_bytes < 1000:
        return f"{num_bytes} bytes"
    value = float(num_bytes)
    for unit in ("kB", "MB", "GB", "TB"):
        value /= 1000
        if value < 1000:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} PB"


def _print_wheel_line(label: str, wheel: WheelInfo) -> None:
    """Print a wheel's filename and a compact line of index metadata."""
    print(f"{label}{wheel.filename}")
    origin = wheel.origin
    details: list[str] = []
    if origin.upload_time is not None:
        # Upload date only (no time-of-day).
        details.append(f"uploaded {origin.upload_time.date().isoformat()}")
    if wheel.file_size:
        details.append(_format_size(wheel.file_size))
    if origin.provenance_url:
        details.append("provenance")
    if origin.has_sdist:
        details.append("sdist")
    if origin.wheel_tags:
        details.append(f"{len(origin.wheel_tags)} wheel tags")
    if details:
        print(f"    {', '.join(details)}")


def _print_comparison(result: Comparison) -> None:
    """Print a wheel comparison result grouped by severity."""
    analysis = result.analysis
    _print_wheel_line("Upstream:   ", result.upstream)
    _print_wheel_line("Downstream: ", result.downstream)
    if result.is_identical:
        print("Wheels are identical.")
        print(f"  {len(analysis.identical)} files match")
        _print_findings_trailer(result)
        return

    # Collect items grouped by severity, then by type.
    groups: dict[Severity, dict[str, list[str]]] = {
        s: {t: [] for t in _TYPE_HEADERS} for s in _SEVERITY_ORDER
    }
    for entry in analysis.only_upstream:
        if entry.hidden:
            continue
        groups[entry.severity]["upstream only"].append(_format_entry(entry))
    for entry in analysis.only_downstream:
        if entry.hidden:
            continue
        groups[entry.severity]["downstream only"].append(_format_entry(entry))
    for diff in analysis.different:
        if diff.hidden:
            continue
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

    print(f"\nIdentical: {len(analysis.identical)}")

    _print_findings_trailer(result)

    if result.has_errors:
        print("\nResult: ERRORS found")
    else:
        print("\nResult: OK (notices only)")


def _print_json(result: Comparison) -> None:
    """Print a wheel comparison result as JSON."""
    json.dump(result.to_dict(), sys.stdout, indent=2)
    print()


def _print_file_diff(
    filename: str,
    upstream_bytes: bytes | None,
    downstream_bytes: bytes | None,
    upstream_label: str,
    downstream_label: str,
) -> None:
    """Print a unified diff or status message for a single file."""
    print(f"--- {filename} ---")
    if upstream_bytes is None and downstream_bytes is None:
        print("File not found in either wheel.")
        return

    if upstream_bytes is None:
        print(f"Only in downstream: {downstream_label}")
        try:
            text = downstream_bytes.decode("utf-8")  # type: ignore[union-attr]
            for line in text.splitlines():
                print(f"  {line}")
        except (UnicodeDecodeError, ValueError):
            print("  Binary file.")
        return

    if downstream_bytes is None:
        print(f"Only in upstream: {upstream_label}")
        try:
            text = upstream_bytes.decode("utf-8")
            for line in text.splitlines():
                print(f"  {line}")
        except (UnicodeDecodeError, ValueError):
            print("  Binary file.")
        return

    if upstream_bytes == downstream_bytes:
        print("Files are identical.")
        return

    # Both present and different
    try:
        upstream_text = upstream_bytes.decode("utf-8")
        downstream_text = downstream_bytes.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        print("Binary files differ.")
        return

    diff_lines = difflib.unified_diff(
        upstream_text.splitlines(keepends=True),
        downstream_text.splitlines(keepends=True),
        fromfile=f"upstream/{filename}",
        tofile=f"downstream/{filename}",
    )
    for line in diff_lines:
        # unified_diff lines may or may not end with newline
        print(line, end="" if line.endswith("\n") else "\n")


_SYNC_BACKENDS = {"requests": "RequestsBackend", "httpx2-sync": "Httpx2SyncBackend"}
_ASYNC_BACKENDS = {"httpx2": "Httpx2Backend", "aiohttp": "AiohttpBackend"}
_BACKEND_CHOICES = [*_SYNC_BACKENDS, *_ASYNC_BACKENDS]


def _run_sync(
    downstream: str,
    backend_name: str,
    downstream_index: str | None,
    upstream_index: str,
    policy: dict | None = None,
) -> Comparison:
    backend_cls = getattr(backends, _SYNC_BACKENDS[backend_name])
    with backend_cls() as be:
        return sync_retread(
            downstream,
            downstream_index=downstream_index,
            upstream_index=upstream_index,
            backend=be,
            policy=policy,
        )


async def _run_async(
    downstream: str,
    backend_name: str,
    downstream_index: str | None,
    upstream_index: str,
    policy: dict | None = None,
) -> Comparison:
    backend_cls = getattr(backends, _ASYNC_BACKENDS[backend_name])
    async with backend_cls() as be:
        return await async_retread(
            downstream,
            downstream_index=downstream_index,
            upstream_index=upstream_index,
            backend=be,
            policy=policy,
        )


def _run_diff_sync(
    downstream: str,
    files: list[str],
    backend_name: str,
    downstream_index: str | None,
    upstream_index: str,
) -> list[tuple[str, bytes | None, bytes | None]]:
    backend_cls = getattr(backends, _SYNC_BACKENDS[backend_name])
    with backend_cls() as be:
        return sync_diff(
            downstream,
            files,
            downstream_index=downstream_index,
            upstream_index=upstream_index,
            backend=be,
        )


async def _run_diff_async(
    downstream: str,
    files: list[str],
    backend_name: str,
    downstream_index: str | None,
    upstream_index: str,
) -> list[tuple[str, bytes | None, bytes | None]]:
    backend_cls = getattr(backends, _ASYNC_BACKENDS[backend_name])
    async with backend_cls() as be:
        return await async_diff(
            downstream,
            files,
            downstream_index=downstream_index,
            upstream_index=upstream_index,
            backend=be,
        )


def _setup_logging(verbose: int) -> None:
    """Configure logging based on verbosity level."""
    if verbose >= 3:
        level = logging.DEBUG
    elif verbose >= 1:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(name)s: %(message)s",
        force=True,
    )
    if verbose == 2:
        logging.getLogger("retread").setLevel(logging.DEBUG)


def _shared_options(func):
    """Apply the shared CLI options for compare and diff subcommands."""
    func = click.argument("downstream")(func)
    func = click.option(
        "--downstream-index",
        metavar="URL",
        default=None,
        help="Resolve downstream filename from this Simple API index.",
    )(func)
    func = click.option(
        "--upstream-index",
        metavar="URL",
        default="https://pypi.org/simple/",
        show_default=True,
        help="Upstream Simple API index.",
    )(func)
    func = click.option(
        "-b",
        "--backend",
        type=click.Choice(_BACKEND_CHOICES),
        default="requests",
        show_default=True,
        help="HTTP backend to use.",
    )(func)
    func = click.option(
        "-v",
        "--verbose",
        count=True,
        help="Increase logging verbosity (-v INFO, -vv DEBUG retread, -vvv DEBUG all).",
    )(func)
    return func


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.option(
    "-p",
    "--policy-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=pathlib.Path),
    default=None,
    help="Directory of per-package TOML policy files.",
)
@click.pass_context
def cli(ctx: click.Context, policy_dir: pathlib.Path | None) -> None:
    """Compare downstream rebuilds of upstream wheels."""
    ctx.ensure_object(dict)
    ctx.obj["policy"] = None
    if policy_dir is not None:
        try:
            ctx.obj["policy"] = load_policy_dir(policy_dir)
        except PolicyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            ctx.exit(1)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit(2)


@cli.command()
@_shared_options
@click.option(
    "-f",
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.pass_context
def compare(
    ctx: click.Context,
    downstream: str,
    downstream_index: str | None,
    upstream_index: str,
    backend: str,
    verbose: int,
    output_format: str,
) -> None:
    """Compare a downstream wheel against its upstream source."""
    _setup_logging(verbose)
    policy = ctx.obj["policy"]
    try:
        if backend in _SYNC_BACKENDS:
            result = _run_sync(downstream, backend, downstream_index, upstream_index, policy)
        else:
            result = asyncio.run(
                _run_async(downstream, backend, downstream_index, upstream_index, policy)
            )
    except RetreadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if output_format == "json":
        _print_json(result)
    else:
        _print_comparison(result)

    if result.has_errors:
        sys.exit(2)


@cli.command()
@_shared_options
@click.argument("files", nargs=-1, required=True)
def diff(
    downstream: str,
    downstream_index: str | None,
    upstream_index: str,
    backend: str,
    verbose: int,
    files: tuple[str, ...],
) -> None:
    """Show unified diffs of files between upstream and downstream wheels."""
    _setup_logging(verbose)
    try:
        file_list = list(files)
        if backend in _SYNC_BACKENDS:
            diff_results = _run_diff_sync(
                downstream, file_list, backend, downstream_index, upstream_index
            )
        else:
            diff_results = asyncio.run(
                _run_diff_async(downstream, file_list, backend, downstream_index, upstream_index)
            )
    except RetreadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    upstream_label = upstream_index
    downstream_label = downstream_index or downstream
    for fname, up_bytes, down_bytes in diff_results:
        _print_file_diff(fname, up_bytes, down_bytes, upstream_label, downstream_label)


def main(argv: list[str] | None = None) -> None:
    """Entry point for ``retread`` console script."""
    cli(args=argv, standalone_mode=True)


if __name__ == "__main__":  # pragma: no cover
    main()

#!/usr/bin/env python3
"""Test retread against wheels from a RHOAI Pulp content index.

Fetches the wheel listing from a RHOAI Pulp content index, selects the
latest version of each package (filtered to 'any' or 'linux_x86_64'
platforms), picks a random subset that doesn't already have stored
results, and runs retread compare on each using async httpx2 with
bounded concurrency.

Results are stored as JSON in data/3.6-EA1/cpu-ubi9-test/.

Usage::

    .venv/bin/python contrib/test_rhoai_index.py
    .venv/bin/python contrib/test_rhoai_index.py -n 10
    .venv/bin/python contrib/test_rhoai_index.py --all
    .venv/bin/python contrib/test_rhoai_index.py --seed 42
    .venv/bin/python contrib/test_rhoai_index.py --concurrency 5
    .venv/bin/python contrib/test_rhoai_index.py --update-errors
    .venv/bin/python contrib/test_rhoai_index.py --update-failed
"""

import asyncio
import json
import pathlib
import random
import re
import sys
import urllib.parse
from html.parser import HTMLParser

import click
import httpx2
import packaging.tags
import tqdm
import tqdm.asyncio
import packaging.utils
import packaging.version

from retread._api import async_retread
from retread._errors import RetreadError
from retread._policy import load_policy_dir
from retread.backends import Httpx2Backend

INDEX_URL = (
    "https://packages.redhat.com/api/pulp-content/public-rhai"
    "/rhoai/3.6-EA1/cpu-ubi9-test/"
)
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
POLICY_DIR = SCRIPT_DIR.parent / "examples" / "policies"
DATA_DIR = SCRIPT_DIR.parent / "data" / "3.6-EA1" / "cpu-ubi9-test"
DEFAULT_COUNT = 20
DEFAULT_CONCURRENCY = 10

_ARCH_RE = re.compile(r"_(x86_64|aarch64|ppc64le|s390x|arm64|i686)$")


class _LinkParser(HTMLParser):
    """Extract href attributes from <a> tags."""

    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.hrefs.append(value)


async def _fetch_wheel_links(
    client: httpx2.AsyncClient, index_url: str
) -> list[tuple[str, str]]:
    """Fetch the index page and return (filename, url) pairs for .whl files."""
    resp = await client.get(index_url)
    resp.raise_for_status()

    parser = _LinkParser()
    parser.feed(resp.text)

    results = []
    for href in parser.hrefs:
        parsed = urllib.parse.urlparse(href)
        filename = urllib.parse.unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1])
        if not filename.endswith(".whl"):
            continue
        url = urllib.parse.urljoin(index_url, href)
        results.append((filename, url))
    return results


def _is_wanted_platform(tags: frozenset[packaging.tags.Tag]) -> bool:
    """Return True if the wheel targets 'any' or 'linux_x86_64'."""
    for tag in tags:
        if tag.platform == "any":
            return True
        m = _ARCH_RE.search(tag.platform)
        if m and m.group(1) == "x86_64":
            return True
    return False


def _build_sort_key(
    build_tag: tuple[int, str] | None,
) -> tuple[int, str]:
    """Sort key for build tags (higher build number sorts later)."""
    if build_tag is None:
        return (0, "")
    return build_tag


def _select_latest_wheels(
    wheel_links: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Group by package name, filter platforms, pick latest version/build."""
    parsed: dict[
        str,
        list[tuple[packaging.version.Version, tuple[int, str], str, str]],
    ] = {}
    for filename, url in wheel_links:
        try:
            name, version, build_tag, tags = packaging.utils.parse_wheel_filename(
                filename
            )
        except packaging.utils.InvalidWheelFilename:
            continue
        if not _is_wanted_platform(tags):
            continue
        key = str(name)
        bt = _build_sort_key(build_tag)
        parsed.setdefault(key, []).append((version, bt, filename, url))

    latest = []
    for name in sorted(parsed):
        entries = parsed[name]
        entries.sort(key=lambda e: (e[0], e[1]), reverse=True)
        _, _, filename, url = entries[0]
        latest.append((filename, url))
    return latest


def _result_path(filename: str) -> pathlib.Path:
    """Return the JSON result path for a wheel filename."""
    stem = filename.removesuffix(".whl")
    return DATA_DIR / f"{stem}.json"


def _has_stored_errors(filename: str) -> bool:
    """Return True if the stored result for *filename* reports errors.

    A missing or unreadable result, or a failure record (which has no
    ``has_errors`` field), counts as no errors.
    """
    result_path = _result_path(filename)
    if not result_path.exists():
        return False
    try:
        data = json.loads(result_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("has_errors"))


def _has_stored_failure(filename: str) -> bool:
    """Return True if the stored result for *filename* is a failure record.

    A failure record is written when resolution or comparison raised (for
    example a missing upstream wheel); it carries an ``error`` field instead of
    a comparison result.  A missing or unreadable result counts as no failure.
    """
    result_path = _result_path(filename)
    if not result_path.exists():
        return False
    try:
        data = json.loads(result_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return "error" in data


def _is_server_error(exc: BaseException) -> bool:
    """Return True if the exception is caused by an HTTP 5xx server error."""
    if isinstance(exc, httpx2.HTTPStatusError):
        return exc.response.status_code >= 500
    # RetreadError and other exceptions may wrap an HTTPStatusError
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        return _is_server_error(cause)
    return False


async def _compare_one(
    filename: str,
    url: str,
    backend: Httpx2Backend,
    semaphore: asyncio.Semaphore,
    policy: dict | None = None,
) -> str:
    """Compare a single wheel, returning a status string."""
    result_path = _result_path(filename)
    async with semaphore:
        try:
            result = await async_retread(url, backend=backend, policy=policy)
            result_path.write_text(json.dumps(result.to_dict(), indent=2) + "\n")
            if result.has_errors:
                return "errors"
            return "ok"
        except RetreadError as exc:
            tqdm.tqdm.write(f"  retread error: {filename}: {exc}")
            if not _is_server_error(exc):
                data = {"error": str(exc), "downstream": url}
                result_path.write_text(json.dumps(data, indent=2) + "\n")
            return "failure"
        except Exception as exc:
            tqdm.tqdm.write(f"  unexpected error: {filename}: {exc}")
            if not _is_server_error(exc):
                data = {"error": str(exc), "downstream": url}
                result_path.write_text(json.dumps(data, indent=2) + "\n")
            return "failure"


async def _run(
    count: int,
    seed: int | None,
    test_all: bool,
    update_errors: bool,
    update_failed: bool,
    concurrency: int,
) -> None:
    async with httpx2.AsyncClient(
        http2=True, follow_redirects=True, timeout=60
    ) as client:
        print(f"Fetching wheel listing from {INDEX_URL}")
        wheel_links = await _fetch_wheel_links(client, INDEX_URL)
        print(f"  {len(wheel_links)} wheel files found")

    latest = _select_latest_wheels(wheel_links)
    print(f"  {len(latest)} packages (latest version, any/x86_64 only)")

    if update_errors or update_failed:
        selected = [
            (f, u)
            for f, u in latest
            if _has_stored_errors(f) or (update_failed and _has_stored_failure(f))
        ]
        label = "with errors or failures" if update_failed else "with stored errors"
        print(f"  {len(selected)} {label} to update")
        if not selected:
            print("\nNothing to update.")
            return
    else:
        untested = [(f, u) for f, u in latest if not _result_path(f).exists()]
        print(f"  {len(untested)} without stored results")

        if not untested:
            print("\nAll wheels already have stored results.")
            return

        if test_all:
            selected = untested
        else:
            n = min(count, len(untested))
            rng = random.Random(seed)
            selected = rng.sample(untested, n)

    selected.sort()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    policy = load_policy_dir(POLICY_DIR) if POLICY_DIR.is_dir() else None

    semaphore = asyncio.Semaphore(concurrency)
    async with Httpx2Backend(timeout=120) as backend:
        tasks = [
            _compare_one(filename, url, backend, semaphore, policy=policy)
            for filename, url in selected
        ]
        statuses = await tqdm.asyncio.tqdm.gather(
            *tasks, desc="Comparing wheels", unit="whl"
        )

    pass_count = sum(1 for s in statuses if s == "ok")
    fail_count = sum(1 for s in statuses if s == "errors")
    error_count = sum(1 for s in statuses if s == "failure")
    total = pass_count + fail_count + error_count

    print()
    for (filename, _url), status in zip(selected, statuses, strict=True):
        print(f"  {filename}: {status}")

    print(
        f"\nDone: {pass_count} clean, {fail_count} with errors, "
        f"{error_count} failures ({total} total)"
    )
    if fail_count or error_count:
        sys.exit(1)


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "-n",
    "--count",
    type=int,
    default=DEFAULT_COUNT,
    show_default=True,
    help="Number of wheels to test.",
)
@click.option(
    "--seed",
    type=int,
    default=None,
    help="Random seed for reproducible selection.",
)
@click.option(
    "--all",
    "test_all",
    is_flag=True,
    help="Test all wheels without stored results.",
)
@click.option(
    "--update-errors",
    is_flag=True,
    help="Re-run all packages whose stored result reports errors.",
)
@click.option(
    "--update-failed",
    is_flag=True,
    help="Re-run all packages whose stored result reports errors or a failure "
    "(e.g. a missing upstream wheel).",
)
@click.option(
    "-j",
    "--concurrency",
    type=int,
    default=DEFAULT_CONCURRENCY,
    show_default=True,
    help="Max parallel fetches.",
)
def main(
    count: int,
    seed: int | None,
    test_all: bool,
    update_errors: bool,
    update_failed: bool,
    concurrency: int,
) -> None:
    """Test retread against wheels from a RHOAI Pulp content index."""
    asyncio.run(_run(count, seed, test_all, update_errors, update_failed, concurrency))


if __name__ == "__main__":
    main()

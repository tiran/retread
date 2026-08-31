"""Compare downstream rebuilds of upstream wheels to detect differences and bugs.

A retread is a rebuilt tire -- **retread** checks the quality of rebuilt wheels.

Uses `zipwire <https://github.com/tiran/zipwire>`_ for efficient remote
wheel access via HTTP range requests and
`pypi-simple <https://github.com/jwodder/pypi-simple>`_ to interact with
package indexes.

Example
-------

High-level API - provide a downstream wheel URL and let retread resolve
the upstream automatically::

    from retread import sync_retread

    result = sync_retread("https://rebuild.example/.../foo-1.0-py3-none-any.whl")
    if result.is_identical:
        print("Wheels are identical")
    else:
        for diff in result.analysis.different:
            print(f"CHANGED: {diff.filename}")

Low-level API - manage your own RemoteZip objects::

    from zipwire import AsyncRemoteZip
    from zipwire.backends import AiohttpReader
    from retread import Context, WheelInfo, compare

    async def main():
        upstream_url = "https://files.pythonhosted.org/.../foo-1.0-py3-none-any.whl"
        downstream_url = "https://rebuild.example/.../foo-1.0-py3-none-any.whl"

        async with AsyncRemoteZip(AiohttpReader(upstream_url)) as upstream:
            async with AsyncRemoteZip(AiohttpReader(downstream_url)) as downstream:
                up = await WheelInfo.from_async_remote(upstream)
                down = await WheelInfo.from_async_remote(downstream)
        result = compare(Context.default(), up, down)

Backends
--------

All backends live in :mod:`retread.backends` and are lazily imported.

Synchronous:
  - ``RequestsBackend`` -- default, uses *requests* (no extra install)
  - ``Httpx2SyncBackend`` -- uses *httpx2*, supports HTTP/2
    (``pip install retread[httpx2]``)

Asynchronous:
  - ``AiohttpBackend`` -- uses *aiohttp* (``pip install retread[aiohttp]``)
  - ``Httpx2Backend`` -- uses *httpx2*, supports HTTP/2
    (``pip install retread[httpx2]``)
"""

from retread._api import async_diff, async_retread, sync_diff, sync_retread
from retread._context import Context
from retread._enums import Classification, Severity, Side
from retread._errors import (
    ComparisonError,
    InvalidMetadataError,
    InvalidWheelError,
    NoWheelsError,
    PolicyError,
    ProjectNotFoundError,
    RetreadError,
    VersionNotFoundError,
    WheelNotFoundError,
)
from retread._findings import (
    Analysis,
    Comparison,
    FileDiff,
    FileEntry,
    MetadataFieldDiff,
    PlatformWarning,
    RecordMismatch,
    ResolutionMismatch,
    VenvBundle,
)
from retread._policy import PackagePolicy, VersionPolicy, load_policy_dir
from retread._pypi import AsyncPyPISimple
from retread._resolve import Resolution, ResolutionStatus, WheelSpec, parse_wheel_spec
from retread._types import Filename, Url
from retread._wheel import FileStat, WheelInfo, WheelSource
from retread.checker import compare

__all__ = [
    "Analysis",
    "AsyncPyPISimple",
    "Classification",
    "Comparison",
    "ComparisonError",
    "Context",
    "FileDiff",
    "FileEntry",
    "FileStat",
    "Filename",
    "InvalidMetadataError",
    "InvalidWheelError",
    "MetadataFieldDiff",
    "NoWheelsError",
    "PackagePolicy",
    "PlatformWarning",
    "PolicyError",
    "ProjectNotFoundError",
    "RecordMismatch",
    "Resolution",
    "ResolutionMismatch",
    "ResolutionStatus",
    "RetreadError",
    "Severity",
    "Side",
    "Url",
    "VenvBundle",
    "VersionNotFoundError",
    "VersionPolicy",
    "WheelInfo",
    "WheelNotFoundError",
    "WheelSource",
    "WheelSpec",
    "async_diff",
    "async_retread",
    "compare",
    "load_policy_dir",
    "parse_wheel_spec",
    "sync_diff",
    "sync_retread",
]

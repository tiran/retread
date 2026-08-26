"""Compare downstream rebuilds of upstream wheels to detect differences and bugs.

A retread is a rebuilt tire -- **retread** checks the quality of rebuilt wheels.

Uses `zipwire <https://github.com/tiran/zipwire>`_ for efficient remote
wheel access via HTTP range requests and
`pypi-simple <https://github.com/jwodder/pypi-simple>`_ to interact with
package indexes.

Example
-------

High-level API — provide a downstream wheel URL and let retread resolve
the upstream automatically::

    from retread import sync_retread

    result = sync_retread("https://rebuild.example/.../foo-1.0-py3-none-any.whl")
    if result.is_identical:
        print("Wheels are identical")
    else:
        for diff in result.different:
            print(f"CHANGED: {diff.filename}")

Low-level API — manage your own RemoteZip objects::

    from zipwire import AsyncRemoteZip
    from zipwire.backends import AiohttpReader
    from retread import async_compare_wheels

    async def main():
        upstream_url = "https://files.pythonhosted.org/.../foo-1.0-py3-none-any.whl"
        downstream_url = "https://rebuild.example/.../foo-1.0-py3-none-any.whl"

        u_reader = AiohttpReader(upstream_url)
        d_reader = AiohttpReader(downstream_url)
        async with AsyncRemoteZip(u_reader) as upstream:
            async with AsyncRemoteZip(d_reader) as downstream:
                result = await async_compare_wheels(upstream, downstream)

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
from retread._compare import (
    Classification,
    FileDiff,
    FileEntry,
    Severity,
    WheelComparison,
    async_compare_wheels,
    compare_wheels,
)
from retread._errors import (
    ComparisonError,
    InvalidWheelError,
    RetreadError,
    WheelNotFoundError,
)
from retread._platform import PlatformWarning
from retread._pypi import AsyncPyPISimple
from retread._record import RecordMismatch
from retread._resolve import WheelSpec, parse_wheel_spec

__all__ = [
    "AsyncPyPISimple",
    "Classification",
    "ComparisonError",
    "FileDiff",
    "FileEntry",
    "InvalidWheelError",
    "PlatformWarning",
    "RecordMismatch",
    "RetreadError",
    "Severity",
    "WheelComparison",
    "WheelNotFoundError",
    "WheelSpec",
    "async_compare_wheels",
    "async_diff",
    "async_retread",
    "compare_wheels",
    "parse_wheel_spec",
    "sync_diff",
    "sync_retread",
]

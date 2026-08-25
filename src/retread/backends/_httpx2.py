"""Sync and async backends using httpx2.

Provides zipwire readers and PyPI Simple API clients from shared
httpx2 clients.  Supports HTTP/2 when the ``h2`` library is available.
"""

from __future__ import annotations

import json
import typing
from typing import Any, Self

import packaging.utils
import pypi_simple
import pypi_simple.errors
import pypi_simple.util

from retread._pypi import _parse_index_page, _parse_project_page

if typing.TYPE_CHECKING:
    from zipwire import AsyncReader, SyncReader


class Httpx2PyPIClient:
    """Async PyPI Simple API client using httpx2.

    Same interface as :class:`~retread._pypi.AsyncPyPISimple` but
    uses ``httpx2.AsyncClient`` instead of ``aiohttp.ClientSession``.
    """

    def __init__(
        self,
        client: Any,
        endpoint: str = pypi_simple.PYPI_SIMPLE_ENDPOINT,
        accept: str = pypi_simple.ACCEPT_JSON_PREFERRED,
    ) -> None:
        self.client = client
        self.endpoint = endpoint.rstrip("/") + "/"
        self.accept = accept

    def get_project_url(self, project: str) -> str:
        """Return the Simple API URL for a project."""
        return self.endpoint + packaging.utils.canonicalize_name(project) + "/"

    async def get_index_page(self, accept: str | None = None) -> pypi_simple.IndexPage:
        """Fetch the Simple API index page listing all projects."""
        headers = {"Accept": accept or self.accept}
        resp = await self.client.get(self.endpoint, headers=headers)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "text/html")
        serial = resp.headers.get("X-PyPI-Last-Serial")
        return _parse_index_page(ct, resp.content, str(resp.url), serial)

    async def get_project_page(
        self, project: str, accept: str | None = None
    ) -> pypi_simple.ProjectPage:
        """Fetch the Simple API page for a single project."""
        url = self.get_project_url(project)
        headers = {"Accept": accept or self.accept}
        resp = await self.client.get(url, headers=headers)
        if resp.status_code == 404:
            raise pypi_simple.errors.NoSuchProjectError(project, url)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "text/html")
        serial = resp.headers.get("X-PyPI-Last-Serial")
        return _parse_project_page(project, ct, resp.content, str(resp.url), serial)

    async def get_package_metadata_bytes(
        self,
        pkg: pypi_simple.DistributionPackage,
        verify: bool = True,
    ) -> bytes:
        """Fetch raw PEP 658 metadata bytes for a distribution package."""
        url = pkg.metadata_url
        if url is None:
            raise pypi_simple.errors.NoMetadataError(pkg.filename, None)
        checker: pypi_simple.util.AbstractDigestChecker
        if verify and pkg.metadata_digests:
            checker = pypi_simple.util.DigestChecker(pkg.metadata_digests, url)
        else:
            checker = pypi_simple.util.NullDigestChecker()
        resp = await self.client.get(url)
        if resp.status_code == 404:
            raise pypi_simple.errors.NoMetadataError(pkg.filename, url)
        resp.raise_for_status()
        checker.update(resp.content)
        checker.finalize()
        return resp.content

    async def get_package_metadata(
        self,
        pkg: pypi_simple.DistributionPackage,
        verify: bool = True,
    ) -> str:
        """Fetch PEP 658 metadata for a distribution package as a string."""
        data = await self.get_package_metadata_bytes(pkg, verify=verify)
        return data.decode("utf-8", "surrogateescape")

    async def get_provenance(self, pkg: pypi_simple.DistributionPackage) -> dict[str, Any]:
        """Fetch PEP 740 provenance data for a distribution package."""
        url = pkg.provenance_url
        if url is None:
            raise pypi_simple.errors.NoProvenanceError(pkg.filename, None)
        resp = await self.client.get(url)
        if resp.status_code == 404:
            raise pypi_simple.errors.NoProvenanceError(pkg.filename, url)
        resp.raise_for_status()
        return json.loads(resp.content)


_DEFAULT_TIMEOUT = 30


class Httpx2SyncBackend:
    """Sync backend using httpx2 for HTTP access.

    Manages an ``httpx2.Client`` and provides a
    ``pypi_simple.PyPISimple`` client and zipwire sync readers.
    Supports HTTP/2 when the ``h2`` library is installed.

    Can be used as a context manager::

        with Httpx2SyncBackend() as backend:
            pypi = backend.pypi_client()
            page = pypi.get_project_page("requests")
            reader = backend.wheel_reader(page.packages[0].url)

    Args:
        client: Optional pre-configured ``httpx2.Client``.
            If not provided, a new client is created on first use.
        timeout: Request timeout in seconds (default: 30).
    """

    def __init__(self, client: Any | None = None, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._client = client
        self._owns_client = client is None
        self.timeout = timeout

    @property
    def client(self) -> Any:
        """The underlying httpx2 sync client, created lazily if needed."""
        if self._client is None:
            import httpx2

            self._client = httpx2.Client(http2=True, follow_redirects=True, timeout=self.timeout)
        return self._client

    def pypi_client(
        self,
        endpoint: str = pypi_simple.PYPI_SIMPLE_ENDPOINT,
        accept: str = pypi_simple.ACCEPT_JSON_PREFERRED,
    ) -> pypi_simple.PyPISimple:
        """Create a sync PyPI Simple API client.

        Note: ``pypi_simple.PyPISimple`` only supports ``requests.Session``,
        so PyPI index access uses its own requests session rather than the
        httpx2 client.  Wheel access still uses httpx2 via :meth:`wheel_reader`.
        """
        return pypi_simple.PyPISimple(endpoint, accept=accept)

    def wheel_reader(self, url: str) -> SyncReader:
        """Create a zipwire sync reader for the given wheel URL."""
        from zipwire.backends import Httpx2SyncReader

        return Httpx2SyncReader(url, client=self.client)

    def close(self) -> None:
        """Close the client if owned by this backend."""
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class Httpx2Backend:
    """Async backend using httpx2 for HTTP access.

    Manages an ``httpx2.AsyncClient`` and provides a PyPI Simple
    API client and zipwire async readers.  Supports HTTP/2 when
    the ``h2`` library is installed.

    Can be used as an async context manager::

        async with Httpx2Backend() as backend:
            pypi = backend.pypi_client()
            page = await pypi.get_project_page("requests")

    Args:
        client: Optional pre-configured ``httpx2.AsyncClient``.
            If not provided, a new client is created on first use.
        timeout: Request timeout in seconds (default: 30).
    """

    def __init__(self, client: Any | None = None, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._client = client
        self._owns_client = client is None
        self.timeout = timeout

    @property
    def client(self) -> Any:
        """The underlying httpx2 async client, created lazily if needed."""
        if self._client is None:
            import httpx2

            self._client = httpx2.AsyncClient(
                http2=True, follow_redirects=True, timeout=self.timeout
            )
        return self._client

    def pypi_client(
        self,
        endpoint: str = pypi_simple.PYPI_SIMPLE_ENDPOINT,
        accept: str = pypi_simple.ACCEPT_JSON_PREFERRED,
    ) -> Httpx2PyPIClient:
        """Create an async PyPI Simple API client using this backend's client."""
        return Httpx2PyPIClient(self.client, endpoint=endpoint, accept=accept)

    def wheel_reader(self, url: str) -> AsyncReader:
        """Create a zipwire async reader for the given wheel URL."""
        from zipwire.backends import Httpx2AsyncReader

        return Httpx2AsyncReader(url, client=self.client)

    async def close(self) -> None:
        """Close the client if owned by this backend."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

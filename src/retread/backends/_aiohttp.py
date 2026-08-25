"""Async backend using aiohttp.

Provides both a PyPI Simple API client and zipwire async readers
from a shared ``aiohttp.ClientSession``.
"""

from __future__ import annotations

import typing
from typing import Self

import pypi_simple

from retread._pypi import AsyncPyPISimple

if typing.TYPE_CHECKING:
    from aiohttp import ClientSession
    from zipwire import AsyncReader


_DEFAULT_TIMEOUT = 30


class AiohttpBackend:
    """Async backend using aiohttp for HTTP access.

    Manages an ``aiohttp.ClientSession`` and provides a PyPI Simple
    API client and zipwire async readers from the same session.

    Can be used as an async context manager::

        async with AiohttpBackend() as backend:
            pypi = backend.pypi_client()
            page = await pypi.get_project_page("requests")
            reader = backend.wheel_reader(page.packages[0].url)

    Args:
        session: Optional pre-configured ``aiohttp.ClientSession``.
            If not provided, a new session is created on first use.
        timeout: Request timeout in seconds (default: 30).
    """

    def __init__(
        self, session: ClientSession | None = None, timeout: float = _DEFAULT_TIMEOUT
    ) -> None:
        self._session = session
        self._owns_session = session is None
        self.timeout = timeout

    @property
    def session(self) -> ClientSession:
        """The underlying aiohttp session, created lazily if needed."""
        if self._session is None:
            import aiohttp

            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self._session

    def pypi_client(
        self,
        endpoint: str = pypi_simple.PYPI_SIMPLE_ENDPOINT,
        accept: str = pypi_simple.ACCEPT_JSON_PREFERRED,
    ) -> AsyncPyPISimple:
        """Create an async PyPI Simple API client using this backend's session."""
        return AsyncPyPISimple(self.session, endpoint=endpoint, accept=accept)

    def wheel_reader(self, url: str) -> AsyncReader:
        """Create a zipwire async reader for the given wheel URL."""
        from zipwire.backends import AiohttpReader

        return AiohttpReader(url, session=self.session)

    async def close(self) -> None:
        """Close the session if owned by this backend."""
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

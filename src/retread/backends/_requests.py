"""Sync backend using requests.

Provides zipwire sync readers and a ``pypi_simple.PyPISimple`` client
from a shared ``requests.Session``.
"""

from __future__ import annotations

import typing
from typing import Self

import pypi_simple

if typing.TYPE_CHECKING:
    from requests import Session
    from zipwire import SyncReader


_DEFAULT_TIMEOUT = 30


class RequestsBackend:
    """Sync backend using requests for HTTP access.

    Manages a ``requests.Session`` and provides a
    ``pypi_simple.PyPISimple`` client and zipwire sync readers
    from the same session.

    Can be used as a context manager::

        with RequestsBackend() as backend:
            pypi = backend.pypi_client()
            page = pypi.get_project_page("requests")
            reader = backend.wheel_reader(page.packages[0].url)

    Args:
        session: Optional pre-configured ``requests.Session``.
            If not provided, a new session is created on first use.
        timeout: Request timeout in seconds (default: 30).
    """

    def __init__(self, session: Session | None = None, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._session = session
        self._owns_session = session is None
        self.timeout = timeout

    @property
    def session(self) -> Session:
        """The underlying requests session, created lazily if needed."""
        if self._session is None:
            import requests
            from requests.adapters import HTTPAdapter

            self._session = requests.Session()
            adapter = HTTPAdapter(max_retries=0)
            self._session.mount("http://", adapter)
            self._session.mount("https://", adapter)
        return self._session

    def pypi_client(
        self,
        endpoint: str = pypi_simple.PYPI_SIMPLE_ENDPOINT,
        accept: str = pypi_simple.ACCEPT_JSON_PREFERRED,
    ) -> pypi_simple.PyPISimple:
        """Create a sync PyPI Simple API client.

        Uses ``pypi_simple.PyPISimple`` with the configured endpoint
        and accept header.
        """
        return pypi_simple.PyPISimple(endpoint, accept=accept, session=self.session)

    def wheel_reader(self, url: str) -> SyncReader:
        """Create a zipwire sync reader for the given wheel URL."""
        from zipwire.backends import RequestsReader

        return RequestsReader(url, session=self.session)

    def close(self) -> None:
        """Close the session if owned by this backend."""
        if self._owns_session and self._session is not None:
            self._session.close()
            self._session = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

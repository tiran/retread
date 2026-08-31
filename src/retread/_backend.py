"""Protocol definitions for retread HTTP backends.

Defines the :class:`SyncBackend` / :class:`AsyncBackend` structural protocols
that every adapter in :mod:`retread.backends` satisfies.
"""

from __future__ import annotations

import typing
from typing import Self

if typing.TYPE_CHECKING:
    import pypi_simple
    from zipwire import AsyncReader, SyncReader

    from retread._pypi import AsyncPyPISimple


@typing.runtime_checkable
class SyncBackend(typing.Protocol):
    """Protocol for synchronous HTTP backends.

    A sync backend provides a PyPI Simple API client and zipwire
    readers for wheel access.
    """

    def pypi_client(
        self,
        endpoint: str = ...,
        accept: str = ...,
    ) -> pypi_simple.PyPISimple:
        """Create a sync PyPI Simple API client."""
        ...

    def wheel_reader(self, url: str) -> SyncReader:
        """Create a zipwire sync reader for the given wheel URL."""
        ...

    def close(self) -> None:
        """Release any held resources."""
        ...

    def __enter__(self) -> Self:
        """Enter the context manager."""
        ...

    def __exit__(self, *exc: object) -> None:
        """Exit the context manager."""
        ...


@typing.runtime_checkable
class AsyncBackend(typing.Protocol):
    """Protocol for asynchronous HTTP backends.

    An async backend provides both a PyPI Simple API client
    and zipwire readers for wheel access.
    """

    def pypi_client(
        self,
        endpoint: str = ...,
    ) -> AsyncPyPISimple:
        """Create an async PyPI Simple API client."""
        ...

    def wheel_reader(self, url: str) -> AsyncReader:
        """Create a zipwire async reader for the given wheel URL."""
        ...

    async def close(self) -> None:
        """Release any held resources."""
        ...

    async def __aenter__(self) -> Self:
        """Enter the async context manager."""
        ...

    async def __aexit__(self, *exc: object) -> None:
        """Exit the async context manager."""
        ...

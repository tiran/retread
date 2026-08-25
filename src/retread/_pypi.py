"""Async PyPI Simple API client (PEP 503/691).

Adapted from maroilles for use with retread's pluggable HTTP backends.
The aiohttp-based implementation can be used directly; other backends
provide their own client classes with the same interface.
"""

from __future__ import annotations

import json
import typing
from typing import Any

import packaging.utils
import pypi_simple
import pypi_simple.errors
import pypi_simple.util

if typing.TYPE_CHECKING:
    from aiohttp import ClientSession


def _parse_content_type(value: str) -> tuple[str, dict[str, str]]:
    """Parse a Content-Type header into media type and parameters."""
    parts = value.split(";")
    media_type = parts[0].strip().lower()
    params: dict[str, str] = {}
    for part in parts[1:]:
        key, _, val = part.strip().partition("=")
        if key:
            params[key.strip().lower()] = val.strip().strip('"')
    return media_type, params


def _parse_index_page(
    content_type: str, body: bytes, url: str, last_serial: str | None
) -> pypi_simple.IndexPage:
    media_type, params = _parse_content_type(content_type)
    if media_type == "application/vnd.pypi.simple.v1+json":
        page = pypi_simple.IndexPage.from_json_data(json.loads(body))
    elif media_type in ("application/vnd.pypi.simple.v1+html", "text/html"):
        page = pypi_simple.IndexPage.from_html(html=body, from_encoding=params.get("charset"))
    else:
        raise pypi_simple.errors.UnsupportedContentTypeError(url, media_type)
    if page.last_serial is None:
        page.last_serial = last_serial
    return page


def _parse_project_page(
    project: str,
    content_type: str,
    body: bytes,
    url: str,
    last_serial: str | None,
) -> pypi_simple.ProjectPage:
    media_type, params = _parse_content_type(content_type)
    if media_type == "application/vnd.pypi.simple.v1+json":
        page = pypi_simple.ProjectPage.from_json_data(json.loads(body), url)
    elif media_type in ("application/vnd.pypi.simple.v1+html", "text/html"):
        page = pypi_simple.ProjectPage.from_html(
            project=project,
            html=body,
            base_url=url,
            from_encoding=params.get("charset"),
        )
    else:
        raise pypi_simple.errors.UnsupportedContentTypeError(url, media_type)
    if page.last_serial is None:
        page.last_serial = last_serial
    return page


class AsyncPyPISimple:
    """Async client for the PyPI Simple Repository API (PEP 503/691).

    Uses ``aiohttp.ClientSession`` for HTTP access.  Prefers JSON
    responses (``ACCEPT_JSON_PREFERRED``) for faster parsing.

    Example::

        import aiohttp
        from retread._pypi import AsyncPyPISimple

        async with aiohttp.ClientSession() as session:
            client = AsyncPyPISimple(session)
            page = await client.get_project_page("requests")
            for pkg in page.packages:
                print(pkg.filename)
    """

    def __init__(
        self,
        session: ClientSession,
        endpoint: str = pypi_simple.PYPI_SIMPLE_ENDPOINT,
        accept: str = pypi_simple.ACCEPT_JSON_PREFERRED,
    ) -> None:
        self.session = session
        self.endpoint = endpoint.rstrip("/") + "/"
        self.accept = accept

    def get_project_url(self, project: str) -> str:
        """Return the Simple API URL for a project."""
        return self.endpoint + packaging.utils.canonicalize_name(project) + "/"

    async def get_index_page(self, accept: str | None = None) -> pypi_simple.IndexPage:
        """Fetch the Simple API index page listing all projects."""
        headers = {"Accept": accept or self.accept}
        async with self.session.get(self.endpoint, headers=headers) as resp:
            resp.raise_for_status()
            body = await resp.read()
            ct = resp.headers.get("content-type", "text/html")
            serial = resp.headers.get("X-PyPI-Last-Serial")
            return _parse_index_page(ct, body, str(resp.url), serial)

    async def get_project_page(
        self, project: str, accept: str | None = None
    ) -> pypi_simple.ProjectPage:
        """Fetch the Simple API page for a single project."""
        url = self.get_project_url(project)
        headers = {"Accept": accept or self.accept}
        async with self.session.get(url, headers=headers) as resp:
            if resp.status == 404:
                raise pypi_simple.errors.NoSuchProjectError(project, url)
            resp.raise_for_status()
            body = await resp.read()
            ct = resp.headers.get("content-type", "text/html")
            serial = resp.headers.get("X-PyPI-Last-Serial")
            return _parse_project_page(project, ct, body, str(resp.url), serial)

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
        async with self.session.get(url) as resp:
            if resp.status == 404:
                raise pypi_simple.errors.NoMetadataError(pkg.filename, url)
            resp.raise_for_status()
            body = await resp.read()
            checker.update(body)
            checker.finalize()
            return body

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
        async with self.session.get(url) as resp:
            if resp.status == 404:
                raise pypi_simple.errors.NoProvenanceError(pkg.filename, url)
            resp.raise_for_status()
            return json.loads(await resp.read())

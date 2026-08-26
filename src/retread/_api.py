"""High-level API for comparing downstream wheel rebuilds.

Orchestrates wheel resolution, backend management, and comparison.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
import typing

import pypi_simple

from retread._compare import (
    WheelComparison,
    _is_url,
    _wheel_basename,
    async_compare_local_wheel,
    async_compare_wheels,
    compare_local_wheel,
    compare_wheels,
)
from retread._errors import ComparisonError, RetreadError
from retread._resolve import find_matching_wheel, parse_wheel_spec

if typing.TYPE_CHECKING:
    from retread._types import AsyncBackend, SyncBackend

logger = logging.getLogger(__name__)

PYPI_SIMPLE_ENDPOINT = pypi_simple.PYPI_SIMPLE_ENDPOINT


def sync_retread(
    downstream: str | pathlib.Path,
    *,
    downstream_index: str | None = None,
    upstream_index: str = PYPI_SIMPLE_ENDPOINT,
    backend: SyncBackend | None = None,
) -> WheelComparison:
    """Compare a downstream wheel against its upstream source.

    The downstream wheel can be specified as:
    - A URL (``http://`` or ``https://``)
    - A local file path (``pathlib.Path`` or string path to an existing file)
    - A wheel filename (requires ``downstream_index``)

    The upstream wheel is automatically resolved from ``upstream_index``
    by matching the wheel filename (name, version, tags).

    Args:
        downstream: URL, local path, or wheel filename.
        downstream_index: Simple API index URL to resolve a downstream filename.
        upstream_index: Simple API index URL for the upstream wheel.
        backend: A sync HTTP backend. If ``None``, a
            :class:`~retread.backends.RequestsBackend` is created.

    Returns:
        A :class:`WheelComparison` result.
    """
    # SyncRemoteZip (not SyncRemoteWheel) -- retread compares all files
    # via infolist(), not just dist-info. The adaptive tail fetch in
    # RemoteWheel would download more data upfront for no benefit when
    # wheels are identical (the common case).
    from zipwire import SyncRemoteZip

    from retread.backends import RequestsBackend

    owns_backend = backend is None
    if backend is None:
        backend = RequestsBackend()

    try:
        downstream_str = str(downstream)
        downstream_filename = _wheel_basename(downstream_str)
        spec = parse_wheel_spec(downstream_filename)
        logger.info("Parsed wheel spec: %s", spec)

        # Resolve upstream wheel
        pypi = backend.pypi_client(endpoint=upstream_index)
        upstream_page = pypi.get_project_page(str(spec.name))
        upstream_pkg = find_matching_wheel(upstream_page, spec, index=upstream_index)
        upstream_url = upstream_pkg.url
        logger.info("Upstream wheel: %s", upstream_url)

        # Determine downstream source type
        if downstream_index is not None:
            # Filename -> resolve from downstream index
            ds_pypi = backend.pypi_client(endpoint=downstream_index)
            ds_page = ds_pypi.get_project_page(str(spec.name))
            ds_pkg = find_matching_wheel(ds_page, spec, index=downstream_index)
            downstream_source = ds_pkg.url
            is_local = False
        elif _is_url(downstream_str):
            downstream_source = downstream_str
            is_local = False
        else:
            downstream_source = str(downstream)
            is_local = True

        # Open upstream and compare
        with SyncRemoteZip(backend.wheel_reader(upstream_url)) as upstream_zip:
            if is_local:
                return compare_local_wheel(upstream_zip, pathlib.Path(downstream_source))
            else:
                with SyncRemoteZip(backend.wheel_reader(downstream_source)) as downstream_zip:
                    return compare_wheels(upstream_zip, downstream_zip)
    except RetreadError:
        raise
    except Exception as exc:
        raise ComparisonError(str(exc)) from exc
    finally:
        if owns_backend:
            backend.close()


async def async_retread(
    downstream: str | pathlib.Path,
    *,
    downstream_index: str | None = None,
    upstream_index: str = PYPI_SIMPLE_ENDPOINT,
    backend: AsyncBackend | None = None,
) -> WheelComparison:
    """Async version of :func:`sync_retread`.

    Args:
        downstream: URL, local path, or wheel filename.
        downstream_index: Simple API index URL to resolve a downstream filename.
        upstream_index: Simple API index URL for the upstream wheel.
        backend: An async HTTP backend. If ``None``, an
            :class:`~retread.backends.AiohttpBackend` is created.

    Returns:
        A :class:`WheelComparison` result.
    """
    # See comment in sync_retread for why RemoteZip, not RemoteWheel.
    from zipwire import AsyncRemoteZip

    from retread.backends import AiohttpBackend

    owns_backend = backend is None
    if backend is None:
        backend = AiohttpBackend()

    try:
        downstream_str = str(downstream)
        downstream_filename = _wheel_basename(downstream_str)
        spec = parse_wheel_spec(downstream_filename)
        logger.info("Parsed wheel spec: %s", spec)

        # Resolve upstream wheel (and downstream index in parallel if needed)
        pypi = backend.pypi_client(endpoint=upstream_index)
        if downstream_index is not None:
            ds_pypi = backend.pypi_client(endpoint=downstream_index)
            upstream_page, ds_page = await asyncio.gather(
                pypi.get_project_page(str(spec.name)),
                ds_pypi.get_project_page(str(spec.name)),
            )
        else:
            upstream_page = await pypi.get_project_page(str(spec.name))
        upstream_pkg = find_matching_wheel(upstream_page, spec, index=upstream_index)
        upstream_url = upstream_pkg.url
        logger.info("Upstream wheel: %s", upstream_url)

        # Determine downstream source type
        if downstream_index is not None:
            # Filename -> resolve from downstream index
            ds_pkg = find_matching_wheel(ds_page, spec, index=downstream_index)
            downstream_source = ds_pkg.url
            is_local = False
        elif _is_url(downstream_str):
            downstream_source = downstream_str
            is_local = False
        else:
            downstream_source = str(downstream)
            is_local = True

        # Open upstream and compare
        async with AsyncRemoteZip(backend.wheel_reader(upstream_url)) as upstream_zip:
            if is_local:
                return await async_compare_local_wheel(
                    upstream_zip, pathlib.Path(downstream_source)
                )
            else:
                async with AsyncRemoteZip(
                    backend.wheel_reader(downstream_source)
                ) as downstream_zip:
                    return await async_compare_wheels(upstream_zip, downstream_zip)
    except RetreadError:
        raise
    except Exception as exc:
        raise ComparisonError(str(exc)) from exc
    finally:
        if owns_backend:
            await backend.close()

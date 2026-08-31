"""High-level API for comparing downstream wheel rebuilds.

Orchestrates wheel resolution, backend management, and comparison.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import typing

import pypi_simple

from retread._context import Context
from retread._errors import ComparisonError, ProjectNotFoundError, RetreadError
from retread._findings import ResolutionMismatch
from retread._policy import lookup_policy
from retread._resolve import (
    Resolution,
    ResolutionStatus,
    _is_url,
    _wheel_basename,
    _wheel_tag_string,
    find_matching_wheel,
    parse_wheel_spec,
    resolve_upstream,
)
from retread._types import Filename, Url
from retread._wheel import WheelInfo, WheelSource
from retread.checker import compare

if typing.TYPE_CHECKING:
    import pathlib

    import pypi_simple as pypi_simple_types

    from retread._backend import AsyncBackend, SyncBackend
    from retread._findings import Comparison
    from retread._policy import PackagePolicy
    from retread._resolve import WheelSpec

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class _Resolved:
    """A resolved wheel to load: where to open it plus its index metadata."""

    source: str  # URL or local path to open
    is_local: bool
    origin: WheelSource
    resolution: Resolution | None = None  # upstream only; set when index-resolved


PYPI_SIMPLE_ENDPOINT = pypi_simple.PYPI_SIMPLE_ENDPOINT


def _sync_reader(backend: SyncBackend, source: str, is_local: bool) -> typing.Any:
    """Build a sync reader for *source*: a local FileReader or an HTTP reader."""
    if is_local:
        from zipwire.backends import FileReader

        return FileReader(source)
    return backend.wheel_reader(source)


def _async_reader(backend: AsyncBackend, source: str, is_local: bool) -> typing.Any:
    """Build an async reader for *source*: a local AsyncFileReader or HTTP reader."""
    if is_local:
        from zipwire.backends import AsyncFileReader

        return AsyncFileReader(source)
    return backend.wheel_reader(source)


def _resolve_project_not_found(exc: Exception, spec: WheelSpec, index: str) -> Exception:
    """Map a pypi_simple ``NoSuchProjectError`` to a retread error.

    Returns the original exception unchanged if it is not a missing-project
    error, so the caller's generic handling still applies.
    """
    if isinstance(exc, pypi_simple.NoSuchProjectError):
        return ProjectNotFoundError(spec.filename, index, str(spec.name))
    return exc


def _upstream_resolved(
    upstream_page: pypi_simple_types.ProjectPage, spec: WheelSpec, upstream_index: str
) -> _Resolved:
    """Resolve the upstream wheel from its project page, keeping the outcome."""
    resolution = resolve_upstream(upstream_page, spec, index=upstream_index)
    return _Resolved(
        source=resolution.package.url,
        is_local=False,
        origin=WheelSource.from_package(resolution.package, upstream_page),
        resolution=resolution,
    )


def _resolution_mismatch(
    resolution: Resolution | None, downstream_filename: str
) -> ResolutionMismatch | None:
    """Build a :class:`ResolutionMismatch` for a fallback resolution, else ``None``."""
    if resolution is None or resolution.status is not ResolutionStatus.FALLBACK:
        return None
    downstream_tags = (_wheel_tag_string(downstream_filename),)
    listed = ", ".join(resolution.available_tags) or "none"
    message = (
        f"no upstream wheel matches the downstream tags "
        f"({downstream_tags[0]}); compared against {resolution.package.filename}. "
        f"Available upstream wheel tags for {resolution.spec.version}: {listed}"
    )
    return ResolutionMismatch(
        message=message,
        downstream_tags=downstream_tags,
        upstream_tags=resolution.available_tags,
    )


def _apply_resolution_mismatch(result: Comparison, resolution: Resolution | None) -> None:
    """Append a fallback :class:`ResolutionMismatch` to *result*'s analysis, if any.

    When the package's policy sets ``allow_cross_platform``, the mismatch is
    marked ignored so the cross-platform comparison (e.g. a downstream Linux
    wheel validated against an upstream Windows or macOS wheel) is reported but
    not treated as an error.
    """
    mismatch = _resolution_mismatch(resolution, result.downstream.filename)
    if mismatch is None:
        return
    policy_map = result.context.policy
    if policy_map:
        version_policy = lookup_policy(
            policy_map, result.upstream.canonical, str(result.upstream.version)
        )
        if version_policy is not None and version_policy.allow_cross_platform:
            mismatch.ignored = True
    result.analysis.resolution_mismatches.append(mismatch)


def _log_fallback(resolution: Resolution | None) -> None:
    """Warn (for the diff path, which has no report) when a fallback wheel is used."""
    if resolution is not None and resolution.status is ResolutionStatus.FALLBACK:
        logger.warning(
            "No upstream wheel matches the downstream tags; diffing against fallback %s",
            resolution.package.filename,
        )


def _local_downstream(downstream_str: str) -> _Resolved:
    """Resolve a downstream wheel when no downstream index is given.

    A URL is used as-is (remote); anything else is treated as a local file
    path.  Either way no index metadata is available, so the origin is
    source-only.
    """
    is_local = not _is_url(downstream_str)
    source: Url | Filename = Filename(downstream_str) if is_local else Url(downstream_str)
    return _Resolved(source=downstream_str, is_local=is_local, origin=WheelSource.local(source))


def _resolve_wheels(
    downstream: str | pathlib.Path,
    backend: SyncBackend,
    *,
    downstream_index: str | None = None,
    upstream_index: str = PYPI_SIMPLE_ENDPOINT,
) -> tuple[_Resolved, _Resolved]:
    """Resolve the upstream and downstream wheels to load.

    Returns ``(upstream, downstream)`` as :class:`_Resolved` objects.
    """
    downstream_str = str(downstream)
    downstream_filename = _wheel_basename(downstream_str)
    spec = parse_wheel_spec(downstream_filename)
    logger.info("Parsed wheel spec: %s", spec)

    # Resolve upstream wheel
    pypi = backend.pypi_client(endpoint=upstream_index)
    try:
        upstream_page = pypi.get_project_page(str(spec.name))
    except Exception as exc:
        raise _resolve_project_not_found(exc, spec, upstream_index) from exc
    upstream = _upstream_resolved(upstream_page, spec, upstream_index)
    logger.info("Upstream wheel: %s", upstream.source)

    # Determine downstream source type
    if downstream_index is not None:
        ds_pypi = backend.pypi_client(endpoint=downstream_index)
        try:
            ds_page = ds_pypi.get_project_page(str(spec.name))
        except Exception as exc:
            raise _resolve_project_not_found(exc, spec, downstream_index) from exc
        ds_pkg = find_matching_wheel(ds_page, spec, index=downstream_index)
        downstream_resolved = _Resolved(
            source=ds_pkg.url,
            is_local=False,
            origin=WheelSource.from_package(ds_pkg, ds_page),
        )
    else:
        downstream_resolved = _local_downstream(downstream_str)

    return upstream, downstream_resolved


async def _async_resolve_wheels(
    downstream: str | pathlib.Path,
    backend: AsyncBackend,
    *,
    downstream_index: str | None = None,
    upstream_index: str = PYPI_SIMPLE_ENDPOINT,
) -> tuple[_Resolved, _Resolved]:
    """Async version of :func:`_resolve_wheels`."""
    downstream_str = str(downstream)
    downstream_filename = _wheel_basename(downstream_str)
    spec = parse_wheel_spec(downstream_filename)
    logger.info("Parsed wheel spec: %s", spec)

    pypi = backend.pypi_client(endpoint=upstream_index)
    try:
        if downstream_index is not None:
            ds_pypi = backend.pypi_client(endpoint=downstream_index)
            upstream_page, ds_page = await asyncio.gather(
                pypi.get_project_page(str(spec.name)),
                ds_pypi.get_project_page(str(spec.name)),
            )
        else:
            upstream_page = await pypi.get_project_page(str(spec.name))
    except Exception as exc:
        # Both pages are for the same project name; map a missing project to a
        # retread error against whichever index reported it.
        if isinstance(exc, pypi_simple.NoSuchProjectError):
            from_downstream = exc.url.startswith(str(downstream_index))
            index = downstream_index if from_downstream else upstream_index
            raise ProjectNotFoundError(spec.filename, index, str(spec.name)) from exc
        raise
    upstream = _upstream_resolved(upstream_page, spec, upstream_index)
    logger.info("Upstream wheel: %s", upstream.source)

    if downstream_index is not None:
        ds_pkg = find_matching_wheel(ds_page, spec, index=downstream_index)
        downstream_resolved = _Resolved(
            source=ds_pkg.url,
            is_local=False,
            origin=WheelSource.from_package(ds_pkg, ds_page),
        )
    else:
        downstream_resolved = _local_downstream(downstream_str)

    return upstream, downstream_resolved


def sync_retread(
    downstream: str | pathlib.Path,
    *,
    downstream_index: str | None = None,
    upstream_index: str = PYPI_SIMPLE_ENDPOINT,
    backend: SyncBackend | None = None,
    policy: dict[str, PackagePolicy] | None = None,
) -> Comparison:
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
        A :class:`~retread.Comparison` result.
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
        up, down = _resolve_wheels(
            downstream,
            backend,
            downstream_index=downstream_index,
            upstream_index=upstream_index,
        )

        # Load both wheels, then compare.  Local wheels flow through the same
        # SyncRemoteZip loader as remote ones via a FileReader.
        with (
            SyncRemoteZip(backend.wheel_reader(up.source)) as upstream_zip,
            SyncRemoteZip(_sync_reader(backend, down.source, down.is_local)) as downstream_zip,
        ):
            upstream_info = WheelInfo.from_sync_remote(upstream_zip, origin=up.origin)
            downstream_info = WheelInfo.from_sync_remote(downstream_zip, origin=down.origin)
        context = Context.default(policy=policy)
        result = compare(context, upstream_info, downstream_info)
        _apply_resolution_mismatch(result, up.resolution)
        return result
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
    policy: dict[str, PackagePolicy] | None = None,
) -> Comparison:
    """Async version of :func:`sync_retread`.

    Args:
        downstream: URL, local path, or wheel filename.
        downstream_index: Simple API index URL to resolve a downstream filename.
        upstream_index: Simple API index URL for the upstream wheel.
        backend: An async HTTP backend. If ``None``, an
            :class:`~retread.backends.AiohttpBackend` is created.

    Returns:
        A :class:`~retread.Comparison` result.
    """
    # See comment in sync_retread for why RemoteZip, not RemoteWheel.
    from zipwire import AsyncRemoteZip

    from retread.backends import AiohttpBackend

    owns_backend = backend is None
    if backend is None:
        backend = AiohttpBackend()

    try:
        up, down = await _async_resolve_wheels(
            downstream,
            backend,
            downstream_index=downstream_index,
            upstream_index=upstream_index,
        )

        # Load both wheels, then compare.  Local wheels flow through the same
        # AsyncRemoteZip loader as remote ones via an AsyncFileReader.
        async with (
            AsyncRemoteZip(backend.wheel_reader(up.source)) as upstream_zip,
            AsyncRemoteZip(_async_reader(backend, down.source, down.is_local)) as downstream_zip,
        ):
            upstream_info = await WheelInfo.from_async_remote(upstream_zip, origin=up.origin)
            downstream_info = await WheelInfo.from_async_remote(downstream_zip, origin=down.origin)
        context = Context.default(policy=policy)
        result = compare(context, upstream_info, downstream_info)
        _apply_resolution_mismatch(result, up.resolution)
        return result
    except RetreadError:
        raise
    except Exception as exc:
        raise ComparisonError(str(exc)) from exc
    finally:
        if owns_backend:
            await backend.close()


def sync_diff(
    downstream: str | pathlib.Path,
    files: list[str],
    *,
    downstream_index: str | None = None,
    upstream_index: str = PYPI_SIMPLE_ENDPOINT,
    backend: SyncBackend | None = None,
) -> list[tuple[str, bytes | None, bytes | None]]:
    """Extract files from both wheels and return their contents for diffing.

    Args:
        downstream: URL, local path, or wheel filename.
        files: List of filenames (paths inside the wheel) to diff.
        downstream_index: Simple API index URL to resolve a downstream filename.
        upstream_index: Simple API index URL for the upstream wheel.
        backend: A sync HTTP backend. If ``None``, a
            :class:`~retread.backends.RequestsBackend` is created.

    Returns:
        A list of ``(filename, upstream_bytes, downstream_bytes)`` tuples.
        ``None`` means the file does not exist on that side.
    """
    from zipwire import SyncRemoteZip

    from retread.backends import RequestsBackend

    owns_backend = backend is None
    if backend is None:
        backend = RequestsBackend()

    try:
        up, down = _resolve_wheels(
            downstream,
            backend,
            downstream_index=downstream_index,
            upstream_index=upstream_index,
        )
        _log_fallback(up.resolution)

        with (
            SyncRemoteZip(backend.wheel_reader(up.source)) as upstream_zip,
            SyncRemoteZip(_sync_reader(backend, down.source, down.is_local)) as downstream_zip,
        ):
            upstream_names = {info.filename for info in upstream_zip.infolist()}
            downstream_names = {info.filename for info in downstream_zip.infolist()}
            result: list[tuple[str, bytes | None, bytes | None]] = []
            for fname in files:
                up_bytes = upstream_zip.read(fname) if fname in upstream_names else None
                down_bytes = downstream_zip.read(fname) if fname in downstream_names else None
                result.append((fname, up_bytes, down_bytes))
            return result
    except RetreadError:
        raise
    except Exception as exc:
        raise ComparisonError(str(exc)) from exc
    finally:
        if owns_backend:
            backend.close()


async def async_diff(
    downstream: str | pathlib.Path,
    files: list[str],
    *,
    downstream_index: str | None = None,
    upstream_index: str = PYPI_SIMPLE_ENDPOINT,
    backend: AsyncBackend | None = None,
) -> list[tuple[str, bytes | None, bytes | None]]:
    """Async version of :func:`sync_diff`."""
    from zipwire import AsyncRemoteZip

    from retread.backends import AiohttpBackend

    owns_backend = backend is None
    if backend is None:
        backend = AiohttpBackend()

    try:
        up, down = await _async_resolve_wheels(
            downstream,
            backend,
            downstream_index=downstream_index,
            upstream_index=upstream_index,
        )
        _log_fallback(up.resolution)

        async with (
            AsyncRemoteZip(backend.wheel_reader(up.source)) as upstream_zip,
            AsyncRemoteZip(_async_reader(backend, down.source, down.is_local)) as downstream_zip,
        ):
            upstream_names = {info.filename for info in upstream_zip.infolist()}
            downstream_names = {info.filename for info in downstream_zip.infolist()}
            result: list[tuple[str, bytes | None, bytes | None]] = []
            for fname in files:
                up_bytes = (await upstream_zip.read(fname)) if fname in upstream_names else None
                down_bytes = (
                    (await downstream_zip.read(fname)) if fname in downstream_names else None
                )
                result.append((fname, up_bytes, down_bytes))
            return result
    except RetreadError:
        raise
    except Exception as exc:
        raise ComparisonError(str(exc)) from exc
    finally:
        if owns_backend:
            await backend.close()

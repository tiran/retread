"""Backend adapters with lazy imports.

No backend library is imported until its class is accessed.

Usage::

    from retread.backends import AiohttpBackend

    async with AiohttpBackend() as backend:
        pypi = backend.pypi_client()
        page = await pypi.get_project_page("requests")
"""

import importlib
import typing

if typing.TYPE_CHECKING:
    from retread.backends._aiohttp import AiohttpBackend as AiohttpBackend
    from retread.backends._httpx2 import Httpx2Backend as Httpx2Backend
    from retread.backends._httpx2 import Httpx2SyncBackend as Httpx2SyncBackend
    from retread.backends._requests import RequestsBackend as RequestsBackend

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "AiohttpBackend": ("retread.backends._aiohttp", "AiohttpBackend"),
    "Httpx2Backend": ("retread.backends._httpx2", "Httpx2Backend"),
    "Httpx2SyncBackend": ("retread.backends._httpx2", "Httpx2SyncBackend"),
    "RequestsBackend": ("retread.backends._requests", "RequestsBackend"),
}

__all__ = list(_LAZY_IMPORTS)


def __getattr__(name: str) -> object:
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        module = importlib.import_module(module_path)
        return getattr(module, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return __all__

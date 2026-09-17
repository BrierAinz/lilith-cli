"""Optional web-console surface.

The base CLI must remain importable without the ``web`` extra.  Keep the
FastAPI-backed application factory lazy so commands such as ``lilith --help``
do not require optional web dependencies.
"""

from __future__ import annotations

from typing import Any


def create_app(*args: Any, **kwargs: Any) -> Any:
    """Create the FastAPI application when the optional web stack is installed."""
    from .server import create_app as _create_app

    return _create_app(*args, **kwargs)


__all__ = ["create_app"]

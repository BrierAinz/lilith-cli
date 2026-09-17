"""Optional local web console for the canonical Lilith runtime."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path


def _loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def web(
    root: str = ".",
    host: str = "127.0.0.1",
    port: int = 12356,
    dev: bool = False,
    config: str | None = None,
) -> None:
    """Abrir la consola web local de Lilith para un workspace."""
    workspace = Path(root).expanduser().resolve()
    if not workspace.is_dir():
        raise SystemExit(f"Workspace inexistente: {workspace}")
    token = os.environ.get("LILITH_AUTH_TOKEN")
    if not token and not _loopback(host):
        raise SystemExit("Un bind no-loopback requiere LILITH_AUTH_TOKEN.")
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "Falta el extra web. Ejecuta: uv sync --package lilith-cli --extra web"
        ) from exc

    from .server import create_app

    origins = [
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
        f"http://[::1]:{port}",
    ]
    app = create_app(
        workspace=str(workspace),
        auth_token=token,
        allowed_origins=origins,
        config_path=config,
    )
    uvicorn.run(
        app,
        host=host,
        port=int(port),
        reload=bool(dev),
        log_level="info",
    )

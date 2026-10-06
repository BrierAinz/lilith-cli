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


def _print_access_hint(host: str, port: int, has_token: bool) -> None:
    from .server import FRONTEND_DIST

    url = f"http://{host}:{port}/"
    if not (FRONTEND_DIST / "index.html").is_file():
        print(
            f"API en {url}api — la interfaz no está compilada: "
            "ejecuta `npm ci && npm run build` en lilith_cli/web_console/frontend."
        )
    elif has_token:
        print(f"Abre {url}#token=<LILITH_AUTH_TOKEN> (el fragmento no llega al servidor).")
    else:
        print(f"Abre {url}")


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
    if dev:
        # uvicorn only reloads an app given as an import string, so the
        # settings reach the reloaded worker through the environment.
        os.environ["LILITH_WEB_WORKSPACE"] = str(workspace)
        os.environ["LILITH_WEB_ORIGINS"] = ",".join(origins)
        if config:
            os.environ["LILITH_WEB_CONFIG"] = config
        else:
            os.environ.pop("LILITH_WEB_CONFIG", None)
        uvicorn.run(
            "lilith_cli.web_console.server:create_app_from_env",
            factory=True,
            host=host,
            port=int(port),
            reload=True,
            reload_dirs=[str(Path(__file__).resolve().parents[1])],
            log_level="info",
        )
        return

    app = create_app(
        workspace=str(workspace),
        auth_token=token,
        allowed_origins=origins,
        config_path=config,
    )
    _print_access_hint(host, port, bool(token))
    uvicorn.run(
        app,
        host=host,
        port=int(port),
        log_level="info",
    )

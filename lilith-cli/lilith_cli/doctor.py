"""Diagnostic helpers for the /doctor slash command."""

from __future__ import annotations

import contextlib
import platform
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from .agent import AgentSession


def run_diagnostics(session: AgentSession) -> list[dict[str, str]]:
    """Run all Lilith installation checks and return a list of results.

    Each result has keys ``check``, ``status`` ("ok" | "warn" | "error") and
    ``message``.  The checks are intentionally side-effect free so they can be
    run in tests without modifying the filesystem.
    """
    results: list[dict[str, str]] = []
    results.append(_check_python_version())
    results.append(_check_api_key(session.config))
    results.append(_check_tool_registry(session))
    results.append(_check_undo_dir())
    results.append(_check_config_file())
    return results


def _check_python_version() -> dict[str, str]:
    """Check whether the Python version is supported."""
    major, minor = sys.version_info[:2]
    version_str = f"{major}.{minor}.{sys.version_info.micro}"
    if major == 3 and minor >= 10:
        return {
            "check": "Python",
            "status": "ok",
            "message": f"Python {version_str} (compatible)",
        }
    return {
        "check": "Python",
        "status": "error",
        "message": f"Python {version_str} — se requiere 3.10+",
    }


def _check_api_key(config: Any) -> dict[str, str]:
    """Check whether an API key is configured for the active provider."""
    provider = config.provider
    api_key = config.api_key

    if api_key and len(api_key) >= 8:
        masked = api_key[:4] + "..." + api_key[-4:]
        return {
            "check": "API key",
            "status": "ok",
            "message": f"API key configurada para {provider} ({masked})",
        }

    profile = config.providers.get(provider.lower()) if config.providers else None
    profile_key = getattr(profile, "api_key", None) if profile else None
    if profile_key and len(profile_key) >= 8:
        masked = profile_key[:4] + "..." + profile_key[-4:]
        return {
            "check": "API key",
            "status": "ok",
            "message": f"API key en perfil '{provider}' ({masked})",
        }

    return {
        "check": "API key",
        "status": "error",
        "message": f"Sin API key para el proveedor '{provider}'. "
        f"Configurá ${provider.upper()}_API_KEY o el perfil en ~/.yggdrasil/config.yaml",
    }


def _check_tool_registry(session: AgentSession) -> dict[str, str]:
    """Check whether the tool registry is loaded and non-empty."""
    try:
        all_tools = session._all_tool_names()
        enabled = session.get_tool_descriptions()
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "check": "Tool registry",
            "status": "error",
            "message": f"No se pudo cargar el registro de herramientas: {exc}",
        }

    if not all_tools:
        return {
            "check": "Tool registry",
            "status": "error",
            "message": "lilith_tools no disponible — ninguna herramienta registrada",
        }

    disabled_count = len(all_tools) - len(enabled)
    if disabled_count:
        return {
            "check": "Tool registry",
            "status": "warn",
            "message": f"{len(enabled)} de {len(all_tools)} herramientas habilitadas "
            f"({disabled_count} deshabilitadas por config o /tools disable)",
        }
    return {
        "check": "Tool registry",
        "status": "ok",
        "message": f"{len(enabled)} herramientas cargadas",
    }


def _check_undo_dir() -> dict[str, str]:
    """Check whether the undo directory exists and is writable."""
    undo_dir = Path("~/.yggdrasil/undo").expanduser()
    if not undo_dir.exists():
        return {
            "check": "Undo dir",
            "status": "warn",
            "message": f"El directorio de backups no existe: {undo_dir}",
        }
    try:
        test_file = undo_dir / ".doctor_write_test"
        with test_file.open("w") as f:
            f.write("ok")
        test_file.unlink()
        return {
            "check": "Undo dir",
            "status": "ok",
            "message": f"Directorio de backups escribible: {undo_dir}",
        }
    except OSError as exc:
        return {
            "check": "Undo dir",
            "status": "error",
            "message": f"No se puede escribir en {undo_dir}: {exc}",
        }


def _check_config_file() -> dict[str, str]:
    """Check whether the global config file exists and is valid YAML."""
    from .config import CONFIG_FILE

    if not CONFIG_FILE.exists():
        return {
            "check": "Config file",
            "status": "warn",
            "message": f"No existe el archivo de configuración: {CONFIG_FILE}",
        }

    try:
        import yaml

        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return {
                "check": "Config file",
                "status": "error",
                "message": f"El config no es un diccionario YAML válido: {CONFIG_FILE}",
            }
        return {
            "check": "Config file",
            "status": "ok",
            "message": f"Configuración válida: {CONFIG_FILE}",
        }
    except Exception as exc:
        return {
            "check": "Config file",
            "status": "error",
            "message": f"Error leyendo {CONFIG_FILE}: {exc}",
        }


def apply_fixes(results: list[dict[str, str]]) -> list[str]:
    """Attempt to fix common issues reported by diagnostics.

    Returns a list of human-readable fix messages.  Only safe, idempotent
    fixes are performed (create directories, write a default config).
    """
    from .config import CONFIG_FILE, _DEFAULT_CONFIG_YAML

    fixed: list[str] = []
    for result in results:
        if result["status"] == "ok":
            continue

        if result["check"] == "Undo dir" and result["status"] in ("warn", "error"):
            undo_dir = Path("~/.yggdrasil/undo").expanduser()
            try:
                undo_dir.mkdir(parents=True, exist_ok=True)
                test_file = undo_dir / ".doctor_write_test"
                with test_file.open("w") as f:
                    f.write("ok")
                test_file.unlink()
                fixed.append(f"Creado directorio de backups: {undo_dir}")
            except OSError as exc:
                fixed.append(f"No se pudo crear {undo_dir}: {exc}")

        if result["check"] == "Config file" and result["status"] == "warn":
            try:
                CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
                CONFIG_FILE.write_text(_DEFAULT_CONFIG_YAML, encoding="utf-8")
                fixed.append(f"Creado archivo de configuración por defecto: {CONFIG_FILE}")
            except OSError as exc:
                fixed.append(f"No se pudo crear {CONFIG_FILE}: {exc}")

    return fixed


__all__ = ["run_diagnostics", "apply_fixes"]

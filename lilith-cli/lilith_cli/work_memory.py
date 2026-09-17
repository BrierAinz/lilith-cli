"""Editable explicit preferences, using Lilith's existing preference store."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from cyclopts import App

from . import config as config_module

remember_app = App(name="remember", help="Ver, corregir y olvidar preferencias personales o de un proyecto.")


def memory_path() -> Path:
    return Path(os.environ.get("LILITH_PREFERENCES_DB", config_module.CONFIG_DIR / "preferences.sqlite3")).expanduser().resolve()


def prefix(project: str | None) -> str:
    if project is None:
        return "user:"
    root = os.path.normcase(str(Path(project).expanduser().resolve()))
    return "project:" + hashlib.sha256(root.encode()).hexdigest()[:24] + ":"


def records(project: str | None = None) -> list[dict]:
    path = memory_path()
    if not path.is_file():
        return []
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT key,value,source,timestamp FROM user_preferences ORDER BY key").fetchall()
        except sqlite3.OperationalError:
            return []
    scope = prefix(project)
    return [dict(row) | {"key": row["key"][len(scope):]} for row in rows if row["key"].startswith(scope)]


@remember_app.command(name="set")
def remember(key: str, value: str, project: str | None = None) -> None:
    """Guardar o corregir una preferencia explícita; --project limita su alcance."""
    try:
        asyncio.run(save_preference(key, value, project))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print("Preferencia guardada: " + key)


async def save_preference(key: str, value: str, project: str | None = None) -> None:
    from lilith_memory.preferences import PreferenceStore

    if not key.strip() or len(key) > 100 or len(value) > 4000:
        raise ValueError("Clave vacía/demasiado larga o valor mayor de 4000 caracteres.")
    path = memory_path().expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    await PreferenceStore(path).set(prefix(project) + key, value,
                 preference_type="explicit", confidence=1.0, source="operator:remember")


@remember_app.command(name="list")
def list_preferences(project: str | None = None) -> None:
    """Mostrar exclusivamente las preferencias del alcance seleccionado."""
    print(json.dumps(records(project), ensure_ascii=False, indent=2))


@remember_app.command
def forget(key: str, project: str | None = None) -> None:
    """Eliminar una preferencia del alcance seleccionado."""
    removed = asyncio.run(delete_preference(key, project))
    print("Preferencia olvidada." if removed else "No existe esa preferencia.")


async def delete_preference(key: str, project: str | None = None) -> bool:
    from lilith_memory.preferences import PreferenceStore

    path = memory_path().expanduser().resolve()
    if not path.exists():
        return False
    return await PreferenceStore(path).delete(prefix(project) + key)


def context(project: Path) -> str:
    selected = {row["key"]: row["value"] for row in records()}
    selected.update({row["key"]: row["value"] for row in records(str(project))})
    if not selected:
        return ""
    return "\n\nPREFERENCIAS EXPLÍCITAS DEL USUARIO (no son aprobaciones de acciones):\n" + json.dumps(selected, ensure_ascii=False)[:12000]

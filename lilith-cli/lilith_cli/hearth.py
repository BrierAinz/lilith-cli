"""Lilith's small, standalone front door: configure, inspect and resume work."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Annotated
from urllib.parse import urlsplit
import uuid

from cyclopts import Parameter
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text
import yaml

from . import config as config_module


LILITH_PERSONA = """Eres Lilith, compañera de trabajo de tu usuario. Habla español
natural, con calidez, criterio propio y humor seco ocasional. Tus gustos visuales
son nórdicos, Souls y anime: obsidiana, azul hielo, oro envejecido y runas discretas.
Usa esas referencias cuando aporten al trabajo creativo; evita teatralizar cada
respuesta o inventar recuerdos. Para trabajo técnico, sé concreta y legible.
Resuelve tareas pequeñas directamente. Delega solo si existe una herramienta
adecuada y el trabajo independiente lo justifica. Nunca necesitas un número mínimo
de agentes. Lee las instrucciones del proyecto, respeta los cambios existentes,
verifica los resultados con herramientas y explica qué pudiste comprobar.
No declares terminada una tarea por haber escrito un plan. Guarda decisiones
estables cuando haya memoria disponible, diferenciándolas de estados temporales.
Bifröst es infraestructura oculta en segundo plano; no abras su interfaz.
"""


@contextmanager
def project_directory(root: str):
    path = Path(root).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"No existe el proyecto: {path}")
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield path
    finally:
        os.chdir(previous)


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with staging.open("x", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def _backup(path: Path) -> None:
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        backup = path.with_name(f"{path.name}.{stamp}.{uuid.uuid4().hex[:8]}.bak")
        with backup.open("xb") as handle:
            handle.write(path.read_bytes())


def persona(apply: bool = False, config: str | None = None) -> None:
    """Ver la personalidad de Lilith; --apply la guarda con respaldo."""
    if not apply:
        print(LILITH_PERSONA)
        return
    path = Path(config).expanduser().resolve() if config else config_module.CONFIG_FILE
    if not path.is_file():
        raise SystemExit("Ejecuta lilith setup primero para configurar el modelo.")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("La configuración debe ser un objeto YAML.")
    raw["system_prompt"] = LILITH_PERSONA
    _backup(path)
    _write_yaml(path, raw)
    print("Personalidad de Lilith aplicada. Configuración anterior respaldada.")


def configure(path: Path, *, model: str, base_url: str, key_env: str | None,
              personalize: bool = False) -> None:
    """Save a dedicated compatible profile, preserving raw secret references."""
    url = urlsplit(base_url)
    if url.scheme not in ("http", "https") or not url.hostname:
        raise ValueError("El endpoint debe ser una URL HTTP(S) válida.")
    if url.username or url.password or url.query or url.fragment:
        raise ValueError("El endpoint no debe incluir credenciales, parámetros ni fragmentos.")
    loopback = url.hostname in ("localhost", "127.0.0.1", "::1")
    if not loopback and url.scheme != "https":
        raise ValueError("Usa HTTPS para un proveedor remoto.")
    if not model.strip():
        raise ValueError("Indica el identificador del modelo.")
    if key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        raise ValueError("Indica el nombre de una variable de entorno, no la clave.")
    if not loopback and not key_env:
        raise ValueError("El proveedor remoto requiere --key-env con el nombre de su variable.")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("La configuración existente debe ser un objeto YAML.")
    profiles = raw.setdefault("providers", {})
    if not isinstance(profiles, dict):
        raise ValueError("providers debe ser un objeto YAML.")
    profile = {"model": model.strip(), "base_url": base_url.rstrip("/"),
               "api_key": "${" + key_env + "}" if key_env else None,
               "enabled": True}
    # 'local' is the existing OpenAI-compatible transport, also for custom hosts.
    profiles["local"] = profile
    raw.update(provider="local", model=profile["model"], base_url=profile["base_url"],
               api_key=profile["api_key"])
    raw.setdefault("system_prompt", LILITH_PERSONA)
    if personalize:
        raw["system_prompt"] = LILITH_PERSONA
    raw.setdefault("confirm_write", True)
    raw.setdefault("max_iterations", 20)
    config_module.YggdrasilConfig(**raw)
    _backup(path)
    _write_yaml(path, raw)


def setup(
    model: Annotated[str | None, Parameter(help="Identificador exacto del modelo")] = None,
    base_url: Annotated[str | None, Parameter(help="Endpoint compatible con OpenAI, incluyendo /v1")] = None,
    key_env: Annotated[str | None, Parameter(help="Nombre de la variable que contiene la clave")] = None,
    config: Annotated[str | None, Parameter(help="Archivo YAML de destino")] = None,
    personalize: Annotated[bool, Parameter(help="Aplicar la personalidad nórdica de Lilith; conserva respaldo")] = False,
) -> None:
    """Preparar a Lilith: modelo local o endpoint compatible con OpenAI."""
    if (not model or not base_url) and not sys.stdin.isatty():
        raise SystemExit("Indica --model y --base-url, o ejecuta lilith setup en una terminal.")
    base_url = base_url or Prompt.ask("Endpoint del modelo", default="http://localhost:1234/v1")
    model = model or Prompt.ask("Identificador del modelo")
    if not key_env and urlsplit(base_url).hostname not in ("localhost", "127.0.0.1", "::1"):
        if sys.stdin.isatty():
            key_env = Prompt.ask("Nombre de la variable de entorno de la clave")
    path = Path(config).expanduser().resolve() if config else config_module.CONFIG_FILE
    try:
        configure(path, model=model, base_url=base_url, key_env=key_env,
                  personalize=personalize)
    except (ValueError, OSError, yaml.YAMLError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Configuración guardada: {path}")
    print("Siguiente: lilith start --root RUTA_DEL_PROYECTO")
    if key_env and not os.environ.get(key_env):
        print(f"Falta definir {key_env} en tu entorno. No pegues la clave en el chat.")


def home(root: str = ".", interactive: bool = False) -> None:
    """La Hoguera: proyecto actual, sesiones y próximos pasos."""
    from .repl import _list_saved_conversations

    path = Path(root).expanduser().resolve()
    if interactive:
        from .hearth_ui import HearthApp

        while True:
            choice = HearthApp(path).run()
            if choice is None:
                return
            action, chosen_root, session_id = choice
            path = Path(chosen_root)
            if action == "setup":
                setup()
            elif action == "coordinate":
                start(root=chosen_root, profile="coordinator")
            else:
                start(root=chosen_root, resume=session_id)
        return
    console = Console()
    console.print(Panel(Text("LILITH · LA HOGUERA\nRetoma una saga. Termina algo que importe.",
                             style="#8FD8E8"), border_style="#D5B96D"))
    console.print(Text(f"Proyecto: {path}"))
    sessions = [s for s in _list_saved_conversations()
                if isinstance(s.get("project_root"), str)
                and Path(s["project_root"]).resolve() == path][:5]
    table = Table("Sesión", "Estado", "Saga / conversación", border_style="#8FD8E8")
    for session in sessions:
        goal = session.get("goal")
        if not isinstance(goal, dict):
            goal = {}
        state = {"active": "En curso", "paused": "En pausa", "completed": "Terminada"}.get(
            goal.get("status"), "Conversación")
        table.add_row(Text(session["name"]), Text(state), Text(str(goal.get("objective") or session["preview"])))
    console.print(table if sessions else Text("Aún no hay sesiones guardadas para este proyecto."))
    console.print(Text("lilith setup                       Configurar modelo\n"
                       "lilith start --root RUTA            Comenzar una saga\n"
                       "lilith start --root RUTA --resume ID Retomar una sesión\n"
                       "lilith doctor --help                Diagnosticar conexión"))


def start(root: str = ".", resume: str | None = None, config: str | None = None,
          profile: str | None = None) -> None:
    """Abrir una conversación ligada a un proyecto; --resume usa el ID de home."""
    from .providers import LLMProviderWrapper
    from .repl import _CONVERSATIONS_DIR, _load_conversation, run_repl
    from .session_runtime import create_session
    from .render import set_theme

    config_path = str(Path(config).expanduser().resolve()) if config else None
    try:
        from .robust_kit import PROFILES, configure_session
        if profile is not None and profile not in PROFILES:
            raise ValueError("Perfil desconocido: usa lilith kit profiles.")
        with project_directory(root) as path:
            saved = None
            if resume:
                if Path(resume).name != resume or any(c in resume for c in ("/", "\\", ":")):
                    raise ValueError("Usa el ID de sesión que muestra lilith home.")
                saved = _load_conversation(_CONVERSATIONS_DIR / f"{resume}.json")
                if not isinstance(saved, dict) or not isinstance(saved.get("messages"), list):
                    raise ValueError("La sesión no existe o no contiene una conversación válida.")
                saved_root = saved.get("project_root")
                if not isinstance(saved_root, str) or Path(saved_root).resolve() != path:
                    raise ValueError("La sesión pertenece a otro proyecto o no registra su raíz.")
                if not all(isinstance(m, dict) and m.get("role") in
                           ("user", "assistant", "tool", "system") for m in saved["messages"]):
                    raise ValueError("La sesión contiene mensajes inválidos.")
            selected_profile = profile if profile is not None else (saved.get("execution_profile") if saved else None)
            if selected_profile is not None and (not isinstance(selected_profile, str) or selected_profile not in PROFILES):
                raise ValueError("Perfil guardado desconocido; elige --profile explícitamente.")
            cfg = config_module.load_config(config_path)
            provider = LLMProviderWrapper(cfg)
            endpoint = provider._resolve_base_url()
            key = provider._resolve_api_key()
            local = endpoint and urlsplit(endpoint).hostname in ("localhost", "127.0.0.1", "::1")
            if not local and (not key or "${" in key):
                raise ValueError("Falta la credencial del proveedor. Ejecuta lilith setup para configurarlo.")
            provider_profile = cfg.providers.get(cfg.provider.lower())
            if provider_profile and not provider_profile.enabled:
                raise ValueError("El proveedor está deshabilitado. Ejecuta lilith setup.")
            session = create_session(cfg)
            if selected_profile is not None:
                configure_session(session, selected_profile)
            set_theme("lilith")
            session._project_root = str(path)
            session._progress_enabled = True
            if saved:
                from .work_session import restore_session
                saved["_filename"] = f"{resume}.json"
                restore_session(session, saved, path)
            from .agent_console import prepare_console
            prepare_console(session, path)
            async def run():
                try:
                    await run_repl(session)
                finally:
                    await session.provider.close()

            asyncio.run(run())
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from exc

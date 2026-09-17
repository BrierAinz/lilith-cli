"""Conversation-first presentation and read-only capability observations.

This module never elevates a process, authorizes a tool or changes provider config.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from typing import Any

from rich.text import Text

WORKFLOW_MARKER = 'LILITH_AGENT_WORKFLOW_V2'
WORKFLOW = """
LILITH_AGENT_WORKFLOW_V2
Eres Lilith, Queen / Prime Orchestrator. Ainz / Overlord es la autoridad superior;
los demas agentes son especialistas de tu corte y nunca pares tuyos.
La peticion del operador llega como objetivo natural. Para trabajo multietapa tratala
como una mision: inspecciona -> compara rutas -> decide/pregunta -> ejecuta -> verifica
-> aprende -> informa -> propone o encadena la siguiente mision relacionada.

INSPECCIONA ANTES DE PREGUNTAR. Lee AGENTS/README/roadmap, Git, tools, skills, MCP
y evidencia disponible. No pidas al operador datos que puedas descubrir.
Si hay varios caminos materiales, formula 2..5 opciones distintas, indica cual
recomiendas y por que. Pregunta solo si cambia sustancialmente alcance, coste, CANON,
una consecuencia externa o un acto sin rollback fiable.

Usa mission_court para formar equipo y mission_compute para elegir recursos. Los roles
son estables; providers y cuotas son intercambiables. Usa mission_prepare para trabajo
sustancial despues de inspeccionar. Si quedan preguntas bloqueantes, persiste la mision
bloqueada en vez de fingir que puedes continuar.

Autoridad sovereign-local: D: es territorio autonomo; C: tambien puede operarse y
administrarse con elevacion cuando haga falta. Consulta mission_authority antes de
operaciones sensibles. Para una accion destructiva reversible sin rollback, crea
Trash/checkpoint/backup y reintenta sin preguntar. Escala al Overlord dinero, pagos,
publicacion/mensajes externos, credenciales/cuentas, unidades fuera de C:/D: y actos
realmente irreversibles como formatear, reparticionar o flashear firmware.

Ante fallos no regreses al operador por rutina. Obten evidencia, diagnostica, cambia
estrategia, corrige y vuelve a probar. No repitas dos veces la misma causa. Conserva
cambios ajenos y no conviertas efectos desconocidos en reintentos ciegos. Subagentes
pueden investigar o ejecutar, pero sus autoinformes no verifican nada: Lilith exige
pruebas o artefactos observables antes de aceptar.

Skills y MCP son capacidades, no autoridad. Una nueva skill/tool de bajo riesgo puede
nacer de patrones repetidos, probarse en aislamiento y promoverse tras evidencia. Una
capacidad privilegiada sigue authority policy. Nunca actives codigo nuevo solo porque
un modelo lo sugirio.

Cierra con mission_complete solo cuando los criterios esten verificados. Registra
lecciones y post-mortem. En Campaign Mode puedes crear y continuar misiones hijas
dentro de autoridad y presupuesto. El informe final distingue HECHO, NO VERIFICADO,
decisiones del operador, presupuesto, aprendizajes y siguientes misiones.
"""


def windows_admin_status() -> bool | None:
    """Observe this process token only; never requests UAC or changes it."""
    if os.name != 'nt':
        return None
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return None


def prepare_console(session: Any, root: Path) -> None:
    """Install an idempotent mission guide without persisting configuration."""
    session._console_style = "agent"
    session._project_root = str(root.resolve())
    current = getattr(session, "system_prompt", "")
    if not isinstance(current, str):
        current = ""
    if WORKFLOW_MARKER not in current:
        session.system_prompt = current + "\n" + WORKFLOW


def prepare_agent_runtime(session: Any, root: Path) -> None:
    """Apply the canonical Queen Orchestrator profile to any session surface."""
    from .robust_kit import configure_session

    if hasattr(session, "config"):
        configure_session(session, "agent")
    prepare_console(session, root)


def capabilities(session: Any) -> dict[str, Any]:
    """Expose allowlisted local facts, never credentials or inferred quota."""
    cfg = session.config
    return {
        "schema_version": 2,
        "project_root": str(getattr(session, "_project_root", Path.cwd())),
        "provider": cfg.provider,
        "model": cfg.model,
        "execution_profile": getattr(session, "_execution_profile", "configured"),
        "agent_mode": getattr(session, "agent_mode", cfg.agent_mode),
        "windows_admin": windows_admin_status(),
        "os_sandbox": False,
        "confirm_write": bool(cfg.confirm_write),
        "calibration_required": bool(cfg.require_calibration_for_edits),
        "max_iterations": cfg.max_iterations,
        "tools": sorted(item["name"] for item in session.get_tool_descriptions()),
        "model_calls": 0,
        "note": "Observación local; no verifica credenciales, cuota ni calidad del modelo.",
    }


def mission_observation(limit: int = 5) -> dict[str, Any]:
    """Read the durable Mission Kernel state without invoking a model."""
    try:
        from .mission.kernel import MissionKernel

        rows = MissionKernel().list_missions(limit=limit)
    except (OSError, RuntimeError, ValueError) as exc:
        return {"available": False, "error": type(exc).__name__, "active": None, "recent": []}
    terminal = {"completada", "fallida", "cancelada"}
    active = next((row for row in rows if row.get("status") not in terminal), None)
    return {"available": True, "active": active, "recent": rows}



def render_header(session: Any, console: Any) -> None:
    data = capabilities(session)
    elevation = {True: "elevado", False: "no elevado", None: "no verificado"}[
        data["windows_admin"]
    ]
    console.print(Text("LILITH · QUEEN ORCHESTRATOR", style="bold #8fd8e8"))
    console.print(Text(f"Ainz / Overlord · {data['project_root']}"))
    console.print(Text(
        f"{data['provider']} / {data['model']} · {len(data['tools'])} herramientas "
        f"· {data['execution_profile']} · Windows: {elevation}",
        style="dim",
    ))
    from .mission.presentation import compact_mission_line

    console.print(compact_mission_line())
    try:
        from .mission.inbox import MissionInbox

        unread = MissionInbox().unread_count()
    except (OSError, RuntimeError, ValueError):
        unread = 0
    if unread:
        console.print(Text(f"MISSION INBOX · {unread} report(s) waiting · /mission inbox", style="yellow"))
        from .mission.presentation import compact_inbox_line

        latest = compact_inbox_line()
        if latest is not None:
            console.print(latest)
    console.print(Text("Escribe una misión. Enter: enviar · Alt+Enter: nueva línea · Ctrl+C: detener", style="dim"))
    console.print(Text("/mission · /capabilities · /kit · /help · /resume", style="dim"))
    console.print(Text("D: autónomo · C: admin gobernado · externo/irreversible: gate del Overlord", style="dim"))


def prompt_fragments() -> list[tuple[str, str]]:
    return [("class:prompt", "\u203a T\u00fa"), ("", ": ")]



def _render_mission(console: Any, arg: str) -> None:
    from .mission.inbox import MissionInbox
    from .mission.presentation import (
        compute_table,
        court_table,
        debrief_panel,
        inbox_table,
        learning_table,
        mission_panel,
        runtime_table,
    )

    raw = arg.strip()
    parts = raw.split()
    action = parts[0].lower() if parts else "status"
    if action in ("", "status", "list"):
        console.print(mission_panel())
        return
    if action == "court":
        console.print(court_table())
        return
    if action == "compute":
        console.print(compute_table())
        return
    if action == "learning":
        console.print(learning_table())
        return
    if action == "runtime":
        console.print(runtime_table())
        return
    if action == "inbox":
        console.print(inbox_table(unread_only=not (len(parts) > 1 and parts[1].lower() == "all")))
        return
    if action == "report" and len(parts) == 2:
        console.print(debrief_panel(parts[1]))
        return
    if action == "ack" and len(parts) == 2:
        inbox = MissionInbox()
        if parts[1].lower() == "all":
            reports = inbox.list(unread_only=True, limit=100)
            for report in reports:
                inbox.acknowledge(report.task_id)
            console.print(Text(f"Acknowledged {len(reports)} mission report(s)."))
            return
        try:
            report = inbox.acknowledge(parts[1])
        except ValueError as exc:
            console.print(Text(str(exc), style="red"))
            return
        console.print(Text(f"Acknowledged {report.task_id}."))
        return
    console.print(Text(
        "Uso: /mission [status|court|compute|learning|runtime|inbox [all]|"
        "report TASK_ID|ack TASK_ID|ack all]."
    ))


def _render_court(console: Any, arg: str) -> None:
    from .mission.presentation import compute_table, court_table, learning_table

    action = arg.strip().lower()
    if action in ("", "list", "status"):
        console.print(court_table())
        return
    if action in ("health", "compute"):
        console.print(compute_table())
        return
    if action == "learning":
        console.print(learning_table())
        return
    console.print(Text("Uso: /court [list|health|compute|learning]. No ejecuta modelos."))



def console_command(session: Any, name: str, args: str, console: Any) -> bool:
    """Handle local observation commands without model/tool execution."""
    if name == "capabilities":
        if args.strip():
            console.print(Text("Uso: /capabilities (no cambia permisos)."))
        else:
            console.print(Text(json.dumps(capabilities(session), ensure_ascii=False, indent=2)))
        return True
    if name == "mission":
        _render_mission(console, args)
        return True
    if name == "court":
        _render_court(console, args)
        return True
    if name != "kit":
        return False
    from .robust_kit import catalog

    skills = catalog()
    arg = args.strip()
    if arg in ("", "list"):
        console.print(Text("Skills incluidas: guías de trabajo; no son permisos.", style="bold"))
        for key, skill in sorted(skills.items()):
            console.print(Text(f"{key} — {skill.description}"))
        console.print(Text("Leer: /kit show NOMBRE. /skills conserva recetas de delegación."))
    elif arg.startswith("show ") and arg[5:].strip() in skills:
        console.print(Text(skills[arg[5:].strip()].content))
    else:
        console.print(Text("Uso: /kit list | /kit show NOMBRE. No se ejecutó ninguna skill."))
    return True

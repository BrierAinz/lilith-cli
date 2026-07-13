"""Workflow command: reusable multi-step coding workflows for Lilith.

A workflow is a named sequence of high-level steps. The /workflow command lets
users list, show, run, and save workflows. Running a workflow posts each step as
a user message to the session and streams the agent response, so tools are
involved at each step just like a normal chat turn.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .render import console, render_error


if TYPE_CHECKING:
    from .agent import AgentSession


_WORKFLOW_DIR: Path = Path.home() / ".yggdrasil" / "workflows"
_WORKFLOW_FILE: Path = _WORKFLOW_DIR / "workflows.json"

_DEFAULT_WORKFLOWS: dict[str, list[str]] = {
    "fix-tests": [
        "Leer el archivo fuente relevante con /file o read_file para entender el código.",
        "Ejecutar los tests relevantes con /git test o terminal para identificar fallos.",
        "Analizar los errores y proponer correcciones manteniendo el estilo existente.",
        "Aplicar los cambios con patch o write_file y volver a ejecutar los tests.",
    ],
    "add-tests": [
        "Leer el archivo fuente que se va a testear.",
        "Generar tests unitarios que cubran los casos felices y los edge cases principales.",
        "Escribir los tests en el archivo de tests correspondiente usando write_file o patch.",
        "Ejecutar los tests y corregir hasta que pasen.",
    ],
    "refactor": [
        "Leer el archivo a refactorizar y entender su responsabilidad actual.",
        "Identificar oportunidades de mejora: nombres, funciones grandes, duplicación, dependencias.",
        "Mostrar un plan de cambios al usuario para confirmación.",
        "Aplicar los cambios de forma incremental y ejecutar tests/lint para verificar.",
    ],
    "document": [
        "Leer el archivo fuente que se va a documentar.",
        "Generar docstrings, comentarios explicativos o una sección README si aplica.",
        "Insertar la documentación con patch o write_file respetando el estilo del proyecto.",
        "Revisar que la documentación sea clara y esté bien ubicada.",
    ],
}


def _ensure_workflow_file(workflow_dir: Path, workflow_file: Path) -> None:
    workflow_dir.mkdir(parents=True, exist_ok=True)
    if not workflow_file.exists():
        workflow_file.write_text(
            json.dumps(_DEFAULT_WORKFLOWS, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


class _WorkflowStore:
    """Path-based workflow storage holder.

    Exposes only ``workflow_dir`` and ``workflow_file`` attributes so tests
    can inject a temporary directory via ``monkeypatch.setattr`` without
    having to mock a full ``load()``/``save()`` protocol.
    """

    def __init__(self) -> None:
        self.workflow_dir: Path = _WORKFLOW_DIR
        self.workflow_file: Path = _WORKFLOW_FILE


_WORKFLOW_STORE = _WorkflowStore()


def _load_workflows() -> dict[str, list[str]]:
    workflow_dir = _WORKFLOW_STORE.workflow_dir
    workflow_file = _WORKFLOW_STORE.workflow_file
    _ensure_workflow_file(workflow_dir, workflow_file)
    try:
        data = json.loads(workflow_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): [str(s) for s in v] for k, v in data.items()}
    except Exception:
        pass
    return dict(_DEFAULT_WORKFLOWS)


def _save_workflows(workflows: dict[str, list[str]]) -> None:
    workflow_dir = _WORKFLOW_STORE.workflow_dir
    workflow_file = _WORKFLOW_STORE.workflow_file
    workflow_dir.mkdir(parents=True, exist_ok=True)
    workflow_file.write_text(
        json.dumps(workflows, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


async def _run_workflow_steps(
    session: "AgentSession",
    name: str,
    steps: list[str],
) -> None:
    """Execute a workflow by posting each step as a user prompt and streaming the response.

    Falls back to echoing the step if streaming is not available.
    """
    for i, step in enumerate(steps, start=1):
        prompt = f"[Workflow '{name}' - Paso {i}/{len(steps)}] {step}"
        console.print(f"\n[bold cyan]▶ {name}[/] [dim]paso {i}/{len(steps)}[/]")
        console.print(f"[dim]{step}[/]")

        # Streaming processing requires these functions; import lazily to avoid cycles.
        try:
            from .repl import _process_with_streaming
            from .render import render_turn_start

            render_turn_start(999)
            await _process_with_streaming(session, prompt)
        except Exception as exc:  # pragma: no cover - defensive fallback
            session.history.append({"role": "user", "content": prompt})
            if hasattr(session, "process_message"):
                await session.process_message(prompt)
            else:
                render_error(f"No se pudo ejecutar el paso {i}: {exc}")


def _steps_from_args(args: str) -> list[str]:
    """Parse steps from a semicolon or newline separated string."""
    text = args.strip()
    if ";" in text:
        return [s.strip() for s in text.split(";") if s.strip()]
    if "\n" in text:
        return [s.strip() for s in text.splitlines() if s.strip()]
    return [text] if text else []


async def run_workflow_command(session: "AgentSession", args: str) -> None:
    """Execute /workflow list|run <name>|show <name>|save <name> <steps>."""
    text = args.strip()

    if not text or text.lower() in ("list", "ls"):
        workflows = _load_workflows()
        console.print("\n[bold realm]᛭ Workflows disponibles[/]\n")
        for name in sorted(workflows):
            console.print(f"  [bold cyan]{name}[/] — [dim]{len(workflows[name])} pasos[/]")
        console.print("\n[dim]Usa /workflow show <nombre> para ver los pasos o /workflow run <nombre> para ejecutar.[/]")
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd == "show":
        if not rest:
            render_error("Uso: /workflow show <nombre>")
            return
        workflows = _load_workflows()
        name = rest.strip()
        if name not in workflows:
            render_error(f"Workflow no encontrado: [model]{name}[/]")
            return
        console.print(f"\n[bold realm]᛭ Workflow: {name}[/]\n")
        for i, step in enumerate(workflows[name], start=1):
            console.print(f"  [bold cyan]{i}.[/] {step}")
        console.print()
        return

    if subcmd == "run":
        if not rest:
            render_error("Uso: /workflow run <nombre>")
            return
        workflows = _load_workflows()
        name = rest.strip()
        if name not in workflows:
            render_error(f"Workflow no encontrado: [model]{name}[/]")
            return
        await _run_workflow_steps(session, name, workflows[name])
        return

    if subcmd == "save":
        if not rest:
            render_error("Uso: /workflow save <nombre> <pasos separados por ; o nueva línea>")
            return
        # Parse name and steps. Prefer steps as everything after the first whitespace
        # token; if only one token is provided, treat as empty steps.
        name_parts = rest.split(maxsplit=1)
        name = name_parts[0].strip()
        steps = _steps_from_args(name_parts[1]) if len(name_parts) > 1 else []
        if not name or not steps:
            render_error("Uso: /workflow save <nombre> <paso1>; <paso2>; ...")
            return
        workflows = _load_workflows()
        workflows[name] = steps
        _save_workflows(workflows)
        console.print(f"[success]✓ Workflow guardado: [model]{name}[/] ({len(steps)} pasos)")
        return

    render_error(
        "Uso: /workflow [list|run <nombre>|show <nombre>|save <nombre> <pasos>]"
    )

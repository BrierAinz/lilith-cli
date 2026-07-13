"""Pipeline command: multi-step tool pipelines for Lilith.

A pipeline is a sequence of tool calls (name + arguments) that run in order.
The /pipeline command lets users list, show, run, save, and delete pipelines.
Running a pipeline executes each tool directly through the session and prints
its result, without requiring an LLM round-trip.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .render import console, render_error

if TYPE_CHECKING:
    from .agent import AgentSession


_PIPELINE_DIR: Path = Path.home() / ".yggdrasil" / "pipelines"
_PIPELINE_FILE: Path = _PIPELINE_DIR / "pipelines.json"

# Built-in pipelines available out of the box.
_DEFAULT_PIPELINES: dict[str, list[dict[str, Any]]] = {
    "check-code": [
        {"name": "format_file", "args": {"path": "src/", "check": True}},
        {"name": "run_linter", "args": {"path": "src/"}},
        {"name": "run_test", "args": {"path": "tests/"}},
    ],
    "review-changes": [
        {"name": "git_operation", "args": {"op": "status", "args": ""}},
        {"name": "git_operation", "args": {"op": "diff", "args": "--stat"}},
    ],
    "update-deps": [
        {"name": "package_guard", "args": {"action": "check"}},
    ],
}


class _PipelineStore:
    """Simple mutable wrapper used by tests to inject a temporary pipeline file."""

    def __init__(self) -> None:
        self.pipeline_dir: Path = _PIPELINE_DIR
        self.pipeline_file: Path = _PIPELINE_FILE

    @property
    def path(self) -> Path:
        return self.pipeline_file

    def ensure(self) -> None:
        self.pipeline_dir.mkdir(parents=True, exist_ok=True)
        if not self.pipeline_file.exists():
            self.pipeline_file.write_text(
                json.dumps(_DEFAULT_PIPELINES, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    def load(self) -> dict[str, list[dict[str, Any]]]:
        self.ensure()
        try:
            data = json.loads(self.pipeline_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {
                    str(k): [
                        {"name": str(step.get("name", "")), "args": dict(step.get("args", {}))}
                        for step in v
                    ]
                    for k, v in data.items()
                }
        except Exception:
            pass
        return {
            k: [{"name": step["name"], "args": dict(step.get("args", {}))} for step in v]
            for k, v in _DEFAULT_PIPELINES.items()
        }

    def save(self, pipelines: dict[str, list[dict[str, Any]]]) -> None:
        self.pipeline_dir.mkdir(parents=True, exist_ok=True)
        self.pipeline_file.write_text(
            json.dumps(pipelines, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )


_PIPELINE_STORE = _PipelineStore()


def _load_pipelines() -> dict[str, list[dict[str, Any]]]:
    return _PIPELINE_STORE.load()


def _save_pipelines(pipelines: dict[str, list[dict[str, Any]]]) -> None:
    _PIPELINE_STORE.save(pipelines)


def _parse_steps(steps_text: str) -> list[dict[str, Any]]:
    """Parse pipeline steps from JSON or a simple name-only syntax.

    Supports:
      - JSON array: [{"name": "tool", "args": {...}}, ...]
      - Semicolon-separated tool names: tool1; tool2
      - Newline-separated tool names
    """
    text = steps_text.strip()
    if not text:
        return []

    if text.startswith("[") or text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON inválido: {exc}") from exc
        if isinstance(parsed, dict) and "steps" in parsed:
            parsed = parsed["steps"]
        if not isinstance(parsed, list):
            raise ValueError("El JSON debe ser una lista de pasos")
        return [{"name": str(step.get("name", "")), "args": dict(step.get("args", {}))} for step in parsed]

    if ";" in text:
        return [{"name": s.strip(), "args": {}} for s in text.split(";") if s.strip()]

    return [{"name": s.strip(), "args": {}} for s in text.splitlines() if s.strip()]


async def _run_pipeline_steps(
    session: "AgentSession",
    name: str,
    steps: list[dict[str, Any]],
) -> None:
    """Execute each step in order using the session's tool runner."""
    from .providers import ToolCall

    total = len(steps)
    for i, step in enumerate(steps, start=1):
        tool_name = step.get("name", "")
        tool_args = step.get("args", {})
        console.print(f"\n[bold cyan]▶ {name}[/] [dim]paso {i}/{total}:[/] [tool.name]{tool_name}[/]")
        if tool_args:
            console.print(f"[dim]  args: {tool_args}[/]")

        known_tools = session._all_tool_names()
        if known_tools and tool_name not in known_tools:
            render_error(f"Herramienta desconocida: [model]{tool_name}[/]")
            continue

        tc = ToolCall(id=f"pipeline-{name}-{i}", name=tool_name, arguments=tool_args)
        try:
            result = await session.execute_tool(tc)
        except Exception as exc:
            render_error(f"Error ejecutando [model]{tool_name}[/]: {exc}")
            continue

        if result.content.startswith("Error:"):
            render_error(result.content)
        else:
            console.print(result.content)


async def run_pipeline_command(session: "AgentSession", args: str) -> None:
    """Execute /pipeline list|run <name>|show <name>|save <name> <steps>|delete <name>."""
    text = args.strip()

    if not text or text.lower() in ("list", "ls"):
        pipelines = _load_pipelines()
        console.print("\n[bold realm]᛭ Pipelines disponibles[/]\n")
        for name in sorted(pipelines):
            console.print(f"  [bold cyan]{name}[/] — [dim]{len(pipelines[name])} pasos[/]")
        console.print("\n[dim]Usa /pipeline show <nombre> para ver los pasos o /pipeline run <nombre> para ejecutar.[/]")
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    pipelines = _load_pipelines()

    if subcmd == "show":
        if not rest:
            render_error("Uso: /pipeline show <nombre>")
            return
        name = rest.strip()
        if name not in pipelines:
            render_error(f"Pipeline no encontrado: [model]{name}[/]")
            return
        console.print(f"\n[bold realm]᛭ Pipeline: {name}[/]\n")
        for i, step in enumerate(pipelines[name], start=1):
            args_str = json.dumps(step.get("args", {}), ensure_ascii=False)
            console.print(f"  [bold cyan]{i}.[/] [tool.name]{step.get('name', '')}[/] [dim]{args_str}[/]")
        console.print()
        return

    if subcmd == "run":
        if not rest:
            render_error("Uso: /pipeline run <nombre>")
            return
        name = rest.strip()
        if name not in pipelines:
            render_error(f"Pipeline no encontrado: [model]{name}[/]")
            return
        await _run_pipeline_steps(session, name, pipelines[name])
        return

    if subcmd == "save":
        if not rest:
            render_error("Uso: /pipeline save <nombre> <pasos JSON o nombres separados por ;>")
            return
        name_parts = rest.split(maxsplit=1)
        name = name_parts[0].strip()
        steps_text = name_parts[1] if len(name_parts) > 1 else ""
        if not name:
            render_error("Uso: /pipeline save <nombre> <pasos>")
            return
        try:
            steps = _parse_steps(steps_text)
        except ValueError as exc:
            render_error(str(exc))
            return
        if not steps:
            render_error("Uso: /pipeline save <nombre> <pasos>")
            return
        pipelines[name] = steps
        _save_pipelines(pipelines)
        console.print(f"[success]✓ Pipeline guardado: [model]{name}[/] ({len(steps)} pasos)")
        return

    if subcmd in ("delete", "rm", "remove"):
        if not rest:
            render_error("Uso: /pipeline delete <nombre>")
            return
        name = rest.strip()
        if name not in pipelines:
            render_error(f"Pipeline no encontrado: [model]{name}[/]")
            return
        del pipelines[name]
        _save_pipelines(pipelines)
        console.print(f"[warning]✗ Pipeline eliminado: [model]{name}[/]")
        return

    render_error(
        "Uso: /pipeline [list|run <nombre>|show <nombre>|save <nombre> <pasos>|delete <nombre>]"
    )

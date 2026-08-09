"""Sub-agent delegation tool.

Lets the orchestrator (Lilith's main session) route a self-contained task
to a Hlidskjalf sub-agent preset (``~/.yggdrasil/hlidskjalf_subagents.yaml``)
and get the sub-agent's answer back as the tool result.

The heavy imports (lilith_cli config/providers) happen lazily inside
``execute`` to avoid a package-level dependency cycle: lilith_cli imports
lilith_tools at startup, and this tool only needs lilith_cli at call time.
Tools run in a worker thread (``asyncio.to_thread``), so ``asyncio.run``
here does not collide with the REPL's event loop.

Two modes are supported (both default-safe, the one-shot mode keeps the
pre-tanda-2 behaviour exactly):

1. **One-shot (default).** A single LLM completion with no tools — the
   sub-agent returns prose. This is the original behaviour and remains
   the default.

2. **Agentic (``agentic=True``).** A mini-loop runs inside this tool:
   the sub-agent gets a *restricted* toolset (read/write/list/edit)
   confined to ``workdir``. Any path outside the workdir is rejected
   with a clear error. The loop is capped by ``max_turns`` (default 10);
   when exhausted, the accumulated state is returned with a partial
   status — no exception is raised.

The agentic mode also accepts ``structured=True``, which asks the final
assistant turn to emit a JSON object matching
:data:`lilith_tools.task_schema.TASK_SCHEMA`. Validation is local so it
works on every provider; OpenAI-compat additionally receives a
``response_format=json_schema`` payload when the preset's provider isn't
the Anthropic path.

Design decision: the agentic mini-loop is implemented locally in this
module rather than reusing ``AgentSession`` from ``lilith_cli.agent``.
``AgentSession`` carries the full session state (history, hooks, global
ToolRegistry, JSON mode flag, cancel_event, message counters, etc.) and
re-instantiating it for a sandboxed sub-agent would require either
loading the entire CLI package or a deep refactor of its
``__init__``. The local loop is ~150 lines, runs the same provider
``complete()`` call against the same messages protocol, applies the
robust tool-args parser from tanda 1 (truncated JSON → corrective
tool_result instead of execution) and confines all file operations to
``workdir`` via path resolution. It also rejects any tool name not in
the restricted set — even if the provider leaked a host-only tool, the
sub-agent cannot call it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry
from .task_schema import TASK_SCHEMA, validate_task_response

logger = logging.getLogger(__name__)

# HTTP status codes that mean "the API rejected this payload format
# deterministically" — retrying with the same payload will just fail
# again. The structured-output degradation chain MUST trigger on these.
_DEGRADATION_STATUS_CODES = frozenset({400, 404, 422})


def _looks_like_unsupported_format_error(exc: BaseException) -> bool:
    """Return True when an exception's message suggests ``response_format``
    is the cause (rather than a payload/schema bug).

    The wrapper re-raises after its 3-retry loop; the underlying message
    usually names ``json_schema``, ``response_format`` or the offending
    field name. When we can match that, we degrade even on 5xx, because
    a deterministic format rejection often surfaces as a wrapped 4xx.
    """
    text = str(exc).lower()
    needles = (
        "json_schema",
        "json schema",
        "response_format",
        "response format",
        "strict mode",
        "unsupported",
        "not supported",
        "invalid schema",
    )
    return any(n in text for n in needles)


# Default toolset exposed to agentic sub-agents. Keys are the lilith_tools
# tool names; values are the actual ``execute`` kwargs that the tool
# accepts. The keys are the *only* ones the sub-agent is allowed to call
# in agentic mode — any other tool name from the model's output is
# rejected with a synthetic tool_result pointing at this allow-list.
_AGENTIC_TOOL_NAMES = frozenset({
    "file_read",
    "file_write",
    "file_append",
    "directory_list",
    "file_edit",
})


def _resolve_workdir(workdir: str | None, preset: str, cwd: Path) -> Path:
    """Return an absolute, unique workdir for a sub-agent run.

    Default layout: ``<cwd>/subagent_work/<preset>-<n>/`` where ``<n>`` is
    a monotonic counter that avoids collisions between concurrent runs
    of the same preset. The directory is created if it does not exist.
    """
    if workdir:
        wd = Path(workdir).expanduser().resolve()
    else:
        base = cwd / "subagent_work"
        base.mkdir(parents=True, exist_ok=True)
        # Counter-based unique suffix; cheap and avoids pid/race issues.
        existing = sorted(p for p in base.iterdir() if p.is_dir()) if base.exists() else []
        n = len(existing) + 1
        # The preset name may contain characters that aren't filesystem-safe;
        # keep only [A-Za-z0-9_-] to avoid surprises on Windows.
        safe_preset = "".join(c for c in preset if c.isalnum() or c in ("_", "-"))[:32] or "agent"
        wd = (base / f"{safe_preset}-{n}").resolve()
    wd.mkdir(parents=True, exist_ok=True)
    return wd


def _is_within(path: Path, root: Path) -> bool:
    """True if ``path`` resolves to a location inside ``root``.

    Uses ``resolve(strict=False)`` so non-existent targets still get
    canonicalised (important — the model can name a file before
    creating it). Symlinks pointing outside the sandbox are caught here
    because ``resolve`` follows them.
    """
    try:
        candidate = path.resolve(strict=False)
        root_resolved = root.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    try:
        candidate.relative_to(root_resolved)
        return True
    except ValueError:
        return False


def _sanitize_path(raw: str, workdir: Path, tool_name: str) -> Path:
    """Resolve ``raw`` against ``workdir`` and enforce the sandbox.

    Absolute paths are honoured only if they live inside the workdir
    after resolution; relative paths are joined to the workdir. Anything
    else raises ``ValueError`` with a message the model can act on.
    """
    if not raw:
        raise ValueError(f"{tool_name}: path is required")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = workdir / p
    if not _is_within(p, workdir):
        raise ValueError(
            f"{tool_name}: path {p!s} is outside the sandbox workdir {workdir}"
        )
    return p


def _run_agentic_tool(
    name: str,
    arguments: dict[str, Any],
    workdir: Path,
    written_files: list[str],
) -> str:
    """Execute one restricted tool call inside the sandbox.

    Returns a JSON string suitable for a ``tool`` message. Any sandbox
    violation is reported as a structured error the model can correct;
    no exception leaks out — the loop must keep going so the sub-agent
    can recover or finish.
    """
    if name not in _AGENTIC_TOOL_NAMES:
        return json.dumps(
            {
                "ok": False,
                "error": (
                    f"tool {name!r} is not available in agentic mode. "
                    f"Allowed: {sorted(_AGENTIC_TOOL_NAMES)}"
                ),
            }
        )

    try:
        if name == "file_read":
            p = _sanitize_path(arguments.get("path", ""), workdir, name)
            if not p.exists():
                return json.dumps({"ok": False, "error": f"file not found: {p}"})
            content = p.read_text(encoding="utf-8", errors="ignore")
            return json.dumps({"ok": True, "path": str(p), "content": content})

        if name == "file_write":
            p = _sanitize_path(arguments.get("path", ""), workdir, name)
            content = arguments.get("content", "")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            written_files.append(str(p.relative_to(workdir)))
            return json.dumps(
                {"ok": True, "path": str(p), "bytes": len(content.encode("utf-8"))}
            )

        if name == "file_append":
            p = _sanitize_path(arguments.get("path", ""), workdir, name)
            content = arguments.get("content", "")
            existed = p.exists() and p.is_file()
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open(
                mode="a" if existed else "w", encoding="utf-8", newline=""
            ) as fh:
                fh.write(content)
            written_files.append(str(p.relative_to(workdir)))
            return json.dumps(
                {
                    "ok": True,
                    "path": str(p),
                    "bytes": len(content.encode("utf-8")),
                    "appended": existed,
                }
            )

        if name == "directory_list":
            target = _sanitize_path(arguments.get("path", "."), workdir, name)
            if not target.exists():
                return json.dumps({"ok": False, "error": f"directory not found: {target}"})
            if not target.is_dir():
                return json.dumps({"ok": False, "error": f"not a directory: {target}"})
            items = [
                {
                    "name": item.name,
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                }
                for item in sorted(target.iterdir())
            ]
            return json.dumps({"ok": True, "path": str(target), "items": items})

        if name == "file_edit":
            p = _sanitize_path(arguments.get("path", ""), workdir, name)
            if not p.exists():
                return json.dumps({"ok": False, "error": f"file not found: {p}"})
            old_string = arguments.get("old_string", "")
            new_string = arguments.get("new_string", "")
            replace_all = bool(arguments.get("replace_all", False))
            original = p.read_text(encoding="utf-8", errors="ignore")
            count = original.count(old_string)
            if count == 0:
                return json.dumps({"ok": False, "error": "old_string not found"})
            if replace_all:
                new_content = original.replace(old_string, new_string)
            else:
                new_content = original.replace(old_string, new_string, 1)
            p.write_text(new_content, encoding="utf-8")
            written_files.append(str(p.relative_to(workdir)))
            return json.dumps(
                {
                    "ok": True,
                    "path": str(p),
                    "replacements": count if replace_all else 1,
                }
            )
    except ValueError as exc:
        # Sandbox violation — never execute, tell the model why.
        return json.dumps({"ok": False, "error": str(exc)})
    except Exception as exc:  # pragma: no cover - defensive
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    # Unreachable: every name in the allow-list has a branch above.
    return json.dumps({"ok": False, "error": f"unhandled tool: {name}"})


def _tool_definitions() -> list[dict[str, Any]]:
    """OpenAI-compatible tool schemas for the restricted agentic set.

    Names match the lilith_tools registry exactly so the model sees a
    consistent vocabulary with the main session.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "file_read",
                "description": (
                    "Lee el contenido de un archivo dentro del workdir "
                    "sandbox. Parametros: path (str, relative o absoluto "
                    "dentro del workdir)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_write",
                "description": (
                    "Escribe contenido a un archivo dentro del workdir. "
                    "Crea directorios padres. Parametros: path (str), content (str). "
                    "Para archivos grandes (>200 lineas) escribi por partes: "
                    "file_write con la primera parte y file_append con el resto."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_append",
                "description": (
                    "Agrega contenido al final de un archivo dentro del "
                    "workdir (crea si no existe). Crea directorios padres. "
                    "Parametros: path (str), content (str). "
                    "Usala para escribir archivos grandes por partes: "
                    "file_write arranca el archivo y file_append suma el resto."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "directory_list",
                "description": (
                    "Lista archivos y subdirectorios del path indicado "
                    "(dentro del workdir). Parametros: path (str)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_edit",
                "description": (
                    "Edita un archivo existente reemplazando old_string "
                    "con new_string. Parametros: path (str), old_string "
                    "(str), new_string (str), replace_all (bool, default False)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                        "replace_all": {"type": "boolean", "default": False},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
        },
    ]


def _agentic_system_prompt(workdir: Path) -> str:
    return (
        f"Estas corriendo como sub-agente agentico dentro de un sandbox.\n"
        f"Tu workdir es: {workdir}\n"
        f"TODAS las herramientas que tienes disponibles operan SOLO dentro de ese "
        f"directorio. Cualquier intento de leer o escribir fuera de ese path sera "
        f"rechazado con un error. Usa SIEMPRE paths relativos al workdir.\n\n"
        f"Tus herramientas: file_read, file_write, file_append, directory_list, "
        f"file_edit. No tienes shell, red, ni otras capacidades. Cuando termines "
        f"la tarea, responde SOLO con el texto final: no llames mas herramientas. "
        f"Para archivos grandes (>200 lineas) escribe por partes: file_write con "
        f"la primera parte y file_append con el resto."
    )
def _structured_system_prompt(workdir: Path, base_system: str) -> str:
    schema_hint = (
        "Cuando termines, tu respuesta final DEBE ser UN UNICO objeto JSON "
        "valido con esta estructura exacta:\n"
        "{\n"
        '  "summary": string no vacia,\n'
        '  "deliverables": [{"name": str, "type": str, "content": str}, ...],\n'
        '  "status": "completed" | "failed" | "blocked",\n'
        '  "blockers": [string, ...],\n'
        '  "next_steps": [string, ...],\n'
        '  "confidence": numero entre 0.0 y 1.0\n'
        "}\n"
        "Sin texto antes ni despues del JSON, sin bloques de markdown. "
        "Si no puedes completarlo, status='failed' o 'blocked' y "
        "blocos/next_steps describen que falto."
    )
    return (
        f"{base_system}\n\n{schema_hint}\n\nworkdir: {workdir}\n"
        f"Todas tus tools operan solo dentro de {workdir}."
    )


def _parse_tool_arguments(name: str, raw_args: Any) -> dict[str, Any] | None:
    """Parse tool-call arguments robustly, mirroring the agent loop.

    Returns ``None`` and a corrective error string on failure (truncated
    JSON, non-JSON, non-dict). The caller injects the error as a
    synthetic tool_result so the model can recover.
    """
    if isinstance(raw_args, dict):
        return raw_args
    if not raw_args:
        return {}
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


@ToolRegistry.register
class DelegateSubagentTool(BaseTool):
    """Delegate a task to a Hlidskjalf sub-agent preset and return its answer.

    Modes:
      * default — single completion, no tools (one-shot).
      * ``agentic=True`` — mini-loop with restricted file tools in a
        sandbox ``workdir``.
      * ``structured=True`` — final message must match
        :data:`TASK_SCHEMA`; one retry on validation failure.
      * ``structured`` and ``agentic`` are combinable; structured
        validation runs against the loop's final assistant message.
    """

    name = "delegate_subagent"
    # Sub-agent runs take longer than regular tools (network + full LLM
    # completion). The agent honours this as a timeout floor.
    timeout_seconds = 180
    description = (
        "Delegar una tarea autocontenida a un sub-agente y devolver su respuesta. "
        "Presets disponibles: ejecutor-kimi (loops largos, scripting, refactors); "
        "investigador-minimax (documentos largos, research multi-fuente); "
        "orquestador-fugu (deep research, síntesis, decisiones de arquitectura); "
        "opencode-glm52 (trabajo genérico barato); "
        "grok-research (contexto 1M, research); "
        "hf-glm52 (GLM-5.2 vía HuggingFace router, ejecutor genérico). "
        "Usala para trabajo que otro modelo puede resolver solo: el sub-agente "
        "NO ve esta conversación, así que el prompt debe incluir todo el contexto. "
        "agentic=True activa un mini-loop con file_read/file_write/file_append/"
        "directory_list/file_edit confinados a un workdir (default "
        "./subagent_work/<preset>-<n>/). "
        "structured=True pide al sub-agente cerrar con un JSON que valida contra "
        "TASK_SCHEMA; combina con agentic para respuestas estructuradas del loop."
    )
    parameters = {
        "preset": {
            "type": "string",
            "description": (
                "Nombre del preset de Hlidskjalf: ejecutor-kimi | "
                "investigador-minimax | orquestador-fugu | "
                "opencode-glm52 | grok-research"
            ),
            "required": True,
        },
        "prompt": {
            "type": "string",
            "description": (
                "Tarea completa y autocontenida para el sub-agente, con todo "
                "el contexto necesario incluido"
            ),
            "required": True,
        },
        "max_tokens": {
            "type": "integer",
            "description": "Límite opcional de tokens de salida para este sub-agente",
            "required": False,
        },
        "agentic": {
            "type": "boolean",
            "default": False,
            "description": (
                "Si True, ejecuta un mini-loop agentico con herramientas "
                "file_read/file_write/file_append/directory_list/file_edit "
                "confinadas al workdir"
            ),
            "required": False,
        },
        "workdir": {
            "type": "string",
            "default": "",
            "description": (
                "Directorio de trabajo para el sub-agente agentico. "
                "Default: ./subagent_work/<preset>-<n>/ bajo el cwd"
            ),
            "required": False,
        },
        "max_turns": {
            "type": "integer",
            "default": 10,
            "description": (
                "Máximo de turnos del loop agentico antes de devolver parcial"
            ),
            "required": False,
        },
        "structured": {
            "type": "boolean",
            "default": False,
            "description": (
                "Si True, exige respuesta final estructurada según TASK_SCHEMA; "
                "un reintento automático si la validación falla"
            ),
            "required": False,
        },
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        preset_name = str(kwargs.get("preset", "")).strip()
        prompt = str(kwargs.get("prompt", "")).strip()
        if not preset_name or not prompt:
            return ToolResult(
                success=False, data=None, error="'preset' y 'prompt' son requeridos"
            )

        state_store = None
        state_task_id = None
        try:
            from .orchestration_state import OrchestrationStateStore

            state_store = OrchestrationStateStore()
            summary = " ".join(prompt.split())
            task = state_store.add_task(
                summary[:80] or "Delegación",
                summary[:500],
                status="delegada",
                preset=preset_name,
            )
            state_task_id = task["id"]
        except Exception:
            # Persistence is best-effort: it must never break the existing API.
            state_store = None
            state_task_id = None

        result = self._execute_delegate(preset_name, prompt, kwargs)
        if state_store is not None and state_task_id is not None:
            data = result.data if isinstance(result.data, dict) else {}
            content = data.get("content") or data.get("raw_content") or result.error
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            provider = str(data.get("provider") or "unknown")
            session_id = str(kwargs.get("session_id") or "default")
            try:
                if usage:
                    state_store.record_cost(
                        preset_name, provider, usage, session_id=session_id
                    )
                state_store.update_task(
                    state_task_id,
                    status="completada" if result.success else "fallida",
                    result=str(content or "")[:1000],
                    usage=usage,
                )
            except Exception:
                pass
        return result

    def _execute_delegate(
        self, preset_name: str, prompt: str, kwargs: dict[str, Any]
    ) -> ToolResult:
        """Execute the original delegation path after state registration."""
        agentic = bool(kwargs.get("agentic", False))
        structured = bool(kwargs.get("structured", False))
        max_turns = int(kwargs.get("max_turns", 10) or 10)
        workdir_arg = kwargs.get("workdir") or ""

        try:
            from lilith_cli.config import load_config
            from lilith_cli.main import _load_subagent_presets
            from lilith_cli.providers import LLMProviderWrapper
        except ImportError as exc:  # pragma: no cover — cli not installed
            return ToolResult(
                success=False, data=None, error=f"lilith_cli no disponible: {exc}"
            )

        presets = _load_subagent_presets()
        if preset_name not in presets:
            return ToolResult(
                success=False,
                data=None,
                error=(
                    f"Preset '{preset_name}' no existe. "
                    f"Disponibles: {sorted(presets) or '(ninguno)'}"
                ),
            )

        preset = presets[preset_name] or {}
        cfg = load_config()
        provider_name = str(preset.get("provider") or cfg.provider).lower()
        if provider_name not in (cfg.providers or {}):
            return ToolResult(
                success=False,
                data=None,
                error=(
                    f"El preset '{preset_name}' apunta al provider "
                    f"'{provider_name}', que no está en config.yaml"
                ),
            )

        profile = cfg.providers[provider_name]
        cfg.provider = provider_name
        cfg.model = preset.get("model") or profile.model or cfg.model
        if preset.get("max_tokens") is not None:
            cfg.max_tokens = int(preset["max_tokens"])
        elif profile.max_tokens is not None:
            cfg.max_tokens = profile.max_tokens
        if kwargs.get("max_tokens") is not None:
            cfg.max_tokens = int(kwargs["max_tokens"])
        if preset.get("temperature") is not None:
            cfg.temperature = float(preset["temperature"])

        base_system = str(preset.get("system_prompt") or "")

        if agentic:
            return self._execute_agentic(
                preset_name=preset_name,
                provider_name=provider_name,
                cfg=cfg,
                prompt=prompt,
                base_system=base_system,
                workdir_arg=workdir_arg,
                max_turns=max_turns,
                structured=structured,
                LLMProviderWrapper=LLMProviderWrapper,
            )

        # ── One-shot path (unchanged from tanda 1) ─────────────────────
        system_prompt = (base_system + "\n\n" if base_system else "") + (
            "IMPORTANTE: no tienes herramientas, ni acceso a archivos, ni shell. "
            "Devuelve todo el contenido directamente en tu respuesta como texto."
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        async def _run() -> dict[str, Any]:
            provider = LLMProviderWrapper(cfg)
            try:
                return await provider.complete(messages)
            finally:
                await provider.close()

        try:
            result = asyncio.run(_run())
        except Exception as exc:
            return ToolResult(
                success=False,
                data=None,
                error=f"Sub-agente '{preset_name}' falló: {exc}",
            )

        content = result.get("content", "")
        usage = result.get("usage", {})

        if structured:
            validated, raw_content, errors = asyncio.run(
                self._enforce_structured(
                    content=content,
                    preset_name=preset_name,
                    provider_name=provider_name,
                    cfg=cfg,
                    base_system=base_system,
                    response_format=self._provider_response_format(),
                )
            )
            return ToolResult(
                success=validated is not None,
                data={
                    "preset": preset_name,
                    "provider": provider_name,
                    "model": cfg.model,
                    "content": (validated.get("summary", "") if validated else content),
                    "usage": usage,
                    "structured": validated,
                    "validation_errors": errors,
                    "raw_content": raw_content if validated is None else None,
                },
                error="" if validated is not None else (
                    f"structured output failed validation: {errors}"
                ),
            )

        return ToolResult(
            success=True,
            data={
                "preset": preset_name,
                "provider": provider_name,
                "model": cfg.model,
                "content": content,
                "usage": usage,
            },
        )

    # ── Agentic mini-loop ─────────────────────────────────────────────

    def _provider_response_format(self) -> dict[str, Any] | None:
        """Return the OpenAI-compat ``response_format`` payload for the
        structured task schema, or ``None`` to fall back to prompt-only.

        The wrapper already passes ``response_format`` through when the
        provider speaks OpenAI's wire format; the Anthropic-compat path
        builds its own payload and ignores this key, so we keep it
        universal.
        """
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "task_response",
                "schema": TASK_SCHEMA,
                "strict": True,
            },
        }

    def _execute_agentic(
        self,
        *,
        preset_name: str,
        provider_name: str,
        cfg: Any,
        prompt: str,
        base_system: str,
        workdir_arg: str,
        max_turns: int,
        structured: bool,
        LLMProviderWrapper: Any,
    ) -> ToolResult:
        try:
            workdir = _resolve_workdir(workdir_arg or None, preset_name, Path.cwd())
        except Exception as exc:
            return ToolResult(
                success=False, data=None,
                error=f"No pude preparar workdir: {exc}",
            )

        tool_schemas = _tool_definitions()
        # Structured always runs against the final assistant message; if
        # the loop is agentic we still let the model use tools and only
        # ask for JSON once it has decided to stop calling them.
        if structured:
            system_prompt = _structured_system_prompt(workdir, base_system)
        else:
            system_prompt = _agentic_system_prompt(workdir)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        written_files: list[str] = []
        usage_accum: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        turns_used = 0
        final_content = ""
        partial = False

        async def _run() -> None:
            nonlocal final_content, turns_used, partial
            provider = LLMProviderWrapper(cfg)
            try:
                # Force response_format when structured and provider is
                # OpenAI-compat; the wrapper passes it through to the
                # payload. Anthropic-compat / Sakana-Responses fall back
                # to the prompt-only instruction already in system_prompt.
                extra_kwargs: dict[str, Any] = {}
                if structured:
                    rf = self._provider_response_format()
                    if rf is not None:
                        extra_kwargs["response_format"] = rf

                for turn in range(1, max_turns + 1):
                    turns_used = turn
                    response = await provider.complete(
                        messages, tools=tool_schemas, **extra_kwargs
                    )
                    usage = response.get("usage", {}) or {}
                    for k in usage_accum:
                        if isinstance(usage.get(k), (int, float)):
                            usage_accum[k] += int(usage[k])

                    content = response.get("content", "") or ""
                    tool_calls_raw = response.get("tool_calls") or []

                    # No tool calls at all → the model is done, this
                    # turn is the final assistant answer.
                    if not tool_calls_raw:
                        final_content = content
                        # Append the assistant turn so any subsequent
                        # structured-validation retry has the full trace.
                        messages.append({"role": "assistant", "content": content})
                        return

                    # Parse tool_calls. The provider usually returns
                    # them as lilith_cli.providers.ToolCall objects, but
                    # tolerate plain dicts from mocks / future paths.
                    # ``raw_shapes`` keeps the wire-format representation
                    # so the assistant turn we append matches what the
                    # model actually emitted (including broken entries
                    # whose arguments we could not parse).
                    raw_shapes: list[dict[str, Any]] = []
                    parsed_calls: list[tuple[str, str, dict[str, Any]]] = []
                    corrective_results: list[dict[str, Any]] = []
                    for tc in tool_calls_raw:
                        if isinstance(tc, dict):
                            tc_id = tc.get("id", "")
                            tc_name = tc.get("name", "")
                            tc_args_raw = tc.get("arguments", {})
                        else:
                            tc_id = getattr(tc, "id", "")
                            tc_name = getattr(tc, "name", "")
                            tc_args_raw = getattr(tc, "arguments", {})
                        if not tc_name:
                            continue
                        # Preserve the original wire-format string for
                        # the assistant turn; the model needs to see its
                        # own arguments echoed back.
                        if isinstance(tc_args_raw, dict):
                            wire_args = json.dumps(tc_args_raw)
                        elif isinstance(tc_args_raw, str):
                            wire_args = tc_args_raw
                        else:
                            wire_args = json.dumps(tc_args_raw)
                        raw_shapes.append(
                            {
                                "id": tc_id,
                                "type": "function",
                                "function": {"name": tc_name, "arguments": wire_args},
                            }
                        )
                        args = _parse_tool_arguments(tc_name, tc_args_raw)
                        if args is None:
                            # Robust parser (tanda 1 pattern): bad JSON
                            # → corrective tool_result, no execution.
                            # Crucially we still let the loop continue so
                            # the model can recover with a fresh attempt.
                            finish_hint = ""
                            if response.get("finish_reason") == "length":
                                finish_hint = (
                                    " El turno terminó por finish_reason='length'."
                                )
                            corrective = (
                                f"Los argumentos de {tc_name} no fueron JSON válido "
                                f"(probable truncamiento por límite de tokens).{finish_hint} "
                                "Divide el contenido en partes más pequeñas o usa "
                                "varias llamadas consecutivas."
                            )
                            corrective_results.append(
                                {"tool_call_id": tc_id, "content": corrective}
                            )
                            continue
                        parsed_calls.append((tc_id, tc_name, args))

                    # Append the assistant turn with the original tool_calls
                    # (including any that failed parsing) so the conversation
                    # history mirrors the OpenAI wire format.
                    messages.append(
                        {
                            "role": "assistant",
                            "content": content,
                            "tool_calls": raw_shapes,
                        }
                    )
                    # Append corrective tool_results for unparseable calls.
                    for cr in corrective_results:
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": cr["tool_call_id"],
                                "content": cr["content"],
                            }
                        )
                    # Execute the valid ones and append their results.
                    for tc_id, tc_name, tc_args in parsed_calls:
                        # Per-turn progress marker (tanda 4 FEATURE C,
                        # minimal version): one log line per tool the
                        # sub-agent calls, with ok/error parsed from
                        # the JSON envelope ``_run_agentic_tool``
                        # returns. The richer Live panel is deferred
                        # to a later tanda; this log line is enough to
                        # debug a hanging sub-agent loop from
                        # ``tail -f`` or the REPL's logger capture.
                        result_str = _run_agentic_tool(
                            tc_name, tc_args, workdir, written_files
                        )
                        try:
                            _rc = json.loads(result_str)
                            _ok = bool(_rc.get("ok"))
                            _err = _rc.get("error", "") if not _ok else ""
                        except Exception:
                            _ok, _err = False, "result-not-json"
                        logger.info(
                            "delegate turn %d: %s %s%s",
                            turn,
                            tc_name,
                            "ok" if _ok else "error",
                            f" - {_err}" if _err else "",
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "content": result_str,
                            }
                        )

                # Turn budget exhausted without a final text turn.
                partial = True
                final_content = (
                    f"[max_turns={max_turns} agotado sin cierre limpio del loop]"
                )
            finally:
                await provider.close()

        try:
            asyncio.run(_run())
        except Exception as exc:
            return ToolResult(
                success=False,
                data={
                    "preset": preset_name,
                    "provider": provider_name,
                    "model": cfg.model,
                    "workdir": str(workdir),
                    "files_written": written_files,
                    "turns_used": turns_used,
                    "usage": usage_accum,
                    "partial": partial,
                },
                error=f"Sub-agente agentico '{preset_name}' falló: {exc}",
            )

        data: dict[str, Any] = {
            "preset": preset_name,
            "provider": provider_name,
            "model": cfg.model,
            "workdir": str(workdir),
            "content": final_content,
            "usage": usage_accum,
            "files_written": written_files,
            "turns_used": turns_used,
            "partial": partial,
        }

        if structured:
            validated, raw_content, errors = asyncio.run(
                self._enforce_structured_in_loop(
                    messages=messages,
                    final_content=final_content,
                    partial=partial,
                    provider_name=provider_name,
                    cfg=cfg,
                    base_system=base_system,
                    workdir=workdir,
                    written_files=written_files,
                    turns_used=turns_used,
                    usage_accum=usage_accum,
                    preset_name=preset_name,
                )
            )
            data["structured"] = validated
            data["validation_errors"] = errors
            data["raw_content"] = raw_content if validated is None else None
            return ToolResult(
                success=validated is not None,
                data=data,
                error="" if validated is not None else (
                    f"structured output failed validation after degradation chain: {errors}"
                ),
            )

        return ToolResult(success=True, data=data)

    # ── Structured-output validation (shared by both modes) ────────────

    async def _enforce_structured(
        self,
        *,
        content: str,
        preset_name: str,
        provider_name: str,
        cfg: Any,
        base_system: str,
        response_format: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, str, list[str]]:
        """Validate one-shot structured output, with a 3-level degradation chain.

        The chain (only entered on the *first* attempt; later attempts
        pick up where the previous one left off):

        1. **Level A — json_schema.** ``response_format={"type":"json_schema", ...}``
           plus the schema hint in the system prompt.
        2. **Level B — json_object.** ``response_format={"type":"json_object"}``
           plus the full schema embedded in the system prompt.
        3. **Level C — prompt-only.** No ``response_format``; the system
           prompt asks the model to emit a JSON object as text.

        Each level runs through the wrapper's 3-retry loop. The wrapper
        wastes retries on a deterministic 400 — that's the bug we are
        working around — but the next level uses a *different* payload,
        so the cumulative cost is bounded: ~3 retries × 3 levels ×
        typical timeout. If a level succeeds, the chain stops.

        After all levels, a final corrective retry is attempted at the
        same level that produced the most content (best-effort). If even
        that fails, the original ``content`` is preserved as
        ``raw_content`` so the orchestrator can still inspect the model's
        work.
        """
        from lilith_cli.providers import LLMProviderWrapper  # local import

        # Level A: try to parse what the wrapper already returned.
        obj, errors = _try_parse(content)
        if obj is not None and not errors:
            return obj, content, []

        levels = self._build_degradation_levels(
            base_system=base_system,
            response_format=response_format,
        )

        last_raw = content
        last_errors = errors
        last_response_format = response_format

        for level_name, level_system, level_rf in levels:
            logger.info(
                "delegate %s: structured validation degraded to level %s",
                preset_name, level_name,
            )
            messages = [
                {"role": "system", "content": level_system},
                {
                    "role": "user",
                    "content": _corrective_message(last_raw, last_errors),
                },
            ]
            try:
                response = await self._complete_async(cfg, messages, level_rf, LLMProviderWrapper)
            except Exception as exc:
                # Wrapper raised — the chain can't help further. Record
                # the failure on this level and continue to the next.
                last_raw = content
                last_errors = last_errors + [
                    f"level {level_name} call failed: {type(exc).__name__}: {exc}"
                ]
                last_response_format = level_rf
                continue

            response_content = response.get("content", "") or ""
            last_raw = response_content  # most recent attempt
            obj2, errors2 = _try_parse(response_content)
            if obj2 is not None and not errors2:
                return obj2, response_content, []

            last_errors = errors2 or ["response is not JSON"]
            last_response_format = level_rf

        # All levels failed. Return None with the most recent raw_content
        # so callers can still read what the model produced.
        return None, last_raw, last_errors

    async def _complete_async(
        self,
        cfg: Any,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None,
        LLMProviderWrapper: Any,
    ) -> dict[str, Any]:
        """Run a single provider.complete call asynchronously.

        Tools run in a worker thread (asyncio.to_thread) so a sync bridge
        here would be wasteful; we await the wrapper directly.
        """
        provider = LLMProviderWrapper(cfg)
        try:
            kwargs: dict[str, Any] = {}
            if response_format is not None:
                kwargs["response_format"] = response_format
            return await provider.complete(messages, **kwargs)
        finally:
            try:
                await provider.close()
            except Exception:
                pass

    def _build_degradation_levels(
        self,
        *,
        base_system: str,
        response_format: dict[str, Any] | None,
    ) -> list[tuple[str, str, dict[str, Any] | None]]:
        """Build the (level_name, system_prompt, response_format) chain.

        The list may be empty if no degradation is possible (e.g. the
        caller already pinned ``response_format=None``).
        """
        levels: list[tuple[str, str, dict[str, Any] | None]] = []

        schema_hint_inline = _schema_hint_inline()

        # Level A: json_schema (only if caller actually requested it).
        if response_format is not None and response_format.get("type") == "json_schema":
            levels.append((
                "A_json_schema",
                (base_system + "\n\n" if base_system else "") + schema_hint_inline,
                response_format,
            ))

        # Level B: json_object (with the schema in the prompt).
        levels.append((
            "B_json_object",
            (base_system + "\n\n" if base_system else "") + schema_hint_inline,
            {"type": "json_object"},
        ))

        # Level C: no response_format; rely on prompt instructions only.
        levels.append((
            "C_prompt_only",
            (base_system + "\n\n" if base_system else "") + schema_hint_inline,
            None,
        ))
        return levels

    async def _enforce_structured_in_loop(
        self,
        *,
        messages: list[dict[str, Any]],
        final_content: str,
        partial: bool,
        provider_name: str,
        cfg: Any,
        base_system: str,
        workdir: Path,
        written_files: list[str],
        turns_used: int,
        usage_accum: dict[str, int],
        preset_name: str,
    ) -> tuple[dict[str, Any] | None, str | None, list[str]]:
        """Validate structured output produced by the agentic loop.

        If the first attempt parses cleanly, return immediately. Otherwise
        fall back to the same 3-level degradation chain used by the
        one-shot path (json_schema → json_object → prompt-only). The
        chain runs *no-tools* completions so it never re-enters the
        sandbox loop. Each level's usage is accumulated into
        ``usage_accum`` so the orchestrator sees the full cost.
        """
        from lilith_cli.providers import LLMProviderWrapper  # local import

        # Tally retry usage too so the orchestrator sees the full cost.
        def _add_usage(u: dict[str, Any]) -> None:
            for k in usage_accum:
                if isinstance(u.get(k), (int, float)):
                    usage_accum[k] += int(u[k])

        obj, errors = _try_parse(final_content)
        if obj is not None and not errors:
            return obj, None, []

        last_raw = final_content
        last_errors = errors
        levels = self._build_degradation_levels(
            base_system=base_system,
            response_format=self._provider_response_format(),
        )

        for level_name, level_system, level_rf in levels:
            logger.info(
                "delegate %s: in-loop structured validation degraded to level %s",
                preset_name, level_name,
            )
            retry_msgs = list(messages) + [
                {
                    "role": "user",
                    "content": _corrective_message(last_raw, last_errors),
                },
            ]
            # Strip the workdir sandbox prompt from the retry system —
            # the in-loop retry is a no-tools completion, no workdir is
            # needed.
            retry_msgs[0] = {
                "role": "system",
                "content": level_system,
            }
            try:
                response = await self._complete_async(
                    cfg, retry_msgs, level_rf, LLMProviderWrapper
                )
            except Exception as exc:
                last_raw = final_content
                last_errors = last_errors + [
                    f"in-loop level {level_name} call failed: {type(exc).__name__}: {exc}"
                ]
                continue

            _add_usage(response.get("usage", {}) or {})
            retry_content = response.get("content", "") or ""
            last_raw = retry_content
            obj2, errors2 = _try_parse(retry_content)
            if obj2 is not None and not errors2:
                return obj2, None, []

            last_errors = errors2 or ["response is not JSON"]

        # All levels failed. Return None with the most recent raw_content
        # so the caller can still surface the model's last attempt.
        return None, last_raw, last_errors


def _try_parse(content: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Parse a JSON object from a model response and validate it.

    Returns ``(obj, [])`` on success, ``(None, errors)`` on any failure
    (invalid JSON, wrong root type, schema violations). On parse success
    but validation failure the parsed object is discarded — the caller
    shouldn't use a half-valid response.

    The parser is deliberately permissive because model output in
    production is messy:

    * Code fences (``\\`\\`\\`json ... \\`\\`\\``` and bare ``\\`\\`\\` ... \\`\\`\\```)
      with or without a language tag, sometimes unclosed.
    * Prose before/after the JSON object ("Here you go: {...} Hope it
      helps!"). We pick the first balanced ``{...}`` block we find.
    * Whitespace, smart quotes, trailing commas — only ``json.loads``
      can tell us whether those matter, so we extract candidates and
      let JSON parsing decide.

    The returned errors are safe to surface to a corrective prompt or a
    caller log; they never include raw model output.
    """
    if not content or not content.strip():
        return None, ["empty response"]

    candidates: list[str] = []
    text = content.strip()

    # 1) Try the whole content first — simplest case, no fences, no prose.
    candidates.append(text)

    # 2) Strip a leading/trailing markdown fence pair (``` or ```json).
    fence_re = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)\n?\s*```", re.DOTALL)
    for m in fence_re.finditer(text):
        candidates.append(m.group(1).strip())

    # 3) If the whole text isn't fenced but starts with ```, try each
    #    fence block individually (handles unclosed fences).
    if text.startswith("```"):
        parts = text.split("```")
        for block in parts[1:]:
            stripped = block.split("\n", 1)[1] if "\n" in block else block
            stripped = stripped.rstrip().rstrip("`").rstrip()
            if stripped:
                candidates.append(stripped)

    # 4) Extract the first balanced {...} from any candidate. Models
    #    that ignore "JSON only" instructions often add prose; we want
    #    the JSON anyway because that's what the validator checks.
    brace_re = re.compile(r"\{")
    # Snapshot the candidate list before iterating: we may extend it
    # with extracted {...} slices below, but we never want to re-scan
    # the slices (that creates an infinite loop on malformed input).
    snapshot = list(candidates)
    for cand in snapshot:
        for match in brace_re.finditer(cand):
            start = match.start()
            depth = 0
            in_string = False
            escape = False
            end = -1
            for i in range(start, len(cand)):
                ch = cand[i]
                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                    continue
                if ch == '"':
                    in_string = True
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end > start:
                candidates.append(cand[start:end])

    # Try candidates in order; the first that parses as an object wins.
    parsed: Any = None
    for cand in candidates:
        if not cand or not cand.strip():
            continue
        try:
            parsed = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            break
        # JSON value but not an object — keep looking.
        parsed = None
    else:
        return None, ["response is not JSON (no parseable object)"]

    if not isinstance(parsed, dict):
        return None, [f"response is not JSON: expected object, got {type(parsed).__name__}"]

    errors = validate_task_response(parsed)
    if errors:
        return None, errors
    return parsed, []


def _corrective_message(content: str, errors: list[str]) -> str:
    preview = content[:400] + ("..." if len(content) > 400 else "")
    bullets = "\n".join(f"  - {e}" for e in errors)
    return (
        "Tu respuesta anterior no cumplio el formato requerido.\n\n"
        f"Errores de validacion:\n{bullets}\n\n"
        f"Tu respuesta fue:\n```\n{preview}\n```\n\n"
        "Devuelve UNICAMENTE un objeto JSON valido con los campos "
        "summary, deliverables, status, blockers, next_steps, confidence. "
        "Sin texto antes ni despues del JSON, sin bloques de markdown."
    )


def _schema_hint_inline() -> str:
    """Schema-as-prose used by degradation levels B (json_object) and C (prompt-only).

    Returns a single string with the full JSON shape the model must
    emit. The validator in :func:`lilith_tools.task_schema.validate_task_response`
    is the source of truth; this prose is a best-effort mirror so models
    that ignore ``response_format`` (or can't accept it at all) still
    know what to send.
    """
    return (
        "Cuando termines, tu respuesta final DEBE ser UN UNICO objeto JSON "
        "valido (sin texto antes ni despues, sin bloques de markdown) "
        "con esta estructura exacta:\n"
        "{\n"
        '  "summary": string no vacia,\n'
        '  "deliverables": ['
        '{"name": str, "type": str, "content": str}, ...'
        '],\n'
        '  "status": "completed" | "failed" | "blocked",\n'
        '  "blockers": [string, ...],\n'
        '  "next_steps": [string, ...],\n'
        '  "confidence": numero entre 0.0 y 1.0\n'
        "}\n"
        "Los campos requeridos son summary y status; el resto son opcionales "
        "pero recomendados. Si no podes completarlo, status='failed' o "
        "'blocked' y blockers/next_steps describen que falto."
    )

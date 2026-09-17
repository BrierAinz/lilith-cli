"""Core agent orchestrator for Yggdrasil CLI v6.0.

The ``AgentSession`` holds all runtime state (config, provider, tools,
memory, history) and implements the main message-processing loop with
tool-call resolution.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import json
import logging
import socket
import uuid
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from .config import YggdrasilConfig, load_config
from .providers import (
    LLMProviderWrapper,
    ToolCall,
    ToolResult,
    create_provider,
    lilith_tools_to_openai,
)
from .tool_hook_dispatcher import ToolHookDispatcher
from .unicode_safety import sanitize_text, sanitize_unicode

logger = logging.getLogger(__name__)


DECISION_SUPPORT_INSTRUCTIONS = """
DECISION SUPPORT:
- When the user would benefit from choosing among materially different paths,
  present 2 to 4 concise, mutually distinct options.
- Mark exactly one option as "Recommended" and explain its main reason and
  practical effect. Let the user choose by number or by option name.
- Ask for a choice only when it would materially change scope, risk, cost,
  privacy, architecture, or an irreversible outcome. Otherwise make a
  reasonable assumption, state it briefly when useful, and continue.
- Options are decision support, not a substitute for required safety or
  authorization checks. Never describe an unsafe or unauthorized path as the
  recommended option.
""".strip()

# ── Tool execution limits (v4.3.1) ──────────────────────────────────
# Protect the conversation from a single tool call flooding the context.
# Tools that produce large output (file_read on a big file, web_search,
# directory_list on a huge tree) are truncated after these limits.
_MAX_TOOL_RESULT_CHARS = 50_000        # hard cap on a single tool result
_DEFAULT_HISTORY_CHAR_BUDGET = 120_000
_DEFAULT_TOOL_RESULT_CHAR_CAP = 8_000
_HISTORY_OMISSION_MARKER = "\n...[contenido omitido]...\n"


def _shrink_tool_result(text: Any, cap: int) -> Any:
    """Limit a string tool result while retaining useful context at both ends."""
    if not isinstance(text, str) or cap < 0 or len(text) <= cap:
        return text
    if cap == 0:
        return ""
    if cap <= len(_HISTORY_OMISSION_MARKER):
        return text[:cap]

    remaining = cap - len(_HISTORY_OMISSION_MARKER)
    head = (remaining + 1) // 2
    tail = remaining - head
    suffix = text[-tail:] if tail else ""
    return text[:head] + _HISTORY_OMISSION_MARKER + suffix


def _trim_history_to_budget(
    history: list[dict[str, Any]],
    budget: int,
    tool_cap: int,
) -> list[dict[str, Any]]:
    """Cap tool output, then discard oldest messages until history fits."""
    trimmed: list[dict[str, Any]] = []
    for message in history:
        if isinstance(message, dict) and message.get("role") == "tool":
            content = message.get("content")
            shrunk = _shrink_tool_result(content, tool_cap)
            if shrunk is not content:
                message = {**message, "content": shrunk}
        trimmed.append(message)

    def content_size(message: dict[str, Any]) -> int:
        content = message.get("content") if isinstance(message, dict) else None
        return len(content) if isinstance(content, str) else 0

    total = sum(content_size(message) for message in trimmed)
    while len(trimmed) > 1 and total > budget:
        total -= content_size(trimmed.pop(0))
    return trimmed
_TRUNCATION_NOTICE = "\n\n[…resultado truncado para proteger el contexto. Si necesitas ver el resto, usa search_files con un patrón m\u00e1s específico o file_read con offset/limit.]"

# ── Conversation history message ─────────────────────────────────────


class Message(dict):
    """A single conversation message, stored as an OpenAI-compatible dict."""

    @staticmethod
    def user(text: str) -> dict[str, Any]:
        """Create a user-role message dict."""
        return {"role": "user", "content": sanitize_text(text)}

    @staticmethod
    def assistant(text: str, tool_calls: list[ToolCall] | None = None) -> dict[str, Any]:
        """Create an assistant-role message dict, optionally with tool calls."""
        msg: dict[str, Any] = {"role": "assistant", "content": sanitize_text(text)}
        if tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments)
                        if isinstance(tc.arguments, dict)
                        else tc.arguments,
                    },
                }
                for tc in tool_calls
            ]
        return msg

    @staticmethod
    def tool_result(tc: ToolResult) -> dict[str, Any]:
        """Create a tool-result message dict from a ToolResult."""
        return sanitize_unicode(tc.to_openai_message())

    @staticmethod
    def system(text: str) -> dict[str, Any]:
        """Create a system-role message dict."""
        return {"role": "system", "content": sanitize_text(text)}


def _drop_orphan_tool_messages(
    history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Repara el pairing tool_call↔tool_result de un historial (posiblemente
    truncado) para que sea v\u00e1lido ante la API de chat/completions.

    Dos desajustes que el truncamiento por max_turns puede introducir y que el
    proveedor (k3/OpenAI) rechaza con ``tool_call_id is not found`` (400):

    1. Un mensaje ``role="tool"`` sin un ``assistant`` previo que declare su
       ``tool_call_id`` (el corte dejó el result pero no su assistant). Se
       descarta el result huérfano.
    2. Un ``assistant`` con ``tool_calls`` al que le falta el ``tool`` result de
       alguno de sus ids (grupo incompleto). Se degrada a un assistant sin
       ``tool_calls`` conservando su ``content``; si no tiene content, se omite.

    No muta el input; devuelve una lista nueva.
    """
    resulted: set[str] = {
        m["tool_call_id"]
        for m in history
        if isinstance(m, dict) and m.get("role") == "tool" and "tool_call_id" in m
    }
    # ids de grupos COMPLETOS: un assistant cuyos tool_calls tienen TODOS su
    # result. Solo esos assistant (con sus tool_calls) y esos results se
    # conservan; si un grupo está incompleto, ni el assistant lleva tool_calls
    # ni se conservan sus results parciales (quedarían huérfanos).
    kept_ids: set[str] = set()
    for m in history:
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls"):
            ids = {tc["id"] for tc in m["tool_calls"] if "id" in tc}
            if ids and ids <= resulted:
                kept_ids.update(ids)

    out: list[dict[str, Any]] = []
    for m in history:
        if not isinstance(m, dict):
            out.append(m)
            continue
        role = m.get("role")
        if role == "tool":
            if m.get("tool_call_id") in kept_ids:
                out.append(m)
            # else: result de un grupo incompleto/huérfano → se descarta
        elif role == "assistant" and m.get("tool_calls"):
            ids = {tc["id"] for tc in m["tool_calls"] if "id" in tc}
            if ids and ids <= resulted:
                out.append(m)  # grupo completo: se mantiene con sus tool_calls
            else:
                content = m.get("content") or ""
                if str(content).strip():
                    out.append({"role": "assistant", "content": content})
                # grupo incompleto sin content → se omite del todo
        else:
            out.append(m)
    return out


# ── AgentSession ────────────────────────────────────────────────────


class AgentSession:
    """Holds all runtime state and drives the conversation loop.

    Parameters
    ----------
    config:
        The loaded :class:`YggdrasilConfig`.
    provider:
        The LLM provider wrapper.  If ``None`` one is created from *config*.

    """

    def __init__(
        self,
        config: YggdrasilConfig,
        provider: LLMProviderWrapper | None = None,
    ) -> None:
        self.config = config
        self.provider = provider or create_provider(config)
        self.history: list[dict[str, Any]] = []
        self.system_prompt = config.system_prompt
        self._tools_enabled = bool(getattr(config, "tools_enabled", True))
        self._total_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        # Per-model usage: {model_name: {prompt_tokens, completion_tokens, total_tokens, cost}}
        self._per_model_usage: dict[str, dict[str, Any]] = {}
        self._last_user_message: str = ""  # For /redo support.

        # Streaming cancellation token. The REPL (or IDE view) creates an
        # asyncio.Event per turn and passes it to process_message_stream so
        # Ctrl+C can cleanly stop the stream and in-flight tool execution.
        self._cancel_event: asyncio.Event | None = None

        # Memory store (lazy-init).
        self._memory: Any = None
        if config.memory.enabled:
            self._init_memory()

        # Project instructions cache (loaded on first _build_messages).
        self._project_instructions: str | None = None

        # Tool registry (lazy-init).
        self._tool_registry: Any = None
        self._tools_cache: list[dict[str, Any]] | None = None

        # Tool enable/disable overrides. Names in this set are excluded from
        # get_tool_descriptions() even when their category is enabled.
        self._disabled_tools: set[str] = set()

        # Agent operating mode (default, plan-first, review-only, auto-edit).
        self.agent_mode: str = "default"
        self._agent_allow_writes: bool = True
        self._agent_plan_first: bool = False
        from .agent_modes import apply_agent_mode, get_agent_mode

        configured_mode = get_agent_mode(config.agent_mode)
        if configured_mode is None:
            raise ValueError(f"Unknown agent_mode: {config.agent_mode!r}")
        requested_confirmation = config.confirm_write
        apply_agent_mode(self, configured_mode)
        if configured_mode.name == "default":
            config.confirm_write = requested_confirmation

        # Auto-execute settings: pre-approved tool patterns.
        self._auto_execute: bool = False
        self._auto_approved_patterns: list[str] = []

        # Stream mode: when True, the REPL renders LLM text as it arrives.
        self._stream_enabled: bool = True

        # Hook registry (lazy-init).
        # hooks fire around every tool execution so policy and audit gates
        # can intercept, rewrite args, or suppress results. See
        # ``lilith_core.hooks`` for the contract.
        self._hook_registry: Any = None
        self._session_id: str = ""
        self._hook_failures: int = 0  # Count of hook exceptions (telemetry)
        self._hook_dispatcher = ToolHookDispatcher()
        self._session_start: datetime = datetime.now(UTC)

        # Simple execution telemetry for /metrics.
        self._tool_call_history: list[dict[str, Any]] = []
        self._command_history: list[dict[str, Any]] = []
        self._file_edit_history: list[dict[str, Any]] = []

        # Pinned messages (in-memory only, per session).
        self._pinned_messages: list[dict[str, Any]] = []

        # JSON mode: when True, the LLM is asked to emit structured JSON output.
        self._json_mode: bool = False

        # Autonomy safeguards: repeated failed calls and text continuations.
        self._last_failed_tool_signature: str | None = None
        self._identical_tool_failures: int = 0
        self._last_auto_continuations: int = 0

        # Interactive Mission ownership. mission_prepare registers + claims with
        # this owner; a heartbeat keeps the lease alive while the REPL event loop
        # exists. A crashed process stops heartbeating and the campaign runtime
        # can recover the expired lease.
        self._mission_owner: str = (
            f"lilith-session:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self._mission_task_id: str | None = None
        self._mission_id: str | None = None
        self._mission_lease_seconds: float = 300.0
        self._mission_heartbeat_seconds: float = 60.0
        self._mission_heartbeat_task: asyncio.Task | None = None
        self._mission_lease_lost: bool = False

    # ── Cancellation ────────────────────────────────────────────────

    def cancel(self) -> None:
        """Signal cancellation to any in-flight stream or tool loop.

        Safe to call from a synchronous context (e.g. a Ctrl+C handler).
        """
        if self._cancel_event is not None and not self._cancel_event.is_set():
            self._cancel_event.set()

    async def _mission_heartbeat_loop(self, task_id: str) -> None:
        from lilith_tools.orchestration_state import OrchestrationStateStore

        store = OrchestrationStateStore()
        while self._mission_task_id == task_id:
            await asyncio.sleep(self._mission_heartbeat_seconds)
            if self._mission_task_id != task_id:
                return
            try:
                await asyncio.to_thread(
                    store.renew_lease,
                    task_id,
                    self._mission_owner,
                    lease_seconds=self._mission_lease_seconds,
                )
            except (OSError, ValueError, RuntimeError):
                try:
                    snapshot = await asyncio.to_thread(store.get)
                    current = next(
                        (row for row in snapshot.get("tasks", []) if row.get("id") == task_id),
                        None,
                    )
                except (OSError, ValueError, RuntimeError):
                    current = None
                if current and current.get("status") in {"completada", "fallida", "cancelada"}:
                    self._mission_task_id = None
                    self._mission_id = None
                    self._mission_lease_lost = False
                    return
                self._mission_lease_lost = True
                self.cancel()
                logger.error("Interactive mission lease lost for %s", task_id)
                return

    async def _adopt_mission_prepare_result(self, result: ToolResult) -> None:
        if result.content.startswith("Error:"):
            return
        try:
            payload = json.loads(result.content)
            registration = payload.get("registration") or {}
            task_id = str(registration.get("task_id") or "").strip()
            mission_id = str(registration.get("mission_id") or "").strip()
            status = str(registration.get("status") or "")
        except (json.JSONDecodeError, AttributeError, TypeError):
            return
        if not task_id or status != "delegada":
            return
        self._mission_task_id = task_id
        self._mission_id = mission_id or None
        self._mission_lease_lost = False
        existing = self._mission_heartbeat_task
        if existing is not None and not existing.done():
            existing.cancel()
        self._mission_heartbeat_task = asyncio.create_task(
            self._mission_heartbeat_loop(task_id)
        )

    async def _refresh_mission_after_complete(self) -> None:
        if not self._mission_task_id:
            return
        from lilith_tools.orchestration_state import OrchestrationStateStore
        try:
            snapshot = await asyncio.to_thread(OrchestrationStateStore().get)
        except (OSError, ValueError, RuntimeError):
            return
        current = next(
            (row for row in snapshot.get("tasks", []) if row.get("id") == self._mission_task_id),
            None,
        )
        if current and current.get("status") in {"completada", "fallida", "cancelada"}:
            self._mission_task_id = None
            self._mission_id = None
            self._mission_lease_lost = False
            heartbeat = self._mission_heartbeat_task
            if heartbeat is not None and not heartbeat.done():
                heartbeat.cancel()
            self._mission_heartbeat_task = None

    # ── Hooks ─────────────────────────────────────────────────────────

    def attach_hooks(
        self,
        registry: Any,
        *,
        session_id: str = "",
    ) -> None:
        """Attach a :class:`lilith_core.hooks.HookRegistry` to this session.

        Once attached, every call to :meth:`execute_tool` fires
        ``pre_tool_call`` (gate / rewrite args) and ``post_tool_call``
        (rewrite / suppress result) hooks. Inspired by Talon's tool
        gating + SmartToolRouter's hook integration.

        Parameters
        ----------
        registry:
            A :class:`lilith_core.hooks.HookRegistry` instance. ``None``
            disables hook integration.
        session_id:
            Optional session id stamped onto every fired hook context.
        """
        dispatcher = self._get_hook_dispatcher()
        dispatcher.attach(registry, session_id=session_id)
        self._sync_hook_state(dispatcher)
        logger.info(
            "HookRegistry attached to AgentSession (session_id=%s)",
            session_id or "<none>",
        )

    def _get_hook_dispatcher(self) -> ToolHookDispatcher:
        """Return the dispatcher, restoring legacy manually-built sessions.

        Some embedders and tests construct ``AgentSession`` through ``__new__``
        and populate its historical private fields directly. Mirroring those
        fields here preserves that compatibility seam.
        """
        registry = getattr(self, "_hook_registry", None)
        session_id = getattr(self, "_session_id", "")
        failures = getattr(self, "_hook_failures", 0)
        dispatcher = getattr(self, "_hook_dispatcher", None)
        if dispatcher is None:
            dispatcher = ToolHookDispatcher(
                registry,
                session_id=session_id,
                failures=failures,
            )
            self._hook_dispatcher = dispatcher
        else:
            dispatcher.registry = registry
            dispatcher.session_id = session_id
            dispatcher.failures = max(dispatcher.failures, failures)
        return dispatcher

    def _sync_hook_state(self, dispatcher: ToolHookDispatcher) -> None:
        """Mirror dispatcher state for backwards-compatible telemetry access."""
        self._hook_registry = dispatcher.registry
        self._session_id = dispatcher.session_id
        self._hook_failures = dispatcher.failures

    def _fire_pre_tool_hook(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Fire ``pre_tool_call`` hooks. Returns (allowed, effective_params).

        ``allowed=False`` means a hook gated the execution. Hooks may also
        rewrite ``params`` by mutating the HookContext data dict.
        """
        dispatcher = self._get_hook_dispatcher()
        outcome = dispatcher.fire_pre(
            tool_name,
            params,
            agent_name=getattr(self.config, "model", "lilith-cli"),
        )
        self._sync_hook_state(dispatcher)
        return outcome

    def _fire_post_tool_hook(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: Any,
    ) -> Any:
        """Fire ``post_tool_call`` hooks. Returns the (possibly modified) result.

        Returning ``None`` from the registry suppresses the result; we
        translate that into a synthetic error ``ToolResult`` so the
        conversation loop still receives structured feedback.
        """
        dispatcher = self._get_hook_dispatcher()
        outcome = dispatcher.fire_post(
            tool_name,
            params,
            result,
            agent_name=getattr(self.config, "model", "lilith-cli"),
        )
        self._sync_hook_state(dispatcher)
        return outcome

    def session_duration(self) -> float:
        """Return elapsed session duration in seconds."""
        return (datetime.now(UTC) - self._session_start).total_seconds()

    @property
    def session_start(self) -> datetime:
        """Return session start time (UTC)."""
        return self._session_start

    def _init_memory(self) -> None:
        """Initialise the memory store if *lilith_memory* is available."""
        try:
            from pathlib import Path

            from lilith_memory.store import MemoryStore

            db_path = Path(self.config.memory.db_path).expanduser()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._memory = MemoryStore(db_path)
            logger.info("Memoria inicializada: %s", db_path)
        except ImportError:
            logger.warning("lilith_memory no disponible — memoria deshabilitada")
            self._memory = None

    @property
    def memory(self) -> Any:
        return self._memory

    def _load_project_instructions(self) -> str:
        """Reload inherited instructions from broadest to most specific each turn.

        A nested AGENTS.md supplements its parents. Local Lilith guidance is
        appended, so it cannot silently hide shared project rules. Reloading also
        prevents stale guidance after /cd or an instruction edit during a session.
        """


        try:
            cwd = Path.cwd()
            local_path = cwd / ".lilith" / "CLAUDE.md"
            global_path = Path.home() / ".lilith" / "CLAUDE.md"
            paths = [
                directory / "AGENTS.md"
                for directory in reversed((cwd, *cwd.parents))
                if (directory / "AGENTS.md").is_file()
            ]
            if local_path.is_file():
                paths.append(local_path)
            if not paths and global_path.is_file():
                paths.append(global_path)
            sections = [
                f"--- Instrucciones: {path} ---\n{path.read_text(encoding='utf-8-sig')}"
                for path in paths
            ]
            instructions = "\n\n".join(sections)
        except (OSError, UnicodeError) as exc:
            self._project_instructions = None
            raise RuntimeError(
                "No se pudieron leer las instrucciones del proyecto; "
                "corrige el acceso o la codificación antes de continuar."
            ) from exc

        self._project_instructions = instructions
        return instructions

    # ── Tools ───────────────────────────────────────────────────────

    def _init_tools(self) -> None:
        """Load tools from *lilith_tools* based on config flags."""
        try:
            # Force registration of all tool classes.
            from lilith_tools import ToolRegistry, filesystem, system  # noqa: F401

            from .mission import tools as mission_tools  # noqa: F401

            with contextlib.suppress(ImportError):
                from lilith_tools import browser, coding, web_search  # noqa: F401

            self._tool_registry = ToolRegistry
        except ImportError:
            logger.warning("lilith_tools no disponible — herramientas deshabilitadas")
            self._tool_registry = None

    def get_tool_descriptions(self) -> list[dict[str, Any]]:
        """Return a list of tool description dicts (name, description,
        parameters) for currently enabled tools.
        """
        if not self._tools_enabled:
            self._tools_cache = []
            return self._tools_cache
        if self._tools_cache is not None:
            return self._tools_cache

        self._init_tools()
        if self._tool_registry is None:
            self._tools_cache = []
            return self._tools_cache

        tools: list[dict[str, Any]] = []
        all_tools = self._tool_registry.list_tools()

        # Map tool categories to their tool names.
        category_map: dict[str, list[str]] = {
            "filesystem": ["file_read", "directory_list"],
            "coding": ["coding"],
            "web_search": ["web_search"],
            "browser": ["browser"],
            "system": ["system"],
        }

        from .agent_modes import tool_capability
        from .qualification import allows_tool
        calibrated_for_edits = allows_tool(self.config, "file_write")
        profile_allowed = getattr(self, "_profile_allowed_tools", None)
        profile_prefixes = getattr(self, "_profile_allowed_prefixes", ())
        for name, description in all_tools.items():
            if profile_allowed is not None and name not in profile_allowed:
                if not any(name.startswith(prefix) for prefix in profile_prefixes):
                    continue
            if not calibrated_for_edits and tool_capability(name) != "read":
                continue
            from .agent_modes import get_agent_mode, mode_allows_tool

            mode = get_agent_mode(self.agent_mode)
            if mode is None or not mode_allows_tool(mode, name):
                continue
            # Check if this tool's category is enabled.
            enabled = True
            for category, names in category_map.items():
                if name in names:
                    enabled = getattr(self.config.tools, category, True)
                    break

            if not enabled:
                continue

            # User override via /tools disable.
            if name in self._disabled_tools:
                continue

            tool_cls = self._tool_registry.get(name)
            params = tool_cls.parameters if tool_cls else {}
            tools.append(
                {
                    "name": name,
                    "description": description,
                    "parameters": params,
                },
            )

        self._tools_cache = tools
        return self._tools_cache

    def _all_tool_names(self) -> set[str]:
        """Return every tool name known to the registry, regardless of enable state."""
        self._init_tools()
        if self._tool_registry is None:
            return set()
        return set(self._tool_registry.list_tools().keys())

    def enable_tool(self, name: str) -> None:
        """Re-enable a previously disabled tool name."""
        self._disabled_tools.discard(name)
        self._tools_cache = None

    def disable_tool(self, name: str) -> None:
        """Disable a tool by name so it is no longer exposed to the LLM."""
        self._disabled_tools.add(name)
        self._tools_cache = None

    def get_openai_tools(self) -> list[dict[str, Any]]:
        """Return tools in OpenAI function-calling format."""
        return lilith_tools_to_openai(self.get_tool_descriptions())

    def _repair_tool_name(self, concatenated_name: str) -> list[str]:
        """Try to split a concatenated tool name into valid tool names.

        Some models (e.g. GLM-5.1) concatenate multiple tool names into one,
        e.g. ``system_infodirectory_list`` → [``system_info``, ``directory_list``].
        This method tries all possible splits and returns the one where every
        segment matches a known tool name.
        """
        known = set(self._tool_registry.list_tools().keys()) if self._tool_registry else set()
        if not known:
            return [concatenated_name]

        # Try every possible left-prefix that is a valid tool name,
        # then recursively split the remainder.
        def _split(name: str) -> list[list[str]]:
            results: list[list[str]] = []
            for tool in known:
                if name.startswith(tool):
                    remainder = name[len(tool) :]
                    if not remainder:
                        results.append([tool])
                    else:
                        for sub in _split(remainder):
                            results.append([tool, *sub])
            return results

        splits = _split(concatenated_name)
        if splits:
            # Prefer the split with the fewest segments (most specific match).
            splits.sort(key=len)
            return splits[0]
        return [concatenated_name]

    @staticmethod
    def _is_transient_error(error_text: str) -> bool:
        """Check whether an error message indicates a transient failure."""
        lowered = error_text.lower()
        return any(
            keyword in lowered
            for keyword in ("timeout", "connection", "network", "5xx", "rate limit")
        )

    async def execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """Execute a single tool call and return the result."""
        import time as _time

        self._init_tools()

        tool_name = tool_call.name
        tool_args = tool_call.arguments
        from .qualification import allows_tool
        if not allows_tool(self.config, tool_name):
            return ToolResult(tool_call.id, tool_name, "Error: este modelo necesita calibración vigente antes de ejecutar acciones mutantes.")
        if not isinstance(tool_args, dict):
            return ToolResult(tool_call.id, tool_name, "Error: los argumentos deben ser un objeto JSON.")
        if getattr(self, "_strict_tool_arguments", False):
            from jsonschema import ValidationError, validate
            schema = next((tool["function"]["parameters"] for tool in self.get_openai_tools()
                           if tool["function"]["name"] == tool_name), None)
            if schema is None:
                return ToolResult(tool_call.id, tool_name, "Error: herramienta fuera del perfil activo.")
            try:
                validate(tool_args, schema)
            except ValidationError as exc:
                return ToolResult(tool_call.id, tool_name, f"Error: argumentos inv\u00e1lidos ({exc.validator}); revisa el esquema de la herramienta.")

        from .agent_modes import tool_capability
        if getattr(self, "_mission_lease_lost", False) and tool_capability(tool_name) != "read":
            return ToolResult(
                tool_call.id,
                tool_name,
                "Error: mission lease ownership was lost; mutating tools are blocked until reconciliation.",
            )
        if tool_name == "mission_prepare":
            if getattr(self, "_mission_task_id", None):
                return ToolResult(
                    tool_call.id,
                    tool_name,
                    f"Error: session already owns active mission {getattr(self, '_mission_task_id', None)}.",
                )
            tool_args = dict(
                tool_args,
                _lease_owner=self._mission_owner,
                _lease_seconds=self._mission_lease_seconds,
            )
            tool_call.arguments = tool_args

        if tool_name in getattr(self, "_disabled_tools", set()):
            return ToolResult(tool_call_id=tool_call.id, name=tool_name,
                              content="Error: herramienta deshabilitada para esta sesión.")
        allowed_files = getattr(self, "_allowed_file_paths", None)
        if allowed_files is not None:
            candidate = Path(str(tool_args.get("path", ""))).expanduser().resolve()
            if tool_name not in ("file_read", "file_write", "file_edit", "file_append") or candidate not in allowed_files:
                return ToolResult(tool_call_id=tool_call.id, name=tool_name,
                                  content="Error: archivo o herramienta fuera del alcance de esta tarea.")

        if not self._tools_enabled:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_name,
                content="Error: las herramientas están deshabilitadas por --no-tools.",
            )
        if tool_name in ("vor_delegate", "huginn_delegate"):
            import uuid
            bindings = getattr(self, "_delegation_request_ids", None)
            if bindings is None:
                bindings = self._delegation_request_ids = {}
            identity = (tool_name, tool_call.id)
            requested = tool_args.get("request_id")
            if requested is not None and (not isinstance(requested, str) or len(requested) != 32 or any(char not in "0123456789abcdef" for char in requested)):
                return ToolResult(tool_call.id, tool_name, "Error: request_id debe contener 32 hex minúsculas.")
            previous = bindings.get(identity)
            if previous is not None and requested is not None and requested != previous:
                return ToolResult(tool_call.id, tool_name, "Error: la llamada ya está ligada a otra request_id.")
            from .delegation_keys import automatic_request_id
            namespace = getattr(self, "_delegation_namespace", None)
            if namespace is None:
                namespace = self._delegation_namespace = uuid.uuid4().hex
            try:
                request_id = previous or requested or automatic_request_id(namespace, tool_name, tool_args, str(Path.cwd()))
            except (ValueError, TypeError):
                return ToolResult(tool_call.id, tool_name, "Error: no se pudo identificar la petición de delegación.")
            bindings[identity] = request_id
            tool_args = dict(tool_args, request_id=request_id)
            tool_call.arguments = tool_args
        signature = json.dumps(
            {"name": tool_name, "arguments": tool_args},
            sort_keys=True, ensure_ascii=False, default=str,
        )
        import hashlib
        receipt_key = hashlib.sha256(signature.encode("utf-8")).hexdigest()
        receipt = getattr(self, "_replay_receipts", {}).get(receipt_key)
        if receipt is not None:
            target = Path(receipt["path"])
            if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == receipt["sha256"]:
                return ToolResult(tool_call_id=tool_call.id, name=tool_name,
                                  content=receipt["result"] + " (recuperado: no se repitió la escritura)")
            return ToolResult(tool_call_id=tool_call.id, name=tool_name,
                              content="Error: el archivo cambió desde el checkpoint; inspección requerida antes de repetir la escritura.")
        if (
            signature == getattr(self, "_last_failed_tool_signature", None)
            and getattr(self, "_identical_tool_failures", 0) >= 2
        ):
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_name,
                content=(
                    "Error: misma llamada fallo 2 veces; cambia de estrategia "
                    "o pregunta al usuario"
                ),
            )

        if tool_name in ("vor_delegate", "huginn_delegate") and getattr(getattr(self.config, "history", None), "save", False):
            from .repl import _auto_save_conversation
            try:
                saved = _auto_save_conversation(self)
            except (OSError, ValueError, TypeError):
                saved = None
            if saved is None:
                return ToolResult(tool_call.id, tool_name,
                    "Error: no se pudo guardar la identidad antes de delegar; no se lanzó el colaborador.")

        # Pre-tool-call hook
        try:
            self._run_hook(
                "pre-tool-call",
                {
                    "LILITH_TOOL_NAME": tool_name,
                    "LILITH_TOOL_ARGS": json.dumps(tool_args, default=str),
                },
            )
        except Exception:
            pass

        start = _time.perf_counter()
        result = await self._execute_tool_impl(tool_call)
        if tool_name == "mission_prepare":
            await self._adopt_mission_prepare_result(result)
        elif tool_name == "mission_complete":
            await self._refresh_mission_after_complete()
        duration = _time.perf_counter() - start
        result_limit = getattr(self, "_tool_result_char_limit", 0)
        if result_limit and len(result.content) > result_limit:
            compact = None
            if tool_name in ("vor_delegate", "huginn_delegate"):
                is_error = result.content.startswith("Error: ")
                try:
                    payload = json.loads(result.content[7:] if is_error else result.content)
                    recovery = payload.get("recovery", payload)
                    fields = {key: recovery[key] for key in (
                        "status", "reference", "reference_saved", "observation_saved", "job_id",
                        "effects_unknown", "retry_safe",
                    ) if key in recovery}
                    fields["output_omitted"] = True
                    compact = ("Error: " if is_error else "") + json.dumps(fields, ensure_ascii=True)
                except (ValueError, TypeError, AttributeError):
                    pass
            fallback = ("Error: resultado demasiado grande para el perfil. Consulta jobs recent para delegaciones; no asumas el contenido omitido."
                        if tool_name in ("vor_delegate", "huginn_delegate") else
                        "Error: resultado demasiado grande para el perfil. Lee menos líneas con start_line y max_lines; no asumas el contenido omitido.")
            result = ToolResult(tool_call.id, tool_name, compact if compact and len(compact) <= result_limit else fallback)

        if (getattr(self, "_progress_enabled", False) and
                tool_name in ("file_write", "file_edit", "file_append") and
                not result.content.startswith("Error") and
                (not self.config.confirm_write or tool_args.get("show_diff") is False)):
            target = Path(str(tool_args.get("path", ""))).resolve()
            if target.is_file():
                if not hasattr(self, "_tool_receipts"):
                    self._tool_receipts = {}
                self._tool_receipts[receipt_key] = {"path": str(target),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "result": result.content[:1000]}

        failed = result.content.startswith("Error:")
        last_signature = getattr(self, "_last_failed_tool_signature", None)
        if failed:
            if signature == last_signature:
                self._identical_tool_failures = getattr(self, "_identical_tool_failures", 0) + 1
            else:
                self._last_failed_tool_signature = signature
                self._identical_tool_failures = 1
        else:
            self._last_failed_tool_signature = None
            self._identical_tool_failures = 0

        self._tool_call_history.append(
            {
                "name": tool_name,
                "arguments": tool_args,
                "duration": duration,
                "timestamp": datetime.now(UTC).isoformat(),
                "success": not result.content.startswith("Error:"),
            },
        )

        # Track destructive file edits separately.
        if tool_name in ("file_write", "file_edit") and not result.content.startswith("Error:"):
            path_arg = tool_args.get("path") if isinstance(tool_args, dict) else None
            if path_arg:
                self._file_edit_history.append(
                    {
                        "path": str(path_arg),
                        "tool": tool_name,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )

        # Post-tool-call hook
        try:
            self._run_hook(
                "post-tool-call",
                {
                    "LILITH_TOOL_NAME": tool_name,
                    "LILITH_TOOL_RESULT": result.content[:500],
                },
            )
        except Exception:
            pass

        return result

    async def _execute_tool_impl(self, tool_call: ToolCall) -> ToolResult:
        """Original implementation of execute_tool, refactored so telemetry can wrap it."""
        tool_name = tool_call.name
        tool_args = tool_call.arguments

        if self._tool_registry is None:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_name,
                content="Error: herramientas no disponibles (lilith_tools no instalado)",
            )

        tool_cls = self._tool_registry.get(tool_name)

        # If tool name not found, try to repair concatenated names.
        if tool_cls is None:
            repaired = self._repair_tool_name(tool_name)
            if len(repaired) > 1 and all(self._tool_registry.get(n) for n in repaired):
                logger.info(
                    "Repaired concatenated tool name: %s → %s",
                    tool_name,
                    repaired,
                )
                # Return a hint so the caller can re-dispatch.
                return ToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_name,
                    content=(
                        f"Error: tool '{tool_name}' was a concatenation of "
                        f"{repaired}. Please call each tool separately: " + ", ".join(repaired)
                    ),
                )

            # Suggest similar tool names when the tool is unknown.
            known_tools = list(self._tool_registry.list_tools().keys())
            suggestions = difflib.get_close_matches(tool_name, known_tools, n=3, cutoff=0.6)
            suggestion_text = ""
            if suggestions:
                suggestion_text = f". Herramientas similares: {', '.join(suggestions)}"

            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_name,
                content=f"Error: herramienta desconocida '{tool_name}'{suggestion_text}",
            )

        from .agent_modes import get_agent_mode, mode_allows_tool

        active_mode = getattr(self, "agent_mode", "default")
        mode = get_agent_mode(active_mode)
        if mode is None or not mode_allows_tool(mode, tool_name):
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_name,
                content=(
                    f"Error: el modo {active_mode} deniega la capacidad mutante "
                    f"de '{tool_name}'."
                ),
            )

        # ── retry settings ────────────────────────────────────────────────
        retry_count = 2
        retry_backoff = 1.0
        tools_config = getattr(self.config, "tools", None)
        if tools_config is not None:
            retry_count = getattr(tools_config, "retry_count", 2) or 2
            retry_backoff = getattr(tools_config, "retry_backoff", 1.0) or 1.0
        if not isinstance(retry_count, int) or retry_count < 0:
            retry_count = 2
        if getattr(tool_cls, "allow_automatic_retry", True) is False:
            retry_count = 0
        if retry_backoff is None or not isinstance(retry_backoff, (int, float)) or retry_backoff <= 0:
            retry_backoff = 1.0

        # Per-tool timeout: default to the configured tool timeout (30s).
        tool_timeout = 30
        if tools_config is not None:
            tool_timeout = getattr(tools_config, "tool_timeout", 30) or 30
        if tool_timeout is None or not isinstance(tool_timeout, (int, float)) or tool_timeout <= 0:
            tool_timeout = 30
        # Tool classes may declare a longer floor via ``timeout_seconds``
        # (e.g. delegate_subagent waits on a full sub-agent run).
        cls_timeout = getattr(tool_cls, "timeout_seconds", None)
        if isinstance(cls_timeout, (int, float)) and cls_timeout > tool_timeout:
            tool_timeout = cls_timeout

        last_error = ""
        for attempt in range(retry_count + 1):
            try:
                tool_instance = tool_cls()

                # ── Destructive-write confirmation / diff-preview policy (v6.6) ──
                # file_write and file_edit are destructive. When confirm_write is enabled
                # (the default) and the caller has not explicitly requested a dry-run diff,
                # rewrite the arguments to show_diff=True so the LLM sees a preview before
                # the file is touched. The LLM can then call the tool again without the
                # preview flag to actually apply the change.
                #
                # In review-only mode, writes are always previewed and the tool is told
                # it cannot apply the change.
                if tool_name in ("file_write", "file_edit"):
                    allow_writes = getattr(self, "_agent_allow_writes", True)
                    if not allow_writes:
                        return ToolResult(
                            tool_call_id=tool_call.id,
                            name=tool_name,
                            content=(
                                "Error: el modo review-only no permite escrituras. "
                                "Solo podés leer, analizar y sugerir cambios."
                            ),
                        )

                    confirm_write = getattr(self.config, "confirm_write", True)
                    # Auto-execute: if the tool args match a pre-approved pattern,
                    # skip the diff preview and write directly.
                    if (
                        self._auto_execute
                        and self._matches_auto_pattern(tool_name, tool_args)
                    ):
                        confirm_write = False

                    # Only rewrite if the caller did not explicitly opt in to writing
                    # by passing show_diff=False. ``is None`` distinguishes "not set"
                    # from "explicitly False" (the LLM's opt-in to actually write).
                    if (
                        confirm_write
                        and "show_diff" not in tool_args
                    ):
                        tool_args = dict(tool_args)
                        tool_args["show_diff"] = True
                        logger.info(
                            "Diff-preview enforced for '%s' (call_id=%s)",
                            tool_name,
                            tool_call.id,
                        )

                # ── pre_tool_call hooks: gate / rewrite args ────────────────
                allowed, effective_args = self._fire_pre_tool_hook(
                    tool_name=tool_name,
                    params=dict(tool_args),
                )
                if not allowed:
                    logger.info(
                        "Tool '%s' execution gated by pre_tool_call hook (call_id=%s)",
                        tool_name,
                        tool_call.id,
                    )
                    return ToolResult(
                        tool_call_id=tool_call.id,
                        name=tool_name,
                        content=(
                            f"Error: tool '{tool_name}' gated by pre_tool_call hook "
                            f"(session_id={self._session_id or '<none>'})"
                        ),
                    )

                # Use the (possibly) rewritten args from the hook
                tool_args = effective_args
                if tool_name in ("vor_delegate", "huginn_delegate"):
                    bound = tool_call.arguments.get("request_id")
                    if tool_args.get("request_id", bound) != bound:
                        return ToolResult(tool_call.id, tool_name, "Error: el hook cambió la identidad de la delegación.")
                    tool_args = dict(tool_args, request_id=bound)

                timeout_policy = getattr(tool_cls, "timeout_for_arguments", None)
                if callable(timeout_policy):
                    tool_timeout = timeout_policy(tool_args)

                result = await asyncio.wait_for(
                    asyncio.to_thread(tool_instance.execute, **tool_args),
                    timeout=float(tool_timeout),
                )
                if result.success:
                    content = (
                        json.dumps(result.data, ensure_ascii=False, default=str)
                        if not isinstance(result.data, str)
                        else result.data
                    )
                    break
                else:
                    content = f"Error: {result.error}"
                    recovery_fields = getattr(tool_cls, "failure_metadata_fields", ())
                    if recovery_fields and isinstance(result.data, dict):
                        recovery = {key: result.data[key] for key in recovery_fields if key in result.data}
                        content = "Error: " + json.dumps({"recovery": recovery, "error": result.error}, ensure_ascii=False)
                    last_error = result.error or ""
                    if not self._is_transient_error(last_error) or attempt == retry_count:
                        break
                    wait = retry_backoff * (2 ** attempt)
                    logger.warning(
                        "Transient error ejecutando '%s' (intento %d/%d): %s. Reintentando en %.1fs...",
                        tool_name,
                        attempt + 1,
                        retry_count + 1,
                        last_error,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    continue
            except TimeoutError:
                content = f"Error: tool '{tool_name}' excedió el timeout de {tool_timeout}s"
                if getattr(tool_cls, "allow_automatic_retry", True) is False:
                    content += "; el trabajo puede seguir activo. Consulta cli_jobs_recent y cli_job_reference; no relances sin reconciliar."
                last_error = content
                if attempt == retry_count:
                    result = None
                    break
                wait = retry_backoff * (2 ** attempt)
                logger.warning(
                    "Timeout ejecutando '%s' (intento %d/%d). Reintentando en %.1fs...",
                    tool_name,
                    attempt + 1,
                    retry_count + 1,
                    wait,
                )
                await asyncio.sleep(wait)
                continue
            except Exception as exc:
                content = f"Error ejecutando {tool_name}: {exc}"
                logger.exception("Tool execution error: %s", tool_name)
                result = None  # type: ignore[assignment]
                break

        tool_result = ToolResult(tool_call_id=tool_call.id, name=tool_name, content=content)

        # ── post_tool_call hooks: rewrite / suppress result ─────────────
        # Only fire when the tool actually executed (i.e. we have a result object).
        if result is not None:
            tool_result = self._fire_post_tool_hook(
                tool_name=tool_name,
                params=tool_args,
                result=tool_result,
            )

            if not isinstance(tool_result, ToolResult):
                logger.warning(
                    "post_tool_call hook for '%s' returned non-ToolResult; wrapping",
                    tool_name,
                )
                tool_result = ToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_name,
                    content=str(tool_result),
                )

        # Truncate oversized results to protect the conversation context.
        # The original size is preserved in the message so the LLM knows
        # what was clipped.
        if len(tool_result.content) > _MAX_TOOL_RESULT_CHARS:
            original_len = len(tool_result.content)
            tool_result.content = (
                tool_result.content[:_MAX_TOOL_RESULT_CHARS]
                + f"\n\n[…truncado: {original_len:,} chars → {_MAX_TOOL_RESULT_CHARS:,} chars. Usa search_files/grep/offset para acceder al resto.]"
            )
            logger.info(
                "Truncated tool result for %s: %d → %d chars",
                tool_name,
                original_len,
                len(tool_result.content),
            )

        return tool_result

    # ── History management ──────────────────────────────────────────

    def clear_history(self) -> None:
        """Reset conversation history (excluding system prompt)."""
        self.history.clear()
        self._total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def compact_history(self, summary: str, keep_recent: int = 2) -> None:
        """Replace conversation history with a summary + recent messages.

        This is called by /compact to reduce token usage while preserving
        context. The summary replaces older messages, and the last
        ``keep_recent`` exchanges (user+assistant pairs) are kept verbatim.

        Parameters
        ----------
        summary:
            A compressed summary of the conversation so far.
        keep_recent:
            Number of recent user+assistant *pairs* to keep (default 2).

        """
        # Calculate how many messages to keep from the end.
        # Each "pair" is typically 2 messages (user + assistant),
        # but tool calls can add tool_result messages, so we scan
        # backwards counting user messages.
        keep_count = 0
        pairs_found = 0
        for i in range(len(self.history) - 1, -1, -1):
            if self.history[i].get("role") == "user":
                pairs_found += 1
                if pairs_found > keep_recent:
                    break
            keep_count += 1

        recent_messages = self.history[-keep_count:] if keep_count > 0 else []

        # Build the compacted history: assistant summary as context + recent messages.
        self.history = [
            {"role": "assistant", "content": f"[Resumen de la conversación anterior]\n{summary}"},
            *recent_messages,
        ]

        logger.info(
            "Historial compactado: %d mensajes → %d (1 resumen + %d recientes)",
            len(self.history) + keep_count,
            len(self.history),
            len(recent_messages),
        )

    async def generate_compact_summary(self) -> str:
        """Ask the LLM to summarize the current conversation history.

        Returns a concise summary suitable for replacing older messages
        in the history, freeing up context tokens.
        """
        if not self.history:
            return ""

        # Build a text representation of the conversation.
        lines = []
        for msg in self.history:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if role == "system":
                continue  # Skip system prompts — they're in every request anyway.
            # Truncate very long messages to keep the summary request manageable.
            if len(content) > 500:
                content = content[:500] + "…[truncado]"
            lines.append(f"{role.upper()}: {content}")

        conversation_text = "\n".join(lines)

        summary_prompt = (
            "Resume la siguiente conversación de forma concisa y completa. "
            "Incluye: decisiones tomadas, archivos modificados, comandos ejecutados, "
            "resultados clave, y cualquier contexto importante que pueda necesitarse "
            "para continuar la conversación. Sé específico con nombres de archivos, "
            "rutas, y valores. No incluyas saludos ni detalles irrelevantes. "
            "Responde en español.\n\n"
            f"CONVERSACIÓN:\n{conversation_text}\n\n"
            "RESUMEN:"
        )

        # Use a temporary session — don't add to history.
        temp_messages = [
            {
                "role": "system",
                "content": (
                    "Eres un asistente que resume conversaciones de forma concisa y precisa."
                ),
            },
            {"role": "user", "content": summary_prompt},
        ]

        response = await self.provider.complete(temp_messages, tools=None)
        return response.get("content", "").strip()

    def _build_messages(self) -> list[dict[str, Any]]:
        """Build the full message list to send to the LLM."""
        # Repair conversations restored from disk before the fix, or content
        # inserted by integrations that bypass the public message methods.
        self.history = sanitize_unicode(self.history)
        messages: list[dict[str, Any]] = [
            Message.system(
                f"{self.system_prompt}\n\n{DECISION_SUPPORT_INSTRUCTIONS}"
            )
        ]

        # /goal metadata lives in history so /save and /resume preserve it,
        # while the model sees it as part of the root system prompt.
        from .extra_commands import _GOAL_MARKER, _goal_from_session

        session_goal = _goal_from_session(self)
        goal_extra = ""
        if session_goal is not None:
            goal_status = session_goal["status"]
            goal_instruction = {
                "active": (
                    "Keep this objective in focus. Do not claim completion "
                    "until the relevant verification tools confirm the result."
                ),
                "paused": "This objective is paused; do not continue it until resumed.",
                "completed": "This objective is completed; do not restart it implicitly.",
            }[goal_status]
            goal_extra = (
                f"\n\nSESSION GOAL ({goal_status}): "
                f"{session_goal['objective']}\n{goal_instruction}"
            )

        # Add tool descriptions into the system prompt.
        tools_desc = self.get_tool_descriptions()
        if tools_desc and self._tools_enabled:
            tool_lines = "\n".join(f"- {t['name']}: {t['description']}" for t in tools_desc)
            # Safety + workflow guidance. The LLM should know about
            # the diff-preview policy, agent mode, and the available slash commands.
            from .agent_modes import get_agent_mode

            mode = get_agent_mode(getattr(self, "agent_mode", "default"))
            extras = ""
            if mode is not None and mode.system_prompt_extra:
                extras += mode.system_prompt_extra

            # The prompt must describe the SAME knob the executor enforces:
            # execute_tool() gates the diff-preview on config.confirm_write
            # (agent modes sync their confirm_write into the config), so a
            # config with confirm_write=false must NOT advertise the
            # two-step preview protocol — models stall deliberating over a
            # policy that is not actually in effect.
            if getattr(self.config, "confirm_write", True):
                extras += (
                    "\n\nSAFETY: file_write and file_edit are guarded by a "
                    "diff-preview policy. Your FIRST call to these tools "
                    "returns a unified diff WITHOUT writing the file. "
                    "After reviewing the diff, call the tool again with "
                    "`show_diff=False` to actually apply the change. "
                    "This two-step pattern is how the user previews edits."
                )
            else:
                extras += (
                    "\n\nAUTO-EDIT: file_write and file_edit apply changes "
                    "directly without requiring a diff preview first."
                )

            extras += (
                "\n\nVERIFICATION: a task is only DONE when its tool result "
                "confirms success. If a tool returned an error, the task "
                "FAILED — do not mark its todo as done and do not report it "
                "as completed; state the exact error instead."
            )
            if hasattr(self, "current_plan") and self.current_plan is not None:
                plan = self.current_plan
                pending = plan.next_pending()
                if pending is not None:
                    extras += (
                        f"\n\nACTIVE PLAN ({plan.goal}):\n"
                        f"  Currently working on step {pending.number}: "
                        f"{pending.description}\n"
                        "Mark steps complete with /plan done <n>."
                    )
            extras += goal_extra
            messages[0]["content"] += (
                f"\n\nYou have access to the following tools:\n{tool_lines}"
                f"{extras}\n\n"
                "IMPORTANT: Call each tool by its EXACT name shown above. "
                "Do NOT combine or concatenate multiple tool names into one call. "
                "If you need multiple tools, call them one at a time — "
                "wait for each result before calling the next. "
                "You can also batch independent reads (e.g. several file_read) "
                "in a single response and the system will run them in parallel. "
                "When you have enough information to answer directly, do so."
            )
        elif goal_extra:
            messages[0]["content"] += goal_extra

        # Inject project-local instructions from .lilith/CLAUDE.md.
        project_instructions = self._load_project_instructions()
        if project_instructions:
            messages[0]["content"] += (
                f"\n\nPROJECT INSTRUCTIONS:\n{project_instructions}"
            )

        # Trim history to max_turns. The /goal metadata is persisted in history
        if getattr(self, "_progress_enabled", False) and self.config.memory.enabled:
            from pathlib import Path

            from .work_memory import context
            messages[0]["content"] += context(Path(self._project_root))

        # Trim history to max_turns. The /goal metadata is persisted in history
        # but already merged into the root system prompt above.
        max_turns = self.config.history.max_turns
        visible_history = [
            message
            for message in self.history
            if not (
                isinstance(message, dict)
                and message.get("role") == "system"
                and str(message.get("content", "")).startswith(_GOAL_MARKER)
            )
        ]
        history = visible_history[-max_turns * 2 :]
        # El corte por cantidad de mensajes no limita el PESO. Con herramientas
        # un turno son muchos mensajes, asi que 100 entradas pueden ser 200 K
        # tokens reenviados en cada iteracion. Techo de tamano real:
        _hcfg = getattr(self.config, "history", None)
        history = _trim_history_to_budget(
            history,
            int(getattr(_hcfg, "max_chars", 0) or _DEFAULT_HISTORY_CHAR_BUDGET),
            int(
                getattr(_hcfg, "max_tool_result_chars", 0)
                or _DEFAULT_TOOL_RESULT_CHAR_CAP
            ),
        )
        # El truncamiento por max_turns corta por cantidad de mensajes, sin
        # respetar los grupos assistant(tool_calls)→tool_results: puede dejar un
        # tool result sin su assistant (huérfano) o un assistant con un grupo de
        # tools incompleto. El proveedor rechaza ambos con "tool_call_id is not
        # found" (400) y la sesión interactiva crashea. Se reparan acá.
        history = _drop_orphan_tool_messages(history)

        messages.extend(history)
        limit = getattr(self, "_context_char_limit", 0)
        if limit and len(json.dumps(messages, ensure_ascii=False)) > limit:
            raise ValueError(f"Contexto excede el l\u00edmite de {limit} caracteres del perfil. Reduce el encargo o usa standard; no se descartaron instrucciones silenciosamente.")
        return messages

    # ── Main processing loop ────────────────────────────────────────

    async def process_message(
        self,
        text: str,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        """Process a user message through the full loop.

        1. Add user message to history
        2. Send to LLM
        3. If LLM returns tool_calls, execute them and loop
        4. Return final assistant text

        Parameters
        ----------
        text:
            The user message.
        cancel_event:
            Optional external asyncio.Event. When set, the current tool loop
            checks it between iterations and returns an empty string instead
            of continuing. If not provided, the session's internal event is used
            (set by :meth:`cancel`).

        Returns the final text response from the assistant.
        """
        text = sanitize_text(text)
        self._cancel_event = cancel_event or asyncio.Event()
        self.history.append(Message.user(text))
        self._last_user_message = text

        messages = self._build_messages()
        tools = (
            self.get_openai_tools()
            if self._tools_enabled and self.get_tool_descriptions()
            else None
        )

        # Tool-calling loop. ``max_iterations`` is configurable via
        # ``YggdrasilConfig.max_iterations`` (default 10); the last
        # iteration receives a soft-warning system message asking
        # the model to wrap up so we can fall back to a no-tools
        # closing summary if the loop still has pending tool calls.
        max_iterations = getattr(self.config, "max_iterations", 10) or 10
        _iter_idx = 0
        for _ in range(max_iterations):
            response_format = {"type": "json_object"} if getattr(self, "_json_mode", False) else None
            response = await self.provider.complete(messages, tools=tools, response_format=response_format)

            # Track usage.
            usage = response.get("usage", {})
            model_used = response.get("model") or self.config.model
            self._track_usage(usage, model_used)

            content = response.get("content", "")
            tool_calls: list[ToolCall] = response.get("tool_calls", [])

            if not tool_calls:
                # Text-only length truncation: continue up to twice and stitch.
                continuations = 0
                while response.get("finish_reason") == "length" and continuations < 2:
                    continuations += 1
                    continuation = await self.provider.complete(
                        [
                            *messages,
                            Message.assistant(content),
                            Message.user("continua exactamente donde quedaste"),
                        ],
                        tools=None,
                        response_format=response_format,
                    )
                    self._track_usage(
                        continuation.get("usage", {}),
                        continuation.get("model") or self.config.model,
                    )
                    content += continuation.get("content", "")
                    response = continuation
                self._last_auto_continuations = continuations
                if continuations:
                    content += f"\n\n[continuación automática: {continuations}]"
                self.history.append(Message.assistant(content))
                return content

            # Auto-repair concatenated tool names (some models merge names).
            self._init_tools()
            repaired_tool_calls: list[ToolCall] = []
            for tc in tool_calls:
                if self._tool_registry and self._tool_registry.get(tc.name) is None:
                    repaired = self._repair_tool_name(tc.name)
                    if len(repaired) > 1 and all(self._tool_registry.get(n) for n in repaired):
                        logger.info(
                            "Non-stream: repaired concatenated tool name: %s → %s",
                            tc.name,
                            repaired,
                        )
                        for i, name in enumerate(repaired):
                            call_args = tc.arguments if i == 0 else {}
                            repaired_tool_calls.append(
                                ToolCall(
                                    id=f"{tc.id}_{i}" if tc.id else f"repair_{i}",
                                    name=name,
                                    arguments=call_args,
                                ),
                            )
                        continue
                repaired_tool_calls.append(tc)
            tool_calls = repaired_tool_calls

            # There are tool calls — execute each one.
            # First, add the assistant message (with tool_calls) to history.
            self.history.append(Message.assistant(content, tool_calls=tool_calls))

            # Execute tools in parallel via asyncio.gather. Independent
            # reads (multiple file_read, directory_list, web_search) are
            # 3-4x faster in parallel than serially, and most coding tasks
            # are read-heavy. The OpenAI protocol preserves tool_call_id
            # ordering on the wire, so the LLM still sees a coherent flow.
            import asyncio as _asyncio

            results = await _asyncio.gather(
                *(self.execute_tool(tc) for tc in tool_calls),
                return_exceptions=True,
            )

            for tc, result in zip(tool_calls, results):
                if isinstance(result, Exception):
                    # Convert exception to a ToolResult so the LLM gets
                    # structured feedback instead of a hard loop break.
                    result = ToolResult(
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=f"Error ejecutando {tc.name}: {result}",
                    )

                # Notify frontend via a callback (set by REPL).
                if self._on_tool_call is not None:
                    self._on_tool_call(tc.name, tc.arguments, result.content)

                # Add tool result to history.
                self.history.append(sanitize_unicode(result.to_openai_message()))

            # Rebuild messages for the next iteration.
            messages = self._build_messages()
            _iter_idx += 1

            # Soft-warning on the final iteration: tell the model this
            # is its last allowed tool-calling round so it can wrap up.
            if _iter_idx >= max_iterations - 1:
                messages = [*messages, Message.system(
                    "AVISO: última iteración disponible. "
                    "No llames m\u00e1s herramientas salvo lo imprescindible; "
                    "cerrá reportando qué completaste y qué quedó pendiente."
                )]

            # Check for cancellation between tool iterations. If the
            # caller set the event, stop the loop without appending the
            # partial assistant turn to history.
            if self._cancel_event is not None and self._cancel_event.is_set():
                return ""

        # Loop exhausted while tool calls are still pending. Ask the
        # model for a closing summary (no tools, no further calls)
        # so the user still gets a readable answer instead of a hard
        # truncation at ``max_iterations``.
        try:
            closing = await self.provider.complete(
                [*messages,
                Message.system(
                    "Iteraciones agotadas. Emití un resumen final: "
                    "qué completaste y qué quedó pendiente."
                ),
                ],
                tools=None,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Closing summary failed: %s", exc)
            return content or ""
        closing_content = closing.get("content", "") if isinstance(closing, dict) else ""
        if closing_content:
            self.history.append(Message.assistant(closing_content))
            self._track_usage(
                closing.get("usage", {}) if isinstance(closing, dict) else {},
                (closing.get("model") if isinstance(closing, dict) else None) or self.config.model,
            )
            return closing_content
        return content or ""

    async def process_message_stream(
        self, text: str, *, cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        stream = self._process_message_stream_impl(text, cancel_event=cancel_event)
        if getattr(self, "_progress_enabled", False):
            from .work_session import track_stream
            async with contextlib.aclosing(track_stream(self, stream)) as tracked:
                async for event in tracked:
                    yield event
        else:
            async with contextlib.aclosing(stream):
                async for event in stream:
                    yield event

    async def _process_message_stream_impl(
        self,
        text: str,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a response from the LLM, yielding chunks.

        Each yielded dict has:
          - "type": "text" | "tool_call" | "tool_result" | "done" | "cancelled"
          - additional keys depending on type.

        Parameters
        ----------
        text:
            The user message.
        cancel_event:
            Optional external asyncio.Event. When set, the streaming loop
            checks it between iterations, yields a ``{"type": "cancelled"}``
            event, and returns without appending the partial assistant turn.
            If not provided, the session's internal event is used (set by
            :meth:`cancel`).
        """
        text = sanitize_text(text)
        self._cancel_event = cancel_event or asyncio.Event()
        self.history.append(Message.user(text))
        self._last_user_message = text

        tools = (
            self.get_openai_tools()
            if self._tools_enabled and self.get_tool_descriptions()
            else None
        )

        # We need messages for the streaming loop.
        messages = self._build_messages()

        # Configurable tool-call loop cap (default 10). The final
        # iteration gets a soft-warning system message; if the loop
        # is still exhausting with pending tool calls we fall back to
        # a no-tools closing summary streamed as text before done.
        max_iterations = getattr(self.config, "max_iterations", 10) or 10
        for iteration in range(max_iterations):
            accumulated_text = ""
            accumulated_reasoning = ""
            accumulated_tool_calls: list[dict[str, Any]] = []

            # Check cancellation before starting a new LLM stream.
            if self._cancel_event is not None and self._cancel_event.is_set():
                yield {"type": "cancelled"}
                return

            response_format = {"type": "json_object"} if getattr(self, "_json_mode", False) else None
            async for chunk in self.provider.stream(messages, tools=tools, response_format=response_format):
                if chunk.get("type") == "usage":
                    self._track_usage(chunk.get("usage", {}), self.config.model)
                    yield {"type": "usage", "usage": self._total_usage}
                    continue
                # Reasoning chunks (reasoning_content deltas from Kimi,
                # GLM-family models, etc.) are a separate event type: forward
                # them as-is so the UI renders a thinking panel instead of
                # gluing the reasoning onto the final message.
                if chunk.get("type") == "reasoning":
                    reasoning_chunk = chunk.get("content", "")
                    if reasoning_chunk:
                        accumulated_reasoning += reasoning_chunk
                        yield {"type": "reasoning", "content": reasoning_chunk}
                    continue

                content = chunk.get("content", "")
                finish_reason = chunk.get("finish_reason")
                tc_deltas = chunk.get("tool_calls")

                if content:
                    accumulated_text += content
                    yield {"type": "text", "content": content}

                # Collect tool calls. The provider layer already accumulates
                # the SSE deltas per index and emits each call fully formed
                # ({id, name, arguments}); merging them here by a nonexistent
                # "index" key used to collapse parallel calls into one slot,
                # concatenating names and leaving every call but the first
                # without arguments.
                if tc_deltas:
                    for tc_data in tc_deltas:
                        accumulated_tool_calls.append(
                            {
                                "id": tc_data.get("id", ""),
                                "name": tc_data.get("name", ""),
                                "arguments": tc_data.get("arguments", ""),
                            }
                        )

                if finish_reason == "stop":
                    continue  # Drain the provider stream so its HTTP context closes.

            # No tool calls — we're done.
            if not accumulated_tool_calls:
                self.history.append(Message.assistant(accumulated_text))
                if accumulated_reasoning:
                    self.history[-1]["reasoning_content"] = accumulated_reasoning
                yield {"type": "done", "content": accumulated_text, "usage": self._total_usage}
                return

            # Resolve tool calls — with auto-repair for concatenated names.
            resolved_tool_calls: list[ToolCall] = []
            invalid_tool_results: list[ToolResult] = []
            for tc_data in accumulated_tool_calls:
                raw_args = tc_data["arguments"]
                if isinstance(raw_args, dict):
                    # Already parsed by the provider layer.
                    args = raw_args
                else:
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except json.JSONDecodeError:
                        finish_hint = " El turno termin\u00f3 por finish_reason='length'." if finish_reason == "length" else ""
                        invalid_tool_results.append(ToolResult(
                            tool_call_id=tc_data["id"],
                            name=tc_data["name"],
                            content=(
                                f"Los argumentos de {tc_data['name']} no fueron JSON v\u00e1lido "
                                f"(probable truncamiento por l\u00edmite de tokens de salida).{finish_hint} "
                                "Divide el contenido en partes m\u00e1s peque\u00f1as o usa varias llamadas consecutivas."
                            ),
                        ))
                        continue

                tc_name = tc_data["name"]
                tc_id = tc_data["id"]

                # Auto-repair: some models (e.g. GLM-5.1) concatenate
                # multiple tool names into one call.  Split them up.
                self._init_tools()
                if self._tool_registry and self._tool_registry.get(tc_name) is None:
                    repaired = self._repair_tool_name(tc_name)
                    if len(repaired) > 1 and all(self._tool_registry.get(n) for n in repaired):
                        logger.info(
                            "Stream: repaired concatenated tool name: %s → %s",
                            tc_name,
                            repaired,
                        )
                        # Create a separate ToolCall for each split name.
                        # Arguments go to the first tool; the rest get {}.
                        for i, name in enumerate(repaired):
                            call_args = args if i == 0 else {}
                            resolved_tool_calls.append(
                                ToolCall(
                                    id=f"{tc_id}_{i}" if tc_id else f"repair_{i}",
                                    name=name,
                                    arguments=call_args,
                                ),
                            )
                        continue

                tc = ToolCall(id=tc_id, name=tc_name, arguments=args)
                resolved_tool_calls.append(tc)

            # Check cancellation before committing the assistant tool-call turn.
            if self._cancel_event is not None and self._cancel_event.is_set():
                yield {"type": "cancelled"}
                return

            serial_tools = getattr(self, "_serial_tools", False)
            self.history.append(Message.assistant(
                accumulated_text, tool_calls=None if serial_tools else resolved_tool_calls,
            ))
            if accumulated_reasoning:
                self.history[-1]["reasoning_content"] = accumulated_reasoning

            for invalid in invalid_tool_results:
                # No executable call was declared for invalid JSON. Keep the
                # validation feedback as an ordinary message so protocol repair
                # cannot discard it as an orphan tool result on the next round.
                self.history.append(Message.user("Error de validación de herramienta: " + invalid.content))
                yield {"type": "tool_result", "id": invalid.tool_call_id,
                       "name": invalid.name, "content": invalid.content, "is_error": True}

            if not resolved_tool_calls:
                messages = self._build_messages()
                continue

            # Execute and yield tool results. Independent tools run in
            # parallel via asyncio.gather — yields remain in original order
            # so the REPL still renders them in the model's intended flow.
            import asyncio as _asyncio

            if serial_tools:
                for tc in resolved_tool_calls:
                    if self._cancel_event is not None and self._cancel_event.is_set():
                        yield {"type": "cancelled"}
                        return
                    # Declare only the call about to start. A checkpoint/pause
                    # after its result must retain a complete protocol group,
                    # without marking later, unstarted calls as unknown effects.
                    self.history.append(Message.assistant("", tool_calls=[tc]))
                    yield {"type": "tool_call", "id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    try:
                        result = await self.execute_tool(tc)
                    except Exception as exc:
                        result = ToolResult(tc.id, tc.name, f"Error ejecutando {tc.name}: {exc}")
                    self.history.append(sanitize_unicode(result.to_openai_message()))
                    yield {"type": "tool_result", "id": tc.id, "name": tc.name, "content": result.content,
                           "is_error": result.content.startswith(("Error:", "Error ejecutando"))}
                gathered = []
            else:
                for tc in resolved_tool_calls:
                    yield {"type": "tool_call", "id": tc.id, "name": tc.name, "arguments": tc.arguments}
                gathered = await _asyncio.gather(
                    *(self.execute_tool(tc) for tc in resolved_tool_calls),
                    return_exceptions=True,
                )

            for tc, result in zip(resolved_tool_calls, gathered):
                if isinstance(result, Exception):
                    result = ToolResult(
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=f"Error ejecutando {tc.name}: {result}",
                    )
                self.history.append(sanitize_unicode(result.to_openai_message()))
                yield {"type": "tool_result", "id": tc.id, "name": tc.name, "content": result.content,
                       "is_error": result.content.startswith(("Error:", "Error ejecutando"))}

            # Rebuild messages for next iteration.
            messages = self._build_messages()

            # Soft-warning on the final iteration: nudge the model to
            # wrap up so we can transition to the closing summary.
            if iteration >= max_iterations - 1:
                messages = [*messages, Message.system(
                    "AVISO: última iteración disponible. "
                    "No llames m\u00e1s herramientas salvo lo imprescindible; "
                    "cerrá reportando qué completaste y qué quedó pendiente."
                )]

            # Check for cancellation between tool iterations.
            if self._cancel_event is not None and self._cancel_event.is_set():
                yield {"type": "cancelled"}
                return

        # Loop exhausted while tool calls are still pending. Stream a
        # closing summary as text (no tools), then done. If the
        # provider.complete call itself fails, we still emit a final
        # done so the REPL/IDE doesn't hang.
        try:
            closing = await self.provider.complete(
                [*messages,
                Message.system(
                    "Iteraciones agotadas. Emití un resumen final: "
                    "qué completaste y qué quedó pendiente."
                ),
                ],
                tools=None,
            )
            closing_content = (
                closing.get("content", "") if isinstance(closing, dict) else ""
            )
            if closing_content:
                self.history.append(Message.assistant(closing_content))
                self._track_usage(
                    closing.get("usage", {}) if isinstance(closing, dict) else {},
                    (closing.get("model") if isinstance(closing, dict) else None) or self.config.model,
                )
                accumulated_text = (accumulated_text + closing_content) if accumulated_text else closing_content
                yield {"type": "text", "content": closing_content}
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Stream closing summary failed: %s", exc)

        yield {"type": "done", "content": accumulated_text, "usage": self._total_usage}

    # ── Callback hook for REPL ──────────────────────────────────────

    _on_tool_call_callbacks: list = []

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)

    @property
    def _on_tool_call(self) -> object:
        """Callback for tool call notifications (set by REPL)."""
        return getattr(self, "__on_tool_call", None)

    @_on_tool_call.setter
    def _on_tool_call(self, fn: object) -> None:
        self.__on_tool_call = fn

    # ── Convenience ──────────────────────────────────────────────────

    def get_plan_progress_str(self) -> str:
        """Return a one-line summary of the active plan's progress."""
        plan = getattr(self, "current_plan", None)
        if plan is None or not plan.steps:
            return ""

        done = sum(1 for step in plan.steps if step.done)
        total = len(plan.steps)
        next_step = plan.next_pending()
        parts: list[str] = []
        for step in plan.steps:
            if step.done:
                parts.append(f"\u2713 {step.description}")
            elif step is next_step:
                parts.append(f"\u25b6 {step.description}")
            else:
                parts.append(f"\u00b7 {step.description}")
        separator = " ? "
        return f"[Plan: {done}/{total}] {separator.join(parts)}"

    @property
    def total_usage(self) -> dict[str, int]:
        return dict(self._total_usage)

    @property
    def per_model_usage(self) -> dict[str, dict[str, Any]]:
        return {model: dict(stats) for model, stats in self._per_model_usage.items()}

    @property
    def tool_call_counts(self) -> dict[str, int]:
        """Return per-tool call counts from the conversation history."""
        counts: dict[str, int] = {}
        for msg in self.history:
            for tc in msg.get("tool_calls", []):
                name = tc.get("function", {}).get("name") or tc.get("name")
                if name:
                    counts[name] = counts.get(name, 0) + 1
        return counts

    @property
    def message_counts(self) -> dict[str, int]:
        """Return counts of messages by role (excluding system)."""
        counts: dict[str, int] = {}
        for msg in self.history:
            role = msg.get("role", "unknown")
            if role == "system":
                continue
            counts[role] = counts.get(role, 0) + 1
        return counts

    def _ensure_per_model_entry(self, model: str) -> None:
        if model not in self._per_model_usage:
            self._per_model_usage[model] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost": 0.0,
            }

    def _track_usage(self, usage: dict[str, Any], model: str) -> None:
        from .providers import estimate_cost

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

        self._total_usage["prompt_tokens"] += prompt_tokens
        self._total_usage["completion_tokens"] += completion_tokens
        self._total_usage["total_tokens"] += total_tokens

        self._ensure_per_model_entry(model)
        self._per_model_usage[model]["prompt_tokens"] += prompt_tokens
        self._per_model_usage[model]["completion_tokens"] += completion_tokens
        self._per_model_usage[model]["total_tokens"] += total_tokens
        actual_cost = usage.get("cost_usd")
        if isinstance(actual_cost, (int, float)) and not isinstance(actual_cost, bool):
            call_cost = float(actual_cost)
        else:
            call_cost = estimate_cost(model, prompt_tokens, completion_tokens)
        self._per_model_usage[model]["cost"] += call_cost

    @property
    def last_user_message(self) -> str:
        """Return the last user message text (for /redo support)."""
        return self._last_user_message

    @classmethod
    def from_config_path(cls, config_path: str | None = None) -> AgentSession:
        """Create an :class:`AgentSession` from a config file path."""
        config = load_config(config_path)
        return cls(config)

    def _matches_auto_pattern(self, tool_name: str, tool_args: dict[str, Any]) -> bool:
        """Return True if *tool_args* match any pre-approved auto-execute pattern.

        Patterns are stored as regex strings. The tool name plus the JSON
        serialisation of the arguments is matched against each pattern. If any
        pattern matches, the tool call is pre-approved and the diff preview is
        skipped.
        """
        import re

        payload = json.dumps({"tool": tool_name, "args": tool_args}, ensure_ascii=False, default=str)
        for pattern in self._auto_approved_patterns:
            try:
                if re.search(pattern, payload):
                    return True
            except re.error:
                logger.warning("Patrón de auto-approve inv\u00e1lido: %s", pattern)
        return False

    def _format_duration(self, seconds: float) -> str:
        """Return a human-readable duration string."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        mins, secs = divmod(int(seconds), 60)
        if mins < 60:
            return f"{mins}m {secs}s"
        hours, mins = divmod(mins, 60)
        return f"{hours}h {mins}m {secs}s"


    # ── Hooks (v4.3.1-extended) ──────────────────────────────────

    def _run_hook(self, event: str, env_extra: dict[str, Any]) -> int:
        """Run a lifecycle hook for ``event`` (e.g. 'pre-tool-call')."""
        try:
            from .hooks import run_hook
            return run_hook(event, env_extra or {})
        except Exception as exc:
            logger.warning("Hook '%s' falló: %s", event, exc)
            return 1


# ── Module-level helper: compact_history ──────────────────────────────────


async def compact_history(
    session: AgentSession,
    ratio: float | None = None,
) -> int:
    """Summarize the middle of ``session.history`` to save tokens.

    Strategy: keep the system prompt (if any), keep the first 2 messages,
    and keep the last N messages (proportional to ratio). The middle is
    replaced by a single "..." summary placeholder.

    Returns the number of removed messages.
    """
    history = session.history
    if len(history) < 6:
        return 0

    # Default: keep ~50% (recent messages)
    keep_ratio = ratio if ratio is not None else 0.5
    keep_count = max(2, int(len(history) * keep_ratio))
    head_count = 2
    tail_count = keep_count - head_count

    if tail_count < 1 or (head_count + tail_count) >= len(history):
        return 0

    head = history[:head_count]
    tail = history[-tail_count:]
    summary_msg = {
        "role": "system",
        "content": f"[... {len(history) - len(head) - len(tail)} mensajes anteriores compactados ...]",
    }
    session.history = head + [summary_msg] + tail
    return len(history) - len(session.history)

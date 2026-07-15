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


logger = logging.getLogger(__name__)

# ── Tool execution limits (v4.3.1) ──────────────────────────────────
# Protect the conversation from a single tool call flooding the context.
# Tools that produce large output (file_read on a big file, web_search,
# directory_list on a huge tree) are truncated after these limits.
_MAX_TOOL_RESULT_CHARS = 50_000        # hard cap on a single tool result
_TRUNCATION_NOTICE = "\n\n[…resultado truncado para proteger el contexto. Si necesitas ver el resto, usa search_files con un patrón más específico o file_read con offset/limit.]"

# ── Conversation history message ─────────────────────────────────────


class Message(dict):
    """A single conversation message, stored as an OpenAI-compatible dict."""

    @staticmethod
    def user(text: str) -> dict[str, Any]:
        """Create a user-role message dict."""
        return {"role": "user", "content": text}

    @staticmethod
    def assistant(text: str, tool_calls: list[ToolCall] | None = None) -> dict[str, Any]:
        """Create an assistant-role message dict, optionally with tool calls."""
        msg: dict[str, Any] = {"role": "assistant", "content": text}
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
        return tc.to_openai_message()

    @staticmethod
    def system(text: str) -> dict[str, Any]:
        """Create a system-role message dict."""
        return {"role": "system", "content": text}


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
        self._tools_enabled = True
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
        self._session_start: datetime = datetime.now(UTC)

        # Simple execution telemetry for /metrics.
        self._tool_call_history: list[dict[str, Any]] = []
        self._command_history: list[dict[str, Any]] = []
        self._file_edit_history: list[dict[str, Any]] = []

        # Pinned messages (in-memory only, per session).
        self._pinned_messages: list[dict[str, Any]] = []

        # JSON mode: when True, the LLM is asked to emit structured JSON output.
        self._json_mode: bool = False

    # ── Cancellation ────────────────────────────────────────────────

    def cancel(self) -> None:
        """Signal cancellation to any in-flight stream or tool loop.

        Safe to call from a synchronous context (e.g. a Ctrl+C handler).
        """
        if self._cancel_event is not None and not self._cancel_event.is_set():
            self._cancel_event.set()

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
        self._hook_registry = registry
        self._session_id = session_id
        logger.info(
            "HookRegistry attached to AgentSession (session_id=%s)",
            session_id or "<none>",
        )

    def _fire_pre_tool_hook(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Fire ``pre_tool_call`` hooks. Returns (allowed, effective_params).

        ``allowed=False`` means a hook gated the execution. Hooks may also
        rewrite ``params`` by mutating the HookContext data dict.
        """
        if self._hook_registry is None:
            return True, params
        try:
            from lilith_core.hooks import HookContext, HookType

            ctx = HookContext(
                hook_type=HookType.PRE_TOOL_CALL,
                agent_name=getattr(self.config, "model", "lilith-cli"),
                session_id=self._session_id or "",
                data={"tool_name": tool_name, "params": dict(params)},
            )
            result = self._hook_registry.fire(ctx)
        except Exception as exc:  # pragma: no cover — defensive
            self._hook_failures += 1
            logger.warning("pre_tool_call hook failed (non-fatal): %s", exc)
            return True, params

        if result is None:
            return False, params
        # Hooks may rewrite params in-place via the data dict
        effective = result.data.get("params", params)
        if not isinstance(effective, dict):
            effective = params
        return True, effective

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
        if self._hook_registry is None:
            return result
        try:
            from lilith_core.hooks import HookContext, HookType

            ctx = HookContext(
                hook_type=HookType.POST_TOOL_CALL,
                agent_name=getattr(self.config, "model", "lilith-cli"),
                session_id=self._session_id or "",
                data={"tool_name": tool_name, "params": dict(params), "result": result},
            )
            hook_result = self._hook_registry.fire(ctx)
        except Exception as exc:  # pragma: no cover — defensive
            self._hook_failures += 1
            logger.warning("post_tool_call hook failed (non-fatal): %s", exc)
            return result

        if hook_result is None:
            # Suppress: convert to a structured ToolResult error
            try:
                from .providers import ToolResult as _TR
                return _TR(
                    tool_call_id="",
                    name=tool_name,
                    content=f"Error: result for '{tool_name}' suppressed by post_tool_call hook",
                )
            except Exception:
                return result
        return hook_result.data.get("result", result)

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
        """Load local .lilith/CLAUDE.md or global ~/.lilith/CLAUDE.md.

        The result is cached on the session so repeated calls are cheap.
        """
        if self._project_instructions is not None:
            return self._project_instructions

        from pathlib import Path

        local_path = Path.cwd() / ".lilith" / "CLAUDE.md"
        global_path = Path.home() / ".lilith" / "CLAUDE.md"

        try:
            if local_path.exists():
                instructions = local_path.read_text(encoding="utf-8")
            elif global_path.exists():
                instructions = global_path.read_text(encoding="utf-8")
            else:
                instructions = ""
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("No se pudieron cargar instrucciones del proyecto: %s", exc)
            instructions = ""

        self._project_instructions = instructions
        return instructions

    # ── Tools ───────────────────────────────────────────────────────

    def _init_tools(self) -> None:
        """Load tools from *lilith_tools* based on config flags."""
        try:
            # Force registration of all tool classes.
            from lilith_tools import ToolRegistry, filesystem, system  # noqa: F401

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

        for name, description in all_tools.items():
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
        duration = _time.perf_counter() - start

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

        # ── retry settings ────────────────────────────────────────────────
        retry_count = 2
        retry_backoff = 1.0
        tools_config = getattr(self.config, "tools", None)
        if tools_config is not None:
            retry_count = getattr(tools_config, "retry_count", 2) or 2
            retry_backoff = getattr(tools_config, "retry_backoff", 1.0) or 1.0
        if not isinstance(retry_count, int) or retry_count < 0:
            retry_count = 2
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
            except asyncio.TimeoutError:
                content = f"Error: tool '{tool_name}' excedió el timeout de {tool_timeout}s"
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
        messages: list[dict[str, Any]] = [Message.system(self.system_prompt)]

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

        # Inject project-local instructions from .lilith/CLAUDE.md.
        project_instructions = self._load_project_instructions()
        if project_instructions:
            messages[0]["content"] += (
                f"\n\nPROJECT INSTRUCTIONS:\n{project_instructions}"
            )

        # Trim history to max_turns.
        max_turns = self.config.history.max_turns
        history = self.history[-max_turns * 2 :]  # user+assistant = 2 messages per turn

        messages.extend(history)
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
        self._cancel_event = cancel_event or asyncio.Event()
        self.history.append(Message.user(text))
        self._last_user_message = text

        messages = self._build_messages()
        tools = (
            self.get_openai_tools()
            if self._tools_enabled and self.get_tool_descriptions()
            else None
        )

        # Tool-calling loop.
        max_iterations = 10  # safety limit
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
                # No more tool calls — we're done.
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
                self.history.append(result.to_openai_message())

            # Rebuild messages for the next iteration.
            messages = self._build_messages()

            # Check for cancellation between tool iterations. If the
            # caller set the event, stop the loop without appending the
            # partial assistant turn to history.
            if self._cancel_event is not None and self._cancel_event.is_set():
                return ""

        return content  # fallback

    async def process_message_stream(
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

        max_iterations = 10
        for iteration in range(max_iterations):
            accumulated_text = ""
            accumulated_tool_calls: list[dict[str, Any]] = []

            # Check cancellation before starting a new LLM stream.
            if self._cancel_event is not None and self._cancel_event.is_set():
                yield {"type": "cancelled"}
                return

            response_format = {"type": "json_object"} if getattr(self, "_json_mode", False) else None
            async for chunk in self.provider.stream(messages, tools=tools, response_format=response_format):
                # Reasoning chunks (reasoning_content deltas from Kimi,
                # GLM-5.1, DeepSeek, …) are a separate event type: forward
                # them as-is so the UI renders a thinking panel instead of
                # gluing the reasoning onto the final message.
                if chunk.get("type") == "reasoning":
                    reasoning_chunk = chunk.get("content", "")
                    if reasoning_chunk:
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
                    break

            # No tool calls — we're done.
            if not accumulated_tool_calls:
                self.history.append(Message.assistant(accumulated_text))
                yield {"type": "done", "content": accumulated_text, "usage": self._total_usage}
                return

            # Resolve tool calls — with auto-repair for concatenated names.
            resolved_tool_calls: list[ToolCall] = []
            for tc_data in accumulated_tool_calls:
                raw_args = tc_data["arguments"]
                if isinstance(raw_args, dict):
                    # Already parsed by the provider layer.
                    args = raw_args
                else:
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except json.JSONDecodeError:
                        args = {"raw": raw_args}

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

            self.history.append(Message.assistant(accumulated_text, tool_calls=resolved_tool_calls))

            # Execute and yield tool results. Independent tools run in
            # parallel via asyncio.gather — yields remain in original order
            # so the REPL still renders them in the model's intended flow.
            import asyncio as _asyncio

            # Yield the tool_call notifications first (deterministic order).
            for tc in resolved_tool_calls:
                yield {"type": "tool_call", "name": tc.name, "arguments": tc.arguments}

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
                yield {"type": "tool_result", "name": tc.name, "content": result.content}
                self.history.append(result.to_openai_message())

            # Rebuild messages for next iteration.
            messages = self._build_messages()

            # Check for cancellation between tool iterations.
            if self._cancel_event is not None and self._cancel_event.is_set():
                yield {"type": "cancelled"}
                return

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
        """Return a one-line summary of the active plan's progress.

        Example::

            [Plan: 2/5] Read file - Edit config - Run tests - Format - Commit

        Completed steps are prefixed with a checkmark, and the current
        pending step is shown in bold. When no plan exists, returns an
        empty string.
        """
        plan = getattr(self, "current_plan", None)
        if plan is None or not plan.steps:
            return ""

        done = sum(1 for s in plan.steps if s.done)
        total = len(plan.steps)
        next_step = plan.next_pending()
        parts: list[str] = []
        for step in plan.steps:
            if step.done:
                parts.append(f"✓ {step.description}")
            elif step is next_step:
                parts.append(f"▶ {step.description}")
            else:
                parts.append(f"· {step.description}")
        return f"[Plan: {done}/{total}] {' — '.join(parts)}"

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
        self._per_model_usage[model]["cost"] += estimate_cost(
            model, prompt_tokens, completion_tokens
        )

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
                logger.warning("Patrón de auto-approve inválido: %s", pattern)
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

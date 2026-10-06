"""Interactive REPL for Yggdrasil CLI v6.0.

Built on ``prompt_toolkit`` with Rich rendering, Norse-themed prompts,
streaming output with thinking panels, slash-command auto-completion,
conversation history, and auto-save on exit.

Inspired by Hermes Agent's REPL architecture (queue-based input,
line-buffered streaming, Rich panels for tool calls, OSC 52 clipboard).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.lexers import PygmentsLexer
from prompt_toolkit.styles import Style as PtStyle
from pygments.lexers import MarkdownLexer as PygmentsMarkdownLexer
from rich.live import Live

if TYPE_CHECKING:
    from .session_runtime import SessionRuntime
from .commands import CommandRegistry
from .config import CONFIG_DIR
from .render import (
    Timer,
    build_stream_tail,
    build_thinking_panel,
    console,
    get_theme,
    list_themes,
    make_thinking_spinner,
    render_assistant_separator,
    render_error,
    render_markdown,
    render_thinking,
    render_tool_call,
    render_tool_result,
    render_turn_end,
    render_user_separator,
    render_welcome,
    set_theme,
)
from .tool_progress import (
    DelegationLive,
    DelegationStreamBuffer,
    ToolProgressTracker,
    set_tool_panels,
)
from .slash_router import dispatch as dispatch_slash
from .slash_router import slash_commands
from .trace import AgentTrace

# ── Prompt constants ────────────────────────────────────────────────

_HISTORY_FILE = CONFIG_DIR / "history"
_CONVERSATIONS_DIR = Path(os.environ.get("LILITH_CONVERSATIONS_DIR", str(CONFIG_DIR / "conversations"))).expanduser()

def _prompt_continuation(_width: int, _row: int, _column: int) -> list[tuple[str, str]]:
    """Return the continuation prompt for multi-line input.

    Shows theme-aligned dots aligned with the main prompt.
    Returns prompt_toolkit formatted text tuples (style, text).
    """

    return [("class:prompt.dots", f"{get_theme().prompt_prefix} ... ")]


# ── Clipboard helpers ───────────────────────────────────────────────


def _copy_to_clipboard(text: str) -> bool:
    """Try copying *text* to the system clipboard. Returns True on success."""
    # 1) Try OSC 52 (works over SSH / tmux)
    try:
        import base64

        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        sys.stdout.write(f"\033]52;c;{encoded}\007")
        sys.stdout.flush()
        return True
    except Exception:
        pass

    # 2) Try WSL → Windows clipboard
    if _is_wsl():
        try:
            import subprocess

            subprocess.run(
                ["clip.exe"],
                input=text.encode("utf-8"),
                check=True,
                capture_output=True,
            )
            return True
        except Exception:
            pass

    # 3) Try xclip / xsel
    for cmd in ["xclip", "xsel", "pbcopy"]:
        try:
            import subprocess

            subprocess.run(
                [cmd],
                input=text.encode("utf-8"),
                check=True,
                capture_output=True,
            )
            return True
        except Exception:
            continue

    return False


def _is_wsl() -> bool:
    """Check if running under Windows Subsystem for Linux."""
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False


# ── Multi-line detection ────────────────────────────────────────────


def _is_multi_line_start(text: str) -> bool:
    """Return True if *text* starts an incomplete multi-line block
    (e.g., triple-quote, unclosed bracket).
    """
    stripped = text.rstrip()
    if stripped.endswith(":") and not stripped.startswith("/"):
        return True
    # Unmatched braces / brackets / parens.
    opens = "({["
    closes = ")}]"
    stack: list[str] = []
    for ch in text:
        if ch in opens:
            stack.append(ch)
        elif ch in closes:
            idx = closes.index(ch)
            if stack and stack[-1] == opens[idx]:
                stack.pop()
    return len(stack) > 0


# ── Conversation persistence ────────────────────────────────────────


def _auto_save_conversation(session: SessionRuntime) -> Path | None:
    """Save the conversation history as JSON. Returns the filepath or None."""
    if not session.history or not session.config.history.save:
        return None

    _CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    session_name = getattr(session, "_conversation_name", None)
    if not isinstance(session_name, str) or Path(session_name).name != session_name:
        session_name = f"conv_{timestamp}_{uuid.uuid4().hex[:8]}.json"
    filepath = _CONVERSATIONS_DIR / session_name

    from .delegation_keys import encode_keys, validate_namespace
    from .extra_commands import _goal_from_session
    from .robust_kit import PROFILES
    try:
        delegation_requests = encode_keys(getattr(session, "_delegation_request_ids", {}))
        delegation_namespace = validate_namespace(getattr(session, "_delegation_namespace", None))
        execution_profile = getattr(session, "_execution_profile", None)
        if execution_profile is not None and (not isinstance(execution_profile, str) or execution_profile not in PROFILES):
            raise ValueError("Invalid execution profile")
    except (ValueError, TypeError, AttributeError):
        render_error("No se guardó la sesión: identidades de delegación inválidas.")
        return None

    data = {
        "delegation_requests": delegation_requests,
        "delegation_namespace": delegation_namespace,
        "execution_profile": execution_profile,
        "goal": _goal_from_session(session),
        "progress": getattr(session, "_run_progress", None),
        "tool_receipts": getattr(session, "_tool_receipts", {}),
        "task_request": getattr(session, "_task_request_file", None),
        "project_root": getattr(session, "_project_root", str(Path.cwd().resolve())),
        "timestamp": timestamp,
        "model": session.config.model,
        "provider": session.config.provider,
        "messages": session.history,
        "usage": session.total_usage,
        "per_model_usage": session._per_model_usage,
    }
    try:
        from .unicode_safety import sanitize_unicode

        data = sanitize_unicode(data)
        staging = filepath.with_name(f".{filepath.name}.{uuid.uuid4().hex}.tmp")
        try:
            with staging.open("x", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False, default=str)
                handle.flush()
                os.fsync(handle.fileno())
            staging.replace(filepath)
        finally:
            staging.unlink(missing_ok=True)
        session._conversation_name = session_name
        return filepath
    except Exception as exc:
        render_error(f"Error guardando conversación: {exc}")
        return None


def _list_saved_conversations() -> list[dict[str, Any]]:
    """Return a sorted list of saved conversation metadata (newest first)."""
    if not _CONVERSATIONS_DIR.exists():
        return []

    conversations: list[dict[str, Any]] = []
    for fpath in sorted(_CONVERSATIONS_DIR.glob("conv_*.json"), reverse=True):
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            messages = data.get("messages", [])
            # Build a short preview from the first user message.
            preview = ""
            for msg in messages:
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    preview = content[:80] + ("…" if len(content) > 80 else "")
                    break
            conversations.append(
                {
                    "file": fpath,
                    "project_root": data.get("project_root"),
                    "goal": data.get("goal"),
                    "progress": data.get("progress"),
                    "task_request": data.get("task_request"),
                    "name": fpath.stem,
                    "timestamp": data.get("timestamp", ""),
                    "model": data.get("model", "unknown"),
                    "provider": data.get("provider", "unknown"),
                    "message_count": len(messages),
                    "usage": data.get("usage", {}),
                    "preview": preview,
                },
            )
        except Exception as exc:
            # Don't swallow corruption silently — surface the file that
            # failed to read so the user can rename/delete it via /profile.
            console.print(f"[warning]No pude leer {fpath.name}: {exc}[/]")
            continue

    return sorted(conversations, key=lambda item: str(item["timestamp"]), reverse=True)


def _load_conversation(filepath: Path) -> dict[str, Any] | None:
    """Load a conversation JSON file. Returns the full data dict or None."""
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except FileNotFoundError:
        render_error(f"Archivo no encontrado: {filepath.name}")
        return None
    except json.JSONDecodeError as exc:
        render_error(
            f"{filepath.name} no es JSON válido (línea {exc.lineno}, col {exc.colno}): "
            f"{exc.msg}. Usá /resume para ver las conversaciones disponibles."
        )
        return None
    except Exception as exc:
        render_error(
            f"Error cargando {filepath.name}: {exc}. "
            f"Usá /resume para ver las conversaciones disponibles."
        )
        return None


# ── Main REPL ───────────────────────────────────────────────────────


async def run_repl(session: SessionRuntime) -> None:
    """Launch the interactive REPL loop."""
    # Ensure directories exist.
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

    # ── Load saved theme from config ───────────────────────────────
    try:
        import yaml as _yaml

        from .config import CONFIG_FILE

        _raw = _yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
        _saved_theme = _raw.get("theme", "norse")
        if getattr(session, "_console_style", None) != "agent" and _saved_theme in [t.name for t in list_themes()]:
            set_theme(_saved_theme)
    except Exception:
        pass  # Fall back to default theme.

    # ── Render welcome ────────────────────────────────────────────
    if getattr(session, "_console_style", None) == "agent":
        from .agent_console import render_header
        render_header(session, console)
    else:
        render_welcome(
            model=session.config.model,
            provider=session.config.provider,
            tools_count=len(session.get_tool_descriptions()),
            has_memory=session.memory is not None,
        )
    console.print()

    # Resume hint for persistent orchestration work from previous sessions.
    try:
        from lilith_tools.orchestration_state import OrchestrationStateStore

        _orch_state = OrchestrationStateStore().get()
        _active_plan = _orch_state.get("plan")
        _pending = [
            task
            for task in _orch_state.get("tasks", [])
            if task.get("status") != "completada"
        ]
        if _active_plan and _pending:
            console.print(
                f"[info]Plan activo: {_active_plan.get('name', '(sin nombre)')} — "
                f"{len(_pending)} tareas pendientes. Usa /state[/]"
            )
    except Exception:
        pass

    # ── MCP server bootstrap (lazy, non-blocking) ────────────────
    # Each enabled server in ``config.mcp_servers`` is spawned, its
    # tools are mounted into the global ``ToolRegistry`` and the
    # manager is attached to the session so ``/mcp`` can introspect
    # it. A broken subprocess never aborts the REPL — the failure is
    # printed as a one-line notice and ``/mcp list`` will show the
    # detailed error. The manager is shut down in the ``finally``
    # block below.
    try:
        from lilith_tools.mcp_client import MCPClientManager

        mcp_servers_cfg = getattr(session.config, "effective_mcp_servers", {})
        if mcp_servers_cfg:
            manager = MCPClientManager(
                {
                    name: cfg.model_dump()
                    for name, cfg in mcp_servers_cfg.items()
                }
            )
            statuses = manager.start_all()
            session._mcp_manager = manager  # type: ignore[attr-defined]
            for server_name, status in statuses.items():
                if status == "ok":
                    count = len(manager.mounted_tools.get(server_name, []))
                    console.print(
                        f"[info]✓ MCP '{server_name}'[/] "
                        f"montado ({count} tool)"
                    )
                elif status == "disabled":
                    console.print(f"[dim]MCP '{server_name}' deshabilitado[/]")
                else:
                    console.print(
                        f"[warning]⚠ MCP '{server_name}' falló: {status}[/]"
                    )
            # Invalidate the agent's tool cache so the LLM sees the
            # newly-mounted tools without a session restart.
            try:
                if hasattr(session, "_tools_cache"):
                    session._tools_cache = None  # type: ignore[attr-defined]
            except Exception:
                pass
        else:
            session._mcp_manager = None  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover — defensive
        # Never crash the REPL over MCP bootstrap problems.
        console.print(f"[warning]⚠ MCP bootstrap: {exc}[/]")
        session._mcp_manager = None  # type: ignore[attr-defined]

    # ── Command registry
    registry = CommandRegistry(session)
    registry.discover()

    # ── prompt_toolkit setup ─────────────────────────────────────
    history = FileHistory(str(_HISTORY_FILE))
    from .command_surface import completion_words

    completer = WordCompleter(completion_words(slash_commands()), ignore_case=True, sentence=True)

    # Build prompt_toolkit style from the active theme — dynamically
    # resolved so that /theme switches update the prompt immediately.
    from prompt_toolkit.styles import DynamicStyle

    def _build_pt_style() -> PtStyle:
        """Build a PtStyle dict from the current theme (called dynamically)."""
        t = get_theme()
        return PtStyle.from_dict(t.pt_style)

    pt_style = DynamicStyle(_build_pt_style)

    # Markdown lexer for syntax highlighting in the input area.
    md_lexer = PygmentsLexer(PygmentsMarkdownLexer)

    # ── Prompt mode state ────────────────────────────────────────
    _multiline_mode = {"active": False}
    _live_tokens = {"prompt": 0, "completion": 0, "total": 0, "turns": 0}

    def _bottom_toolbar() -> list[tuple[str, str]]:
        """Dynamic bottom toolbar showing input mode, turns, and token usage."""
        t = get_theme()
        mode = (
            f"{t.prompt_prefix} MULTILINE"
            if _multiline_mode["active"]
            else f"{t.prompt_prefix} SINGLE"
        )
        parts = [
            ("class:prompt", mode),
            ("", "  "),
            ("class:auto-suggestion", "Alt+Enter: nueva línea  Ctrl+O: toggle multiline"),
        ]
        # Show token bar if we have usage data.
        s = _live_tokens
        if s["total"] > 0:
            parts.append(("", "  "))
            parts.append(
                ("class:usage", f"Tokens: {s['prompt']}↑ {s['completion']}↓ {s['total']}Σ"),
            )
            parts.append(("", " "))
            parts.append(("class:usage", f"Turn: {s['turns']}"))

        # Show plan progress when there's an active plan.
        plan_progress = session.get_plan_progress_str()
        if plan_progress:
            parts.append(("", "  "))
            parts.append(("class:info", plan_progress))

        # Show agent mode in the bottom toolbar.
        agent_mode = getattr(session, "agent_mode", "default")
        if agent_mode and agent_mode != "default":
            parts.append(("", "  "))
            parts.append(("class:warning", f"Modo: {agent_mode}"))
        return parts

    prompt_session: PromptSession = PromptSession(
        history=history,
        auto_suggest=AutoSuggestFromHistory(),
        completer=completer,
        lexer=md_lexer,
        style=pt_style,
        multiline=False,
        prompt_continuation=_prompt_continuation,
        bottom_toolbar=None if getattr(session, "_console_style", None) == "agent" else _bottom_toolbar,
    )

    # Key bindings.
    kb = KeyBindings()

    @kb.add("c-c")
    def _cancel_current(event: KeyPressEvent) -> None:
        """Ctrl+C cancels the current generation (not the REPL)."""
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    @kb.add("escape", "enter")
    def _insert_newline(event: KeyPressEvent) -> None:
        """Alt+Enter inserts a newline for multi-line input.

        On Windows Terminal, Shift+Enter also sends Escape+Enter,
        so this doubles as Shift+Enter support.
        """
        event.current_buffer.insert_text("\n")

    @kb.add("c-o")
    def _toggle_multiline(event: KeyPressEvent) -> None:
        """Ctrl+O toggles multiline mode for the current input."""
        _multiline_mode["active"] = not _multiline_mode["active"]
        buf = event.current_buffer
        buf.is_multiline = _multiline_mode["active"]
        # Toolbar updates automatically via _bottom_toolbar().
        event.app.invalidate()

    # ── Tool call callback ────────────────────────────────────────
    def on_tool_call(name: str, args: dict, result: str) -> None:
        """Render a tool call in the REPL UI."""
        from .render import (
            _extract_data,
            render_diff,
            render_tool_line,
            summarize_tool_result,
        )
        
        is_err = result.startswith("Error:") or result.startswith("Error ejecutando")
        data = _extract_data(result)
        if isinstance(data, dict):
            error = data.get("error") or data.get("is_error")
            if error:
                is_err = True

        activity = None
        if getattr(session, "_console_style", None) == "agent":
            from .mission.presentation import mission_activity_line

            activity = mission_activity_line(
                name, args, data, error=is_err, raw=result
            )
        if activity is not None:
            console.print(activity)
        elif is_err:
            render_tool_call(name, args, result=result)
        else:
            has_diff = False
            if name in ("file_write", "file_edit", "batch_edit") and isinstance(data, dict):
                from pathlib import Path
                if name == "batch_edit":
                    edits = data.get("edits", [])
                    if edits:
                        for e in edits:
                            diff_text = e.get("diff", "")
                            if diff_text:
                                has_diff = True
                                path_str = e.get("path", "")
                                path_name = Path(path_str).name if path_str else None
                                console.print(render_diff(diff_text, path_name))
                    else:
                        diff_text = data.get("combined_diff", "")
                        if diff_text:
                            has_diff = True
                            console.print(render_diff(diff_text))
                else:
                    diff_text = data.get("diff", "")
                    if diff_text:
                        has_diff = True
                        path_str = data.get("path", "")
                        path_name = Path(path_str).name if path_str else None
                        console.print(render_diff(diff_text, path_name))

            if has_diff:
                pass
            elif name in ("file_read", "directory_list", "grep_files"):
                rendered = render_tool_result(name, result)
                if rendered is not None:
                    console.print(rendered)
            else:
                summary = summarize_tool_result(name, result, args)
                render_tool_line(name, summary)

    session._on_tool_call = on_tool_call

    # ── Live activity trace (Neurosurfer pattern) ─────────────────
    trace = AgentTrace()

    # ── Turn counter ──────────────────────────────────────────────
    turn_number = 0

    # ── REPL loop ─────────────────────────────────────────────────
    try:
        while True:
            # Build prompt with turn counter and theme prefix.
            turn_number += 1
            model_name = session.config.model
            current_theme = get_theme()
            prompt_formatted = [
                ("class:prompt", f"{current_theme.prompt_prefix} {model_name}"),
                ("", ": "),
            ]
            if getattr(session, "_console_style", None) == "agent":
                from .agent_console import prompt_fragments
                prompt_formatted = prompt_fragments()

            try:
                user_input = await prompt_session.prompt_async(
                    prompt_formatted,
                    key_bindings=kb,
                    multiline=_multiline_mode["active"],
                )
            except KeyboardInterrupt:
                # Ctrl+C during prompt: cancel current generation, stay in REPL.
                console.print("[dim]^C Cancelado.[/]")
                continue
            except EOFError:
                # Ctrl+D: exit.
                console.print("\n[dim]Odin te guíe. Hasta la próxima.[/]")
                break

            text = user_input.strip()
            if not text:
                continue

            # ── Slash command dispatch ────────────────────────────
            if text.startswith("/"):
                try:
                    if await dispatch_slash(session, text, registry):
                        continue
                except SystemExit:
                    break

            # ── Process message via streaming ─────────────────────
            render_user_separator(text)

            # Create a per-turn cancel token so Ctrl+C can cleanly stop the
            # stream and in-flight tool execution without killing the REPL.
            cancel_event = asyncio.Event()

            try:
                await _process_with_streaming(
                    session, text, stats=_live_tokens, trace=trace, cancel_event=cancel_event
                )
            except KeyboardInterrupt:
                cancel_event.set()
                console.print("\n[dim]^C Generación cancelada.[/]")
                continue
            except ConnectionError as exc:
                render_error(f"Error de conexión: {exc}")
                continue
            except Exception as exc:
                render_error(f"Error: {exc}")
                import traceback

                traceback.print_exc()
                continue
            finally:
                _auto_save_conversation(session)

    finally:
        # ── Auto-save on exit ─────────────────────────────────────
        saved_path = _auto_save_conversation(session)
        if saved_path:
            console.print(f"[dim]Conversación guardada: {saved_path.name}[/]")

        # ── Tear down MCP subprocesses ────────────────────────────
        mgr = getattr(session, "_mcp_manager", None)
        if mgr is not None:
            try:
                mgr.shutdown()
            except Exception:
                pass
            try:
                session._mcp_manager = None  # type: ignore[attr-defined]
            except Exception:
                pass


    # ── One-shot mode ───────────────────────────────────────────────────


async def _process_with_streaming(
    session: SessionRuntime,
    text: str,
    stats: dict | None = None,
    trace: AgentTrace | None = None,
    cancel_event: asyncio.Event | None = None,
) -> None:
    """Process a user message with streaming output rendering.

    Handles all event types from process_message_stream:
    - "reasoning": GLM-5.1 thinking content → dim panel
    - "text": normal LLM output → line-buffered with final Markdown
    - "tool_call": tool execution start → card
    - "tool_result": tool result → result card
    - "done": turn complete → usage + duration
    - "cancelled": the caller cancelled the turn via Ctrl+C

    Shows an animated thinking spinner while waiting for the first token.
    """
    accumulated = ""
    reasoning_text = ""
    usage: dict[str, int] = {}
    timer = Timer()
    in_reasoning = False
    first_token_received = False
    _assistant_sep_shown = False

    timer.__enter__()

    # ── Live streaming display (reasoning panel / markdown response) ──
    # Rich allows a single active Live; this one alternates with the
    # tool-progress tracker (paused via tool_progress.pause_live()).
    stream_live: Live | None = None

    def _open_stream_live() -> Live:
        nonlocal stream_live
        if stream_live is None:
            tool_progress.pause_live()
            # transient: the live frame (a bounded tail of the content) is
            # erased on close and the full content printed once. A frame
            # taller than the terminal would duplicate on every refresh.
            stream_live = Live(
                console=console,
                refresh_per_second=12,
                transient=True,
            )
            stream_live.__enter__()
        return stream_live

    def _close_stream_live() -> None:
        nonlocal stream_live
        if stream_live is not None:
            stream_live.__exit__(None, None, None)
            stream_live = None

    # ── Live tool progress tracker for parallel tool execution ────
    # La preferencia manda sobre los paneles. stream_live queda intacto a
    # proposito: ese es el texto de su respuesta, no el proceso.
    set_tool_panels(bool(getattr(session.config, "show_tool_panels", False)))
    tool_progress = ToolProgressTracker()

    # ── Live delegation streaming panel (tanda 6, ITEM 3) ────────────
    # delegate_subagent runs on a worker thread (asyncio.to_thread) so
    # we cannot pipe events into the agent loop directly. Instead we
    # open a dedicated Live panel between the ``tool_call`` and
    # ``tool_result`` events for that name and poll a thread-safe
    # buffer. tool_progress.pause_live() frees the single Rich Live
    # slot while our panel runs, mirroring how streaming alternates
    # with the tool tracker elsewhere in this loop.
    delegation_live: DelegationLive | None = None
    delegation_buffer: DelegationStreamBuffer | None = None

    # ── Start the thinking spinner (shows while LLM processes) ────
    spinner_info = make_thinking_spinner()
    spinner_status = spinner_info["status"]
    spinner_status.__enter__()

    try:
        try:
            with tool_progress:
                async for event in session.process_message_stream(text, cancel_event=cancel_event):
                    event_type = event.get("type", "")

                    # Feed event to live activity trace (Neurosurfer pattern)
                    if trace is not None:
                        trace.handle(event)

                    # ── Provider reasoning stream ──
                    if event_type == "reasoning":
                        chunk = event.get("content", "")
                        if chunk:
                            # Stop spinner before printing anything; otherwise Rich
                            # may leave its active Status frame in scrollback.
                            if not first_token_received:
                                first_token_received = True
                                spinner_status.__exit__(None, None, None)
                            # Show assistant separator before first output.
                            if not _assistant_sep_shown:
                                _assistant_sep_shown = True
                                render_assistant_separator()

                            reasoning_text += chunk
                            in_reasoning = True
                            # Stream the thinking into a live panel (tail
                            # only, so long reasoning doesn't flood the
                            # screen).
                            _open_stream_live().update(
                                build_thinking_panel(reasoning_text, tail_lines=8)
                            )

                    # ── Normal text ──────────────────────────────────────
                    elif event_type == "text":
                        chunk = event.get("content", "")
                        if chunk:
                            # Stop spinner before printing anything; otherwise Rich
                            # may leave its active Status frame in scrollback.
                            if not first_token_received:
                                first_token_received = True
                                spinner_status.__exit__(None, None, None)
                            # Show assistant separator before first output.
                            if not _assistant_sep_shown:
                                _assistant_sep_shown = True
                                render_assistant_separator()

                            # Close reasoning panel if transitioning to content.
                            if in_reasoning:
                                in_reasoning = False
                                _close_stream_live()
                                render_thinking(reasoning_text)
                                reasoning_text = ""
                                console.print()  # blank line before response

                            accumulated += chunk
                            # Stream the response live as Markdown (bounded
                            # tail; the full text prints once at the end).
                            _open_stream_live().update(build_stream_tail(accumulated))

                    # ── Tool call start ───────────────────────────────────
                    elif event_type == "tool_call":
                        if not first_token_received:
                            first_token_received = True
                            spinner_status.__exit__(None, None, None)
                        if not _assistant_sep_shown:
                            _assistant_sep_shown = True
                            render_assistant_separator()

                        # Close reasoning panel before showing tool progress.
                        if in_reasoning:
                            in_reasoning = False
                            _close_stream_live()
                            render_thinking(reasoning_text)
                            reasoning_text = ""
                        # Print any partially streamed text (the transient
                        # live erased its frame) and free the Live slot for
                        # the tool tracker; the next iteration streams into
                        # a fresh display.
                        _close_stream_live()
                        if accumulated.strip():
                            render_markdown(accumulated)
                            console.print()
                        accumulated = ""

                        progress_name = event["name"]
                        if getattr(session, "_console_style", None) == "agent":
                            from .mission.presentation import mission_tool_label

                            progress_name = mission_tool_label(
                                event["name"], event.get("arguments") or {}
                            )
                        tool_progress.start(progress_name)

                        # ITEM 3 (tanda 6): open the delegation streaming
                        # panel when the model invokes delegate_subagent.
                        # The tool itself runs on a worker thread; the
                        # buffer below is what the tool pushes into (when
                        # wired up). For now we capture preset / model /
                        # agentic from the call arguments so the panel
                        # renders meaningful metadata immediately.
                        if (
                            event["name"] == "delegate_subagent"
                            and delegation_live is None
                        ):
                            args = event.get("arguments") or {}
                            buf = DelegationStreamBuffer(
                                preset=str(args.get("preset") or "?"),
                                model=str(args.get("model") or "—"),
                                agentic=bool(args.get("agentic")),
                            )
                            tool_progress.pause_live()
                            delegation_buffer = buf
                            delegation_live = DelegationLive(buf)
                            delegation_live.__enter__()

                    # ── Tool result ──────────────────────────────────────
                    elif event_type == "tool_result":
                        if not first_token_received:
                            first_token_received = True
                            spinner_status.__exit__(None, None, None)

                        # Check for "error" indicator from provider-style events.
                        error = event.get("error") or event.get("is_error")
                        error_str = str(error) if error else None
                        progress_name = event["name"]
                        if getattr(session, "_console_style", None) == "agent":
                            from .mission.presentation import mission_tool_label

                            progress_name = mission_tool_label(
                                event["name"], event.get("arguments") or {}
                            )
                        tool_progress.complete(progress_name, error=error_str)

                        # ITEM 3 (tanda 6): close the delegation streaming
                        # panel that ``tool_call`` opened above. The
                        # transient frame is erased by ``__exit__`` and
                        # the standard ``render_tool_result`` /
                        # ``render_tool_call`` below take over rendering.
                        if (
                            event["name"] == "delegate_subagent"
                            and delegation_live is not None
                        ):
                            try:
                                if error_str and delegation_buffer is not None:
                                    delegation_buffer.finish(error=error_str)
                                elif delegation_buffer is not None:
                                    delegation_buffer.finish()
                                delegation_live.refresh()
                            finally:
                                delegation_live.__exit__(None, None, None)
                                delegation_live = None
                                delegation_buffer = None
                                # tool_progress reopens automatically on
                                # its next ``start()``.

                        from .render import (
                            _extract_data,
                            render_diff,
                            render_tool_line,
                            summarize_tool_result,
                        )
                        
                        name = event["name"]
                        args = event.get("arguments", {})
                        content = event.get("content", "")
                        data = _extract_data(content)
                        is_err = content.startswith("Error:") or content.startswith("Error ejecutando")
                        if error_str:
                            is_err = True

                        activity = None
                        if getattr(session, "_console_style", None) == "agent":
                            from .mission.presentation import mission_activity_line

                            activity = mission_activity_line(
                                name, args, data, error=is_err, raw=content
                            )
                        if activity is not None:
                            console.print(activity)
                        elif is_err:
                            render_tool_call(name, args, result=content)
                        else:
                            has_diff = False
                            if name in ("file_write", "file_edit", "batch_edit") and isinstance(data, dict):
                                from pathlib import Path
                                if name == "batch_edit":
                                    edits = data.get("edits", [])
                                    if edits:
                                        for e in edits:
                                            diff_text = e.get("diff", "")
                                            if diff_text:
                                                has_diff = True
                                                path_str = e.get("path", "")
                                                path_name = Path(path_str).name if path_str else None
                                                console.print(render_diff(diff_text, path_name))
                                    else:
                                        diff_text = data.get("combined_diff", "")
                                        if diff_text:
                                            has_diff = True
                                            console.print(render_diff(diff_text))
                                else:
                                    diff_text = data.get("diff", "")
                                    if diff_text:
                                        has_diff = True
                                        path_str = data.get("path", "")
                                        path_name = Path(path_str).name if path_str else None
                                        console.print(render_diff(diff_text, path_name))

                            if has_diff:
                                pass
                            elif name in ("file_read", "directory_list", "grep_files"):
                                rendered = render_tool_result(name, content)
                                if rendered is not None:
                                    console.print(rendered)
                            else:
                                summary = summarize_tool_result(name, content, args)
                                render_tool_line(name, summary, duration=event.get("duration"))

                    # ── Turn complete ─────────────────────────────────────
                    elif event_type == "done":
                        usage = event.get("usage", {})
                        # ITEM 3 safety net: if a delegation panel is
                        # somehow still open (cancelled, lost event),
                        # close it so Rich isn't left with a live frame.
                        if delegation_live is not None:
                            try:
                                if delegation_buffer is not None:
                                    delegation_buffer.finish()
                                delegation_live.refresh()
                            finally:
                                delegation_live.__exit__(None, None, None)
                                delegation_live = None
                                delegation_buffer = None
                        break

                    # ── Turn cancelled ─────────────────────────────────────
                    elif event_type == "cancelled":
                        if delegation_live is not None:
                            try:
                                if delegation_buffer is not None:
                                    delegation_buffer.finish(error="cancelled")
                                delegation_live.refresh()
                            finally:
                                delegation_live.__exit__(None, None, None)
                                delegation_live = None
                                delegation_buffer = None
                        break

        except StopAsyncIteration:
            pass
        except BaseException:
            # Free the Live slot before propagating (Ctrl+C, provider
            # errors, cancellation) so the terminal isn't left in live
            # mode, and print whatever partial text streamed so it isn't
            # lost with the transient frame.
            _close_stream_live()
            if accumulated.strip():
                render_markdown(accumulated)
            raise
        finally:
            # If spinner is still active, stop it.
            if not first_token_received:
                spinner_status.__exit__(None, None, None)

    finally:
        pass  # outer try: ensure spinner exit regardless of inner raise

    # ── Stop spinner if it's somehow still running ──────────────
    if not first_token_received:
        spinner_status.__exit__(None, None, None)

    # ── Final rendering ─────────────────────────────────────────────
    # Close any remaining reasoning block.
    if in_reasoning and reasoning_text:
        _close_stream_live()
        render_thinking(reasoning_text)
        console.print()

    # Close the streaming display (transient — erases its tail frame)
    # and print the full Markdown response exactly once.
    _close_stream_live()
    if accumulated.strip():
        render_markdown(accumulated)

    # Final newline.
    console.print()

    # Show tool execution summary when tools were used this turn.
    if tool_progress.is_active():
        tool_progress.render_summary()

    # Show turn summary (duration + tokens).
    timer.__exit__(None, None, None)
    render_turn_end(timer.elapsed, usage or session.total_usage)

    # ── Update live token stats for the bottom toolbar ─────────────
    if stats is not None:
        tu = session.total_usage
        stats["prompt"] = tu.get("prompt_tokens", 0)
        stats["completion"] = tu.get("completion_tokens", 0)
        stats["total"] = tu.get("total_tokens", 0)
        stats["turns"] += 1

    # ── Update last user message for /redo ────────────────────────
    session._last_user_message = text


async def run_oneshot(session: SessionRuntime, prompt: str, *, echo: bool = False) -> None:
    """Run a single prompt and print the result.

    Used by the CLI's one-shot mode (e.g. ``lilith -p "hola"``).
    """
    render_user_separator(prompt)
    await _process_with_streaming(session, prompt)
    if echo:
        console.print("\n[dim]── Sesión finalizada ──[/]")

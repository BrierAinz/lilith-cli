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
import sys
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
    from .agent import AgentSession
from .commands import CommandRegistry
from .config import CONFIG_DIR
from .extra_commands import (
    _load_aliases,
    run_alias_command,
    run_base64_command,
    run_bench_command,
    run_capture_command,
    run_changelog_command,
    run_compact_command,
    run_compare_command,
    run_conclave_command,
    run_learn_command,
    run_cost_command,
    run_deps_command,
    run_diff_staged_command,
    run_doctor_command,
    run_editor_command,
    run_env_command,
    run_explain_command,
    run_feedback_command,
    run_fork_command,
    run_git_command,
    run_hash_command,
    run_help_command,
    run_history_command,
    run_hooks_command,
    run_json_command,
    run_json_mode_command,
    run_last_tool_command,
    run_lint_command,
    run_lint_fix_command,
    run_lines_command,
    run_log_command,
    run_macro_command,
    run_metrics_command,
    run_model_info_command,
    run_multi_file_command,
    run_now_command,
    run_pin_command,
    run_profile_command,
    run_qr_command,
    run_test_command,
    run_recap_command,
    run_redact_command,
    run_replay_command,
    run_release_command,
    run_reverse_command,
    run_review_command,
    run_search_command,
    run_snippet_command,
    run_summary_command,
    run_secret_command,
    run_stream_command,
    run_tip_command,
    run_tokens_command,
    run_usage_command,
    run_uuid_command,
    run_voice_command,
    run_todos_command,
    run_whereami_command,
    run_tour_command,
    run_tree_command,
    run_watch_command,
)

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
from .pipeline_command import run_pipeline_command
from .tool_progress import ToolProgressTracker, render_tool_progress
from .trace import AgentTrace
from .workflow_command import run_workflow_command


# ── Prompt constants ────────────────────────────────────────────────

_HISTORY_FILE = CONFIG_DIR / "history"
_CONVERSATIONS_DIR = CONFIG_DIR / "conversations"

_SLASH_COMMANDS = [
    "/tools",
    "/model",
    "/provider",
    "/memory",
    "/costs",
    "/state",
    "/skills",
    "/clear",
    "/status",
    "/env",
    "/git",
    "/todos",
    "/search",
    "/s",
    "/workflow",
    "/pipeline",
    "/bench",
    "/diff-config",
    "/diffconfig",
    "/dcfg",
    "/deps",
    "/compare",
    "/conclave",
    "/learn",
    "/editor",
    "/diff-staged",
    "/diffstaged",
    "/config",
    "/quit",
    "/exit",
    "/q",
    "/redo",
    "/r",
    "/retry",
    "/reintentar",
    "/continue",
    "/cont",
    "/copy",
    "/recap",
    "/summary",
    "/alias",
    "/changelog",
    "/stream",
    "/history",
    "/hist",
    "/last-tool",
    "/compact",
    "/summarize",
    "/resume",
    "/load",
    "/theme",
    "/themes",
    "/file",
    "/f",
    "/export",
    "/exp",
    "/capture",
    "/cap",
    "/h",
    "/?",
    "/lint",
    "/l",
    "/review",
    "/rev",
    "/secret",
    "/redact",
    "/watch",
    "/w",
    "/tip",
    "/pin",
    "/p",
    "/profile",
    "/test",
    "/tour",
    "/tree",
    "/log",
    "/fork",
    "/model-info",
    "/macro",
    "/m",
    "/cls",
    "/agent",
    "/mode",
    "/modo",
    "/template",
    "/templates",
    "/tpl",
    "/auto",
    "/json-mode",
    "/metrics",
    "/tokens",
    "/usage",
    "/mcp",
    "/mcps",
    "/subagents",
    "/sa",
]


def _expand_user_alias(cmd_name: str, cmd_args: str) -> tuple[str, str] | None:
    """Resolve un alias de usuario (aliases.json) a ``(cmd_name, cmd_args)``.

    Expansión de un solo nivel: los comandos built-in (los listados en
    ``_SLASH_COMMANDS``) siempre ganan y no pueden ser sombreados; el
    target del alias es un comando de barra (con o sin ``/`` inicial) y
    los argumentos extra del usuario se anexan a los del alias.
    Devuelve ``None`` si no hay alias aplicable.
    """
    if f"/{cmd_name}" in _SLASH_COMMANDS:
        return None
    target = _load_aliases().get(cmd_name)
    if not target:
        return None
    expanded = target.strip().lstrip("/")
    if not expanded:
        return None
    full = f"{expanded} {cmd_args}".strip()
    parts = full.split(maxsplit=1)
    return parts[0].lower(), parts[1] if len(parts) > 1 else ""


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


def _auto_save_conversation(session: AgentSession) -> Path | None:
    """Save the conversation history as JSON. Returns the filepath or None."""
    if not session.history:
        return None

    _CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filepath = _CONVERSATIONS_DIR / f"conv_{timestamp}.json"

    data = {
        "timestamp": timestamp,
        "model": session.config.model,
        "provider": session.config.provider,
        "messages": session.history,
        "usage": session.total_usage,
        "per_model_usage": session._per_model_usage,
    }
    try:
        filepath.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
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
                    "name": fpath.stem,
                    "timestamp": data.get("timestamp", ""),
                    "model": data.get("model", "unknown"),
                    "provider": data.get("provider", "unknown"),
                    "message_count": len(messages),
                    "usage": data.get("usage", {}),
                    "preview": preview,
                },
            )
        except Exception:
            continue

    return conversations


def _load_conversation(filepath: Path) -> dict[str, Any] | None:
    """Load a conversation JSON file. Returns the full data dict or None."""
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except Exception as exc:
        render_error(f"Error cargando conversación: {exc}")
        return None


# ── Main REPL ───────────────────────────────────────────────────────


async def run_repl(session: AgentSession) -> None:
    """Launch the interactive REPL loop."""
    # Ensure directories exist.
    _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

    # ── Load saved theme from config ───────────────────────────────
    try:
        import yaml as _yaml

        from .config import CONFIG_FILE

        _raw = _yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
        _saved_theme = _raw.get("theme", "norse")
        if _saved_theme in [t.name for t in list_themes()]:
            set_theme(_saved_theme)
    except Exception:
        pass  # Fall back to default theme.

    # ── Render welcome ────────────────────────────────────────────
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
    completer = WordCompleter(_SLASH_COMMANDS, ignore_case=True, sentence=True)

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
        bottom_toolbar=_bottom_toolbar,
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
        render_tool_call(name, args)
        rendered = render_tool_result(name, result)
        if rendered is not None:
            console.print(rendered)

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
                parts = text[1:].split(maxsplit=1)
                cmd_name = parts[0].lower()
                cmd_args = parts[1] if len(parts) > 1 else ""

                # ── User alias expansion (aliases.json, un nivel) ─────
                expanded_alias = _expand_user_alias(cmd_name, cmd_args)
                if expanded_alias is not None:
                    cmd_name, cmd_args = expanded_alias
                    text = f"/{cmd_name} {cmd_args}".strip()
                if cmd_name == "macro":
                    from .commands import _macro_recording as macro_state
                    # While recording, append slash commands to the macro and
                    # skip normal dispatch so the commands are not executed
                    # until playback.
                    if id(session) in macro_state and text.startswith("/"):
                        macro_state[id(session)].append(text)
                        console.print(f"[dim]  + grabado: {text}[/]")
                        continue
                    await run_macro_command(session, cmd_args)
                    continue
                if cmd_name == "git":
                    await run_git_command(session, cmd_args)
                    continue
                if cmd_name == "hash":
                    await run_hash_command(session, cmd_args)
                    continue
                if cmd_name in ("help", "h", "?"):
                    await run_help_command(session, cmd_args)
                    continue
                if cmd_name in ("diff-staged", "diffstaged"):
                    await run_diff_staged_command(session, cmd_args)
                    continue
                if cmd_name == "todos":
                    await run_todos_command(session, cmd_args)
                    continue
                if cmd_name in ("search", "s"):
                    await run_search_command(session, cmd_args)
                    continue
                if cmd_name == "workflow":
                    await run_workflow_command(session, cmd_args)
                    continue
                if cmd_name == "base64":
                    await run_base64_command(session, cmd_args)
                    continue
                if cmd_name == "bench":
                    await run_bench_command(session, cmd_args)
                    continue
                if cmd_name == "pipeline":
                    await run_pipeline_command(session, cmd_args)
                    continue
                if cmd_name == "lint":
                    await run_lint_command(session, cmd_args)
                    continue
                if cmd_name == "review":
                    await run_review_command(session, cmd_args)
                    continue
                if cmd_name == "secret":
                    await run_secret_command(session, cmd_args)
                    continue
                if cmd_name == "redact":
                    await run_redact_command(session, cmd_args)
                    continue
                if cmd_name == "changelog":
                    await run_changelog_command(session, cmd_args)
                    continue
                if cmd_name in ("watch", "w"):
                    await run_watch_command(session, cmd_args)
                    continue
                if cmd_name == "tip":
                    await run_tip_command(session, cmd_args)
                    continue
                if cmd_name == "voice":
                    await run_voice_command(session, cmd_args)
                    continue
                if cmd_name == "multi-file":
                    await run_multi_file_command(session, cmd_args)
                    continue
                if cmd_name == "now":
                    await run_now_command(session, cmd_args)
                    continue
                if cmd_name == "release":
                    await run_release_command(session, cmd_args)
                    continue
                if cmd_name in ("reverse", "rev"):
                    await run_reverse_command(session, cmd_args)
                    continue
                if cmd_name in ("pin", "p"):
                    await run_pin_command(session, cmd_args)
                    continue
                if cmd_name == "profile":
                    await run_profile_command(session, cmd_args)
                    continue
                if cmd_name == "test":
                    await run_test_command(session, cmd_args)
                    continue
                if cmd_name == "hooks":
                    await run_hooks_command(session, cmd_args)
                    continue
                if cmd_name == "json":
                    await run_json_command(session, cmd_args)
                    continue
                if cmd_name == "json-mode":
                    await run_json_mode_command(session, cmd_args)
                    continue
                if cmd_name == "last-tool":
                    await run_last_tool_command(session, cmd_args)
                    continue
                if cmd_name == "compact":
                    await run_compact_command(session, cmd_args)
                    continue
                if cmd_name == "cost":
                    await run_cost_command(session, cmd_args)
                    continue
                if cmd_name == "fork":
                    await run_fork_command(session, cmd_args)
                    continue
                if cmd_name == "tree":
                    await run_tree_command(session, cmd_args)
                    continue
                if cmd_name == "uuid":
                    await run_uuid_command(session, cmd_args)
                    continue
                if cmd_name == "qr":
                    await run_qr_command(session, cmd_args)
                    continue
                if cmd_name == "alias":
                    await run_alias_command(session, cmd_args)
                    continue
                if cmd_name == "env":
                    await run_env_command(session, cmd_args)
                    continue
                if cmd_name == "replay":
                    await run_replay_command(session, cmd_args)
                    continue
                if cmd_name == "tour":
                    await run_tour_command(session, cmd_args)
                    continue
                if cmd_name == "model-info":
                    await run_model_info_command(session, cmd_args)
                    continue
                if cmd_name == "stream":
                    await run_stream_command(session, cmd_args)
                    continue
                if cmd_name == "doctor":
                    await run_doctor_command(session, cmd_args)
                    continue
                if cmd_name == "deps":
                    await run_deps_command(session, cmd_args)
                    continue
                if cmd_name == "compare":
                    await run_compare_command(session, cmd_args)
                    continue
                if cmd_name == "conclave":
                    await run_conclave_command(session, cmd_args)
                    continue
                if cmd_name == "learn":
                    await run_learn_command(session, cmd_args)
                    continue
                if cmd_name in ("capture", "cap"):
                    await run_capture_command(session, cmd_args)
                    continue
                if cmd_name in ("log", "l"):
                    await run_log_command(session, cmd_args)
                    continue
                if cmd_name == "recap":
                    await run_recap_command(session, cmd_args)
                    continue
                if cmd_name == "summary":
                    await run_summary_command(session, cmd_args)
                    continue
                if cmd_name == "snippet":
                    await run_snippet_command(session, cmd_args)
                    continue
                if cmd_name == "editor":
                    await run_editor_command(session, cmd_args)
                    continue
                if cmd_name == "history":
                    await run_history_command(session, cmd_args)
                    continue
                if cmd_name == "explain":
                    await run_explain_command(session, cmd_args)
                    continue
                if cmd_name == "whereami":
                    await run_whereami_command(session, cmd_args)
                    continue
                if cmd_name == "lint-fix":
                    await run_lint_fix_command(session, cmd_args)
                    continue
                if cmd_name == "lines":
                    await run_lines_command(session, cmd_args)
                    continue
                if cmd_name == "metrics":
                    await run_metrics_command(session, cmd_args)
                    continue
                if cmd_name == "tokens":
                    await run_tokens_command(session, cmd_args)
                    continue
                if cmd_name == "usage":
                    await run_usage_command(session, cmd_args)
                    continue
                try:
                    handled = await registry.dispatch(text)
                    if handled:
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
    session: AgentSession,
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
    tool_progress = ToolProgressTracker()

    # ── Live delegation streaming panel (tanda 6, ITEM 3) ────────────
    # delegate_subagent runs on a worker thread (asyncio.to_thread) so
    # we cannot pipe events into the agent loop directly. Instead we
    # open a dedicated Live panel between the ``tool_call`` and
    # ``tool_result`` events for that name and poll a thread-safe
    # buffer. tool_progress.pause_live() frees the single Rich Live
    # slot while our panel runs, mirroring how streaming alternates
    # with the tool tracker elsewhere in this loop.
    delegation_live: "DelegationLive | None" = None
    delegation_buffer: "DelegationStreamBuffer | None" = None

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

                    # ── Reasoning (Kimi / GLM-5.1 / DeepSeek thinking) ──
                    if event_type == "reasoning":
                        chunk = event.get("content", "")
                        if chunk:
                            # Show assistant separator before first output.
                            if not _assistant_sep_shown:
                                _assistant_sep_shown = True
                                render_assistant_separator()
                            # Stop spinner: the live panel takes over.
                            if not first_token_received:
                                first_token_received = True
                                spinner_status.__exit__(None, None, None)

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
                            # Show assistant separator before first output.
                            if not _assistant_sep_shown:
                                _assistant_sep_shown = True
                                render_assistant_separator()
                            # Stop spinner on first real token.
                            if not first_token_received:
                                first_token_received = True
                                spinner_status.__exit__(None, None, None)

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
                        if not _assistant_sep_shown:
                            _assistant_sep_shown = True
                            render_assistant_separator()
                        if not first_token_received:
                            first_token_received = True
                            spinner_status.__exit__(None, None, None)

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

                        tool_progress.start(event["name"])

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
                        tool_progress.complete(event["name"], error=error_str)

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

                        rendered = render_tool_result(event["name"], event["content"])
                        if rendered is not None:
                            console.print(rendered)

                        # Also render the tool call card now that we have the result,
                        # so users see the arguments that produced it.
                        render_tool_call(
                            event["name"], event.get("arguments", {}), result=event["content"]
                        )

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


async def run_oneshot(session: AgentSession, prompt: str, *, echo: bool = False) -> None:
    """Run a single prompt and print the result.

    Used by the CLI's one-shot mode (e.g. ``lilith -p "hola"``).
    """
    render_user_separator(prompt)
    await _process_with_streaming(session, prompt)
    if echo:
        console.print("\n[dim]── Sesión finalizada ──[/]")

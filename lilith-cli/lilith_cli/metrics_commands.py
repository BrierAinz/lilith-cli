"""Comandos de metricas, tokens y costos de la sesion.

Agrupa /metrics, /tokens, /usage y /bench: los tres primeros comparten los
helpers que leen y formatean el estado de uso, asi que no pueden separarse
sin duplicar logica. Extraidos de extra_commands.py, que superaba las 11.000
lineas y donde la herramienta patch colisiona con cientos de coincidencias.

extra_commands.py los reexporta: ningun import existente cambia."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import time
from .render import console, get_theme, render_error, set_theme


async def run_metrics_command(session, args: str) -> None:
    """Aggregate session metrics: tokens + tool calls + commands + file edits.

    Subcommands (same as legacy MetricsCommand):
        /metrics               — full summary panel
        /metrics tools         — tool breakdown (Table)
        /metrics commands      — most-used slash commands (Table)
        /metrics files         — most-edited files (Table)
        /metrics json          — machine-readable JSON
        /metrics all           — alias for no-subcommand
    """
    import json
    import sys

    from rich.panel import Panel
    from rich.table import Table

    subcmd = args.strip().lower()
    if subcmd == "json":
        _metrics_emit_json(session, sys.stdout)
        return

    if not subcmd or subcmd == "all":
        await _metrics_show_summary(session)
        return

    if subcmd == "tools":
        _metrics_show_tools(session)
        return

    if subcmd == "commands":
        _metrics_show_commands(session)
        return

    if subcmd == "files":
        _metrics_show_files(session)
        return

    render_error(
        "Uso: /metrics [tools|commands|files|json|all] — muestra métricas de la sesión",
    )


async def run_tokens_command(session, args: str) -> None:  # noqa: ARG001
    """Show session token usage in a colored grid panel (/tokens).

    Same data as the legacy TokensCommand — prompt / completion / total —
    but rendered as a Rich Panel with color-coded values (green / yellow / red).
    """
    from rich.panel import Panel
    from rich.table import Table

    usage = session.total_usage
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    total = usage.get("total_tokens", 0)

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")
    grid.add_row(
        "Prompt",
        f"[{_usage_color(prompt)}]{prompt:,}[/{_usage_color(prompt)}]",
    )
    grid.add_row(
        "Completion",
        f"[{_usage_color(completion)}]{completion:,}[/{_usage_color(completion)}]",
    )
    grid.add_row(
        "Total",
        f"[bold {_usage_color(total)}]{total:,}[/bold {_usage_color(total)}]",
    )

    console.print(Panel(
        grid,
        title="[bold realm]᛭ Tokens de la sesión[/]",
        border_style="cyan",
        expand=False,
    ))
    console.print()


async def run_usage_command(session, args: str) -> None:
    """Detailed session statistics: tokens, cost, tools, messages, duration.

    Subcommands (same as legacy UsageCommand):
        /usage         — full statistics grid panel
        /usage json    — machine-readable JSON
    """
    import json
    import sys

    from .providers import estimate_cost
    from rich.panel import Panel
    from rich.table import Table

    usage = session.total_usage
    model = session.config.model
    total_cost = estimate_cost(
        model,
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
    )
    tool_counts = session.tool_call_counts
    msg_counts = session.message_counts
    duration = session.session_duration()
    duration_str = _format_duration_short(duration)
    start_time = session.session_start.strftime("%Y-%m-%d %H:%M:%S")

    if args.strip().lower() == "json":
        data = {
            "tokens": {
                "prompt": usage.get("prompt_tokens", 0),
                "completion": usage.get("completion_tokens", 0),
                "total": usage.get("total_tokens", 0),
            },
            "cost": {
                "total_usd": round(total_cost, 6),
                "per_model": session.per_model_usage,
            },
            "tool_calls": tool_counts,
            "messages": msg_counts,
            "session": {
                "start_time": session.session_start.isoformat(),
                "duration_seconds": duration,
                "duration_human": duration_str,
            },
        }
        # Bypass Rich console for JSON to avoid markup interpretation.
        sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()
        return

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    total = usage.get("total_tokens", 0)

    grid.add_row("[bold frost]Tokens — Prompt[/]", f"[{_usage_color(prompt)}]{prompt:,}[/{_usage_color(prompt)}]")
    grid.add_row("[bold frost]Tokens — Completion[/]", f"[{_usage_color(completion)}]{completion:,}[/{_usage_color(completion)}]")
    grid.add_row("[bold frost]Tokens — Total[/]", f"[bold {_usage_color(total)}]{total:,}[/bold {_usage_color(total)}]")

    grid.add_row("[bold frost]Costo[/]", f"[model]${total_cost:.4f} USD[/]")

    if tool_counts:
        tool_lines = ", ".join(
            f"[tool.name]{name}[/]: {cnt}"
            for name, cnt in sorted(tool_counts.items(), key=lambda x: -x[1])
        )
    else:
        tool_lines = "[dim](ninguna)[/]"
    grid.add_row("[bold frost]Herramientas[/]", tool_lines)

    msg_total = sum(msg_counts.values())
    grid.add_row(
        "[bold frost]Mensajes[/]",
        f"usuario [cyan]{msg_counts.get('user', 0)}[/] · "
        f"asistente [cyan]{msg_counts.get('assistant', 0)}[/] · "
        f"herramienta [cyan]{msg_counts.get('tool', 0)}[/] · "
        f"total [bold cyan]{msg_total}[/]",
    )

    grid.add_row("[bold frost]Inicio[/]", f"[dim]{start_time}[/]")
    grid.add_row("[bold frost]Duración[/]", f"[bold cyan]{duration_str}[/]")

    console.print(Panel(
        grid,
        title="[bold realm]᛭ Estadísticas de la sesión[/]",
        border_style="cyan",
        expand=False,
    ))
    console.print()

    # Per-model breakdown table if multiple models have been used.
    if len(session.per_model_usage) > 1:
        table = Table(
            title="[bold realm]Desglose por modelo[/]",
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
            expand=False,
        )
        table.add_column("Modelo", style="model")
        table.add_column("Prompt", justify="right")
        table.add_column("Completion", justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Costo", justify="right")
        for m, stats in sorted(session.per_model_usage.items()):
            table.add_row(
                m,
                str(stats.get("prompt_tokens", 0)),
                str(stats.get("completion_tokens", 0)),
                str(stats.get("total_tokens", 0)),
                f"${stats.get('cost', 0.0):.4f}",
            )
        console.print(table)
        console.print()


async def run_bench_command(session: AgentSession, args: str) -> None:  # noqa: ARG001
    """Ejecuta /bench para medir latencias del proveedor actual.

    Examples:
        /bench
        /bench --turns 3
        /bench --provider openai --model gpt-4o
    """
    import argparse

    parser = argparse.ArgumentParser(prog="/bench", add_help=False)
    parser.add_argument("--turns", type=int, default=1)
    parser.add_argument("--provider", default=session.config.provider)
    parser.add_argument("--model", default=session.config.model)
    parser.add_argument("--prompt", default="Explain quantum computing in one sentence.")
    try:
        parsed = parser.parse_args(shlex.split(args) if args.strip() else [])
    except SystemExit:
        return

    from .providers import create_provider

    cfg = session.config
    bench_config = cfg
    if parsed.provider != cfg.provider or parsed.model != cfg.model:
        # clone config and override provider/model for the benchmark run
        bench_config = cfg.model_copy() if hasattr(cfg, "model_copy") else cfg
        bench_config.provider = parsed.provider
        bench_config.model = parsed.model

    provider = create_provider(bench_config)
    latencies: list[float] = []
    ttft_values: list[float] = []
    total_tokens = 0

    console.print(f"\n[bold realm]᛭ Benchmark[/] {parsed.model} @ {parsed.provider} ({parsed.turns} turnos)")
    for i in range(1, parsed.turns + 1):
        timer_start = time.perf_counter()
        first_token_time: float | None = None
        token_count = 0
        try:
            async for event in provider.stream(
                [{"role": "user", "content": parsed.prompt}],
                model=parsed.model,
            ):
                if event.get("content"):
                    if first_token_time is None:
                        first_token_time = time.perf_counter()
                    token_count += len(event.get("content", "").split())
                if event.get("finish_reason"):
                    break
        except Exception as exc:
            render_error(f"Benchmark falló en turno {i}: {exc}")
            return
        elapsed = time.perf_counter() - timer_start
        latencies.append(elapsed)
        ttft = (first_token_time - timer_start) if first_token_time else None
        if ttft is not None:
            ttft_values.append(ttft)
        total_tokens += token_count
        console.print(f"  Turno {i}: {elapsed:.3f}s" + (f" (TTFT {ttft:.3f}s)" if ttft else ""))

    await provider.close()

    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    avg_ttft = sum(ttft_values) / len(ttft_values) if ttft_values else None
    console.print(f"\n[bold]Resumen:[/]")
    console.print(f"  Latencia promedio: {avg_latency:.3f}s")
    if avg_ttft is not None:
        console.print(f"  TTFT promedio: {avg_ttft:.3f}s")
    if avg_latency > 0 and total_tokens > 0:
        console.print(f"  Tokens/segundo: {total_tokens / sum(latencies):.2f}")
    console.print()


def _command_metrics(session) -> dict[str, int]:
    """Count slash command invocations from session history."""
    history = getattr(session, "_command_history", None)
    if history is None:
        return {}
    counts: dict[str, int] = {}
    for entry in history:
        name = entry.get("name")
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _file_edit_metrics(session) -> dict[str, int]:
    """Count file edits per path from session history."""
    history = getattr(session, "_file_edit_history", None)
    if history is None:
        return {}
    counts: dict[str, int] = {}
    for entry in history:
        path = entry.get("path")
        if path:
            counts[path] = counts.get(path, 0) + 1
    return counts


def _fmt_secs(seconds: float) -> str:
    """Format seconds as 'X.XXXs' or 'Xms' for small values."""
    if seconds < 0.001:
        return "0.000s"
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.3f}s"


def _format_duration_short(seconds: float) -> str:
    """Human-readable duration like 3m 14s or 42s."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m"


def _metrics_emit_json(session, stream) -> None:
    """Emit machine-readable JSON via stream (bypasses Rich markup)."""
    import json

    counts, avg, total = _tool_metrics(session)
    data = {
        "tokens": dict(session.total_usage),
        "tools": {
            "total": total,
            "counts": counts,
            "average_duration": avg,
        },
        "commands": _command_metrics(session),
        "files": _file_edit_metrics(session),
        "session": {
            "start_time": session.session_start.isoformat(),
            "duration_seconds": session.session_duration(),
        },
    }
    stream.write(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")
    stream.flush()


def _metrics_show_commands(session) -> None:
    """Detailed slash-command breakdown (Table)."""
    from rich.table import Table

    counts = _command_metrics(session)
    console.print()
    console.print("[bold realm]᛭ Métricas de comandos[/]")
    if not counts:
        console.print("  [dim](ninguno)[/]")
        console.print()
        return

    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        expand=False,
    )
    table.add_column("Comando", style="tool.name")
    table.add_column("Usos", justify="right")
    for name, count in sorted(counts.items(), key=lambda x: -x[1]):
        table.add_row(f"/{name}", str(count))
    console.print(table)
    console.print()


def _metrics_show_files(session) -> None:
    """Detailed file-edit breakdown (Table)."""
    from rich.table import Table

    counts = _file_edit_metrics(session)
    console.print()
    console.print("[bold realm]᛭ Métricas de archivos editados[/]")
    if not counts:
        console.print("  [dim](ninguno)[/]")
        console.print()
        return

    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        expand=False,
    )
    table.add_column("Archivo", style="tool.name")
    table.add_column("Ediciones", justify="right")
    for path, count in sorted(counts.items(), key=lambda x: -x[1]):
        table.add_row(path, str(count))
    console.print(table)
    console.print()


async def _metrics_show_summary(session) -> None:
    """Top-level summary: tokens, top tools, top commands, top files."""
    from rich.panel import Panel
    from rich.table import Table

    usage = session.total_usage
    counts, avg, total = _tool_metrics(session)
    cmd_counts = _command_metrics(session)
    file_counts = _file_edit_metrics(session)

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    # Tokens
    grid.add_row(
        "[bold frost]Tokens[/]",
        f"prompt [cyan]{usage.get('prompt_tokens', 0):,}[/] · "
        f"completion [cyan]{usage.get('completion_tokens', 0):,}[/] · "
        f"total [{_usage_color(usage.get('total_tokens', 0))}]"
        f"{usage.get('total_tokens', 0):,}[/{_usage_color(usage.get('total_tokens', 0))}]",
    )

    # Tool calls
    if counts:
        top_tools = sorted(counts.items(), key=lambda x: -x[1])[:5]
        tool_lines = ", ".join(
            f"[tool.name]{name}[/]: {cnt} (avg {_fmt_secs(avg[name])})"
            for name, cnt in top_tools
        )
    else:
        status = _telemetry_status(session)
        if not status["tools"]:
            tool_lines = "[info](telémetría no activa en esta sesión)[/]"
        else:
            tool_lines = "[dim](ninguna)[/]"
    grid.add_row(f"[bold frost]Herramientas[/] ({total})", tool_lines)

    # Slash commands
    if cmd_counts:
        top_cmds = sorted(cmd_counts.items(), key=lambda x: -x[1])[:5]
        cmd_lines = ", ".join(
            f"[tool.name]/{name}[/]: {cnt}" for name, cnt in top_cmds
        )
    else:
        status = _telemetry_status(session)
        if not status["commands"]:
            cmd_lines = "[info](telémetría no activa en esta sesión)[/]"
        else:
            cmd_lines = "[dim](ninguno)[/]"
    grid.add_row("[bold frost]Comandos[/]", cmd_lines)

    # File edits
    if file_counts:
        top_files = sorted(file_counts.items(), key=lambda x: -x[1])[:5]
        file_lines = ", ".join(
            f"[tool.name]{path}[/]: {cnt}" for path, cnt in top_files
        )
    else:
        status = _telemetry_status(session)
        if not status["files"]:
            file_lines = "[info](telémetría no activa en esta sesión)[/]"
        else:
            file_lines = "[dim](ninguno)[/]"
    grid.add_row("[bold frost]Archivos editados[/]", file_lines)

    console.print(Panel(
        grid,
        title="[bold realm]᛭ Métricas de la sesión[/]",
        border_style="cyan",
        expand=False,
    ))
    console.print()


def _metrics_show_tools(session) -> None:
    """Detailed tool call breakdown (Table)."""
    from rich.table import Table

    counts, avg, total = _tool_metrics(session)
    console.print()
    console.print(f"[bold realm]᛭ Métricas de herramientas[/] [dim]({total} llamadas)[/dim]")
    if not counts:
        console.print("  [dim](ninguna)[/]")
        console.print()
        return

    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
        expand=False,
    )
    table.add_column("Herramienta", style="tool.name")
    table.add_column("Llamadas", justify="right")
    table.add_column("Duración promedio", justify="right")
    for name, count in sorted(counts.items(), key=lambda x: -x[1]):
        table.add_row(name, str(count), _fmt_secs(avg[name]))
    console.print(table)
    console.print()


def _telemetry_status(session) -> dict[str, bool]:
    """Which telemetry lists are active on the session.

    Lets the renderer distinguish 'telemetry off in this session' from
    'no events recorded yet' — the latter is a normal empty state,
    the former is an actionable hint.
    """
    return {
        "tools": hasattr(session, "_tool_call_history"),
        "commands": hasattr(session, "_command_history"),
        "files": hasattr(session, "_file_edit_history"),
    }


def _tool_metrics(session) -> tuple[dict[str, int], dict[str, float], int]:
    """Aggregate tool call counts and average duration from session history.

    Returns (counts, averages, total). If the session was never wired with
    telemetry tracking (``_tool_call_history`` attribute missing), returns
    empty dicts + 0 — caller can detect this via ``_telemetry_status``.
    """
    history = getattr(session, "_tool_call_history", None)
    if history is None:
        return {}, {}, 0
    counts: dict[str, int] = {}
    durations: dict[str, list[float]] = {}
    total = 0
    for entry in history:
        name = entry.get("name")
        if not name:
            continue
        total += 1
        counts[name] = counts.get(name, 0) + 1
        durations.setdefault(name, []).append(entry.get("duration", 0.0))
    avg: dict[str, float] = {
        name: sum(durs) / len(durs) if durs else 0.0
        for name, durs in durations.items()
    }
    return counts, avg, total


def _usage_color(value: int) -> str:
    """Color a token count by usage tier (green < 4k < yellow < 16k < red)."""
    if value < 4000:
        return "green"
    if value < 16000:
        return "yellow"
    return "red"

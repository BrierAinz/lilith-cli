"""Environment slash commands: /env, /secret, /redact, /doctor, /cd, /pwd and /cls."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import CONFIG_DIR
from ..render import console, render_error

if TYPE_CHECKING:
    from ..session_runtime import SessionRuntime
from lilith_tools.env import EnvGetTool, EnvListTool, SysInfoTool

from ..doctor import apply_fixes, run_diagnostics

# ---------------------------------------------------------------------------
# Redaction helper for /redact: replace sensitive substrings with [REDACTED].
# Builtin patterns cover common credentials, contacts, and PII.
# ---------------------------------------------------------------------------
_REDACT_PATTERNS: dict[str, tuple[str, ...]] = {
    "api_key": (r"(?i)api[_-]?key\s*[=:]\s*\S+",),
    "password": (r"(?i)password\s*[=:]\s*\S+",),
    "secret": (r"(?i)secret\s*[=:]\s*\S+", r"(?i)token\s*[=:]\s*\S+"),
    "email": (r"[\w.+-]+@[\w-]+\.[\w.-]+",),
    "ssn": (r"\b\d{3}-\d{2}-\d{4}\b",),
    "credit_card": (r"\b\d{4}\s\d{4}\s\d{4}\s\d{4}\b",),
}


def _redact_text(text: str, patterns: list[str] | None = None) -> str:
    """Replace sensitive substrings in *text* with ``[REDACTED]``.

    When *patterns* is None (default), every builtin pattern is applied.
    When *patterns* is a list of names from :data:`_REDACT_PATTERNS`, only
    those patterns are applied. Unknown names are silently ignored.
    """
    import re

    if patterns is None:
        selected = _REDACT_PATTERNS.values()
    else:
        selected = (_REDACT_PATTERNS[name] for name in patterns if name in _REDACT_PATTERNS)

    redacted = text
    for pattern_tuple in selected:
        for pattern in pattern_tuple:
            redacted = re.sub(pattern, "[REDACTED]", redacted)
    return redacted


async def run_redact_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /redact para ocultar información sensible.

    Examples:
        /redact <archivo>
        /redact <archivo> --out <salida>
        /redact <archivo> --patterns api_key,password
    """
    import argparse as _argparse
    import shlex as _shlex

    text = args.strip()
    if not text:
        render_error("Uso: /redact <archivo> [--out <salida>] [--patterns <p1,p2,...>]")
        return

    parser = _argparse.ArgumentParser(prog="/redact", add_help=False)
    parser.add_argument("file", nargs="?")
    parser.add_argument("--out", dest="out")
    parser.add_argument("--patterns", dest="patterns", default="")
    try:
        parsed, _ = parser.parse_known_args(_shlex.split(text))
    except Exception as exc:
        render_error(f"Error parseando argumentos: {exc}")
        return

    if not parsed.file:
        render_error("Uso: /redact <archivo> [--out <salida>] [--patterns <p1,p2,...>]")
        return

    path = Path(parsed.file)
    if not path.exists():
        render_error(f"Archivo no encontrado: {path}")
        return

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        render_error(f"Error leyendo {path}: {exc}")
        return

    selected_patterns = [p.strip() for p in parsed.patterns.split(",") if p.strip()] or None
    redacted = _redact_text(content, selected_patterns)

    if parsed.out:
        try:
            out_path = Path(parsed.out)
            out_path.write_text(redacted, encoding="utf-8")
            console.print(f"[success]✓ Redactado guardado en {out_path}[/]")
        except Exception as exc:
            render_error(f"Error escribiendo {parsed.out}: {exc}")
        return

    console.print(redacted)


def _print_env_json(result) -> None:
    """Print EnvListTool result as JSON via sys.stdout (bypasses Rich markup)."""
    import json as _json
    import sys as _sys

    if hasattr(result, "data") and result.data is not None:
        payload = result.data
    elif hasattr(result, "output"):
        payload = {"output": result.output}
    else:
        payload = result
    _sys.stdout.write(_json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n")
    _sys.stdout.flush()


async def run_env_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /env [name|info|prefix <PREFIX>|unset <name>|snapshot|diff] [--json].

    Examples:
        /env PATH
        /env info
        /env prefix PYTHON
        /env unset FOO
        /env snapshot                       — capture current env to disk
        /env diff                           — show changes since snapshot
        /env --json                         — all env vars as JSON
        /env prefix PYTHON --json           — filtered JSON output
    """

    text = args.strip()

    # /env --json alone means list all as JSON
    if text.lower() == "--json":
        tool = EnvListTool()
        result = tool.execute()
        _print_env_json(result)
        return

    if not text or text.lower() in ("list", "ls", "all"):
        tool = EnvListTool()
        result = tool.execute()
        if "--json" in text.lower().split():
            _print_env_json(result)
            return
        _print_env_list(result)
        return

    parts = text.split(maxsplit=1)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd == "info":
        tool = SysInfoTool()
        result = tool.execute()
        _print_sys_info(result)
        return

    if subcmd == "prefix":
        if not rest:
            render_error("Uso: /env prefix <PREFIX>")
            return
        tool = EnvListTool()
        # Check for --json after prefix value: /env prefix X --json
        json_mode = False
        prefix_value = rest
        if " --json" in rest:
            parts = rest.split(" --json")
            prefix_value = parts[0].strip()
            json_mode = True
        result = tool.execute(prefix=prefix_value)
        if json_mode:
            _print_env_json(result)
        else:
            _print_env_list(result)
        return

    if subcmd == "unset":
        name = rest.strip()
        if not name:
            render_error("Uso: /env unset <name>")
            return
        console.print(f"[warning]⚠ Simulación: eliminaría {name}={os.environ.get(name, '')!r}[/]")
        console.print("[dim]No se realiza ningún cambio por seguridad.[/]")
        return

    if subcmd == "snapshot":
        _env_snapshot_save()
        return

    if subcmd == "diff":
        await _env_diff_snapshot(_print_env_diff)
        return

    # /env <name> (single env var)
    tool = EnvGetTool()
    result = tool.execute(name=text)
    if not result.success:
        render_error(result.error or f"No se pudo leer {text}")
        return
    _print_env_get(result)


# Path to the persisted env snapshot used by /env snapshot + /env diff.
_ENV_SNAPSHOT_PATH = CONFIG_DIR / "env_snapshot.json"


def _env_snapshot_save() -> None:
    """/env snapshot — capture the current process env to
    ~/.yggdrasil/env_snapshot.json. Used as the baseline for /env diff.

    Only the keys that already have values are written (no empties);
    the file is JSON-encoded for readability and round-trip parity.
    """
    import json as _json

    payload = {k: v for k, v in os.environ.items()}
    try:
        _ENV_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ENV_SNAPSHOT_PATH.write_text(
            _json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        console.print(
            f"[success]✓ Snapshot guardado:[/] {len(payload)} variable(s) en "
            f"[tool.result]{_ENV_SNAPSHOT_PATH}[/]"
        )
    except Exception as exc:
        render_error(f"No pude guardar el snapshot: {exc}")


async def _env_diff_snapshot(renderer) -> None:
    """/env diff — show what's changed in os.environ since the last snapshot.

    Compares the live process env against the file written by
    /env snapshot. Reports three buckets:

    - Added: variables that exist now but weren't in the snapshot.
    - Removed: variables that were in the snapshot but no longer set.
    - Changed: variables whose value differs from the snapshot.

    Returns the diff to the renderer (which formats as a Rich Table).
    If no snapshot exists yet, instructs the user to run
    /env snapshot first.
    """
    import json as _json

    if not _ENV_SNAPSHOT_PATH.exists():
        render_error(
            f"No hay snapshot previo. Ejecutá /env snapshot primero "
            f"(guardaría {_ENV_SNAPSHOT_PATH})."
        )
        return

    try:
        snapshot = _json.loads(_ENV_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot no es un dict")
    except Exception as exc:
        render_error(
            f"Snapshot corrupto en {_ENV_SNAPSHOT_PATH}: {exc}. "
            f"Borrá el archivo o ejecutá /env snapshot de nuevo."
        )
        return

    live = dict(os.environ)
    snapshot_keys = set(snapshot.keys())
    live_keys = set(live.keys())

    added = sorted(live_keys - snapshot_keys)
    removed = sorted(snapshot_keys - live_keys)
    common = snapshot_keys & live_keys
    changed = sorted(k for k in common if snapshot[k] != live[k])

    renderer(added=added, removed=removed, changed=changed, snapshot=snapshot, live=live)


def _print_env_diff(*, added: list, removed: list, changed: list, snapshot: dict, live: dict) -> None:
    """Render the /env diff output as three grouped Rich tables."""
    from rich.table import Table

    if not (added or removed or changed):
        console.print("[dim]Sin cambios respecto al snapshot.[/]")
        return

    def _truncate(s: str, n: int = 80) -> str:
        return s if len(s) <= n else s[: n - 1] + "…"

    if added:
        t = Table(
            title=f"[bold green]+ Añadidas ({len(added)})[/]",
            show_header=True,
            header_style="bold cyan",
            border_style="green",
            expand=False,
        )
        t.add_column("Variable", style="tool.name")
        t.add_column("Valor actual", style="green")
        for k in added:
            t.add_row(k, _truncate(live[k]))
        console.print(t)
        console.print()

    if removed:
        t = Table(
            title=f"[bold red]- Eliminadas ({len(removed)})[/]",
            show_header=True,
            header_style="bold cyan",
            border_style="red",
            expand=False,
        )
        t.add_column("Variable", style="tool.name")
        t.add_column("Valor anterior", style="red")
        for k in removed:
            t.add_row(k, _truncate(snapshot[k]))
        console.print(t)
        console.print()

    if changed:
        t = Table(
            title=f"[bold yellow]~ Cambiadas ({len(changed)})[/]",
            show_header=True,
            header_style="bold cyan",
            border_style="yellow",
            expand=False,
        )
        t.add_column("Variable", style="tool.name")
        t.add_column("Anterior", style="red")
        t.add_column("Actual", style="green")
        for k in changed:
            t.add_row(k, _truncate(snapshot[k]), _truncate(live[k]))
        console.print(t)
        console.print()


def _print_env_get(result) -> None:
    """Renderiza el resultado de env_get."""
    if isinstance(result.data, dict):
        for name, value in result.data.items():
            console.print(f"[tool.name]{name}[/]=[tool.result]{value}[/]")
    else:
        console.print(str(result.data))


def _print_env_list(result) -> None:
    """Renderiza el resultado de env_list."""
    if not result.success:
        render_error(result.error or "Error listando variables de entorno")
        return

    data = result.data or {}
    variables = data.get("variables", {})
    total = data.get("total", len(variables))
    returned = data.get("returned", len(variables))
    prefix = data.get("prefix", "")
    limit = data.get("limit", 50)

    if not variables:
        console.print("[dim]No hay variables de entorno" + (f" con prefijo '{prefix}'" if prefix else "") + ".[/]")
        return

    console.print("\n[bold realm]᛭ Variables de entorno[/]" + (f" — prefijo '{prefix}'" if prefix else ""))
    for name, value in sorted(variables.items()):
        console.print(f"  [tool.name]{name}[/]=[tool.result]{value!r}[/]")
    if total > returned:
        console.print(f"[dim](mostrando {returned} de {total}; límite={limit})[/]")
    console.print()


def _print_sys_info(result) -> None:
    """Renderiza el resultado de sys_info."""
    if not result.success:
        render_error(result.error or "Error obteniendo información del sistema")
        return

    data = result.data or {}
    disk = data.get("disk", {})

    from rich.table import Table

    table = Table(
        title="[bold realm]᛭ Información del sistema[/]",
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
    )
    table.add_column("Propiedad", style="tool.name")
    table.add_column("Valor", style="tool.result")

    table.add_row("Python", f"{data.get('python_version', '?')} ({data.get('python_implementation', '?')})")
    table.add_row("Sistema operativo", str(data.get("os", "?")))
    table.add_row("Versión OS", str(data.get("os_version", "?")))
    table.add_row("Máquina", str(data.get("machine", "?")))
    table.add_row("Procesador", str(data.get("processor", "?")))
    table.add_row("Plataforma", str(data.get("platform", "?")))
    table.add_row("Nodo", str(data.get("node", "?")))

    if isinstance(disk, dict) and "error" not in disk:
        table.add_row(
            "Disco (libre / total)",
            f"{disk.get('free_gb', '?')} GB / {disk.get('total_gb', '?')} GB",
        )
    elif isinstance(disk, dict):
        table.add_row("Disco", f"[error]Error: {disk.get('error')}[/]")

    console.print(table)

# ── Secret / env helpers ────────────────────────────────────────────

_SECRET_KEY = ""


async def run_secret_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /secret para gestionar variables secretas de la sesión.

    Examples:
        /secret
        /secret set <nombre> <valor>
        /secret get <nombre>
        /secret list
        /secret clear
    """
    global _SECRET_KEY  # noqa: PLW0603

    text = args.strip()
    secrets: dict[str, str] = getattr(session, "_secrets", None)
    if secrets is None:
        secrets = {}
        session._secrets = secrets

    if not text or text.lower() in ("list", "ls"):
        if not secrets:
            console.print("[dim]No hay secretos configurados.[/]")
            return
        console.print("\n[bold realm]᛭ Secretos configurados[/]")
        for name in sorted(secrets.keys()):
            console.print(f"  [bold cyan]{name}[/]: [dim]••••••••[/]")
        console.print()
        return

    parts = text.split(maxsplit=2)
    subcmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if subcmd in ("set", "add"):
        if len(parts) < 3:
            render_error("Uso: /secret set <nombre> <valor>")
            return
        name, value = parts[1], parts[2]
        secrets[name] = value
        console.print(f"[success]✓ Secreto configurado: {name}[/]")
        return

    if subcmd in ("get", "show"):
        if not rest:
            render_error("Uso: /secret get <nombre>")
            return
        value = secrets.get(rest)
        if value is None:
            render_error(f"Secreto no encontrado: {rest}")
            return
        console.print(f"[tool.name]{rest}[/]=[tool.result]{value}[/]")
        return

    if subcmd in ("clear", "reset"):
        secrets.clear()
        console.print("[success]✓ Secretos eliminados.[/]")
        return

    render_error("Uso: /secret [set <nombre> <valor>|get <nombre>|list|clear]")


# ── /cls (clear screen) ─────────────────────────────────────────────


async def run_clear_screen_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Limpia la pantalla del terminal sin tocar el historial.

    Equivalente a escribir ``cls`` (Windows) o ``clear`` (Unix) en el shell,
    pero dentro del REPL. No toca ``session.history`` ni ``session._file_edit_history``.

    Examples:
        /cls
    """
    import os
    import sys

    del session  # unused; kept for command-dispatcher signature parity.

    # ANSI clear-screen + cursor-home. Works on Windows 10+ Terminal, modern
    # conemu, Linux/macOS terminals, and the Git-Bash mintty used here.
    if sys.stdout.isatty():
        os.system("cls" if os.name == "nt" else "clear")
    else:
        # Non-TTY (piped output, tests, IDE captures): just emit enough
        # newlines to push the prior content off-screen. Better than
        # silently doing nothing.
        sys.stdout.write("\n" * 50)
        sys.stdout.flush()


# ── /doctor command ───────────────────────────────────────


async def run_doctor_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Run environment diagnostics (/doctor [--fix] [--json] [--quiet] [--deep])."""
    import json as _json

    tokens = args.split()
    do_fix = "--fix" in tokens
    do_json = "--json" in tokens
    do_quiet = "--quiet" in tokens or "-q" in tokens
    do_deep = "--deep" in tokens

    results = run_diagnostics(session)
    if do_deep:
        if not do_quiet:
            with console.status("[cyan]Running deep diagnostics\u2026[/cyan]", spinner="dots"):
                results.extend(_run_deep_checks(session))
        else:
            results.extend(_run_deep_checks(session))

    if do_json:
        # Machine-readable output for scripting
        # Use sys.stdout (not Rich console) to avoid markup interpretation
        import sys as _sys
        out = _json.dumps(results, indent=2, ensure_ascii=False)
        _sys.stdout.write(out + "\n")
        _sys.stdout.flush()
        return

    if not do_quiet:
        console.print("\n[bold realm]᛭ Doctor[/] — diagnosticando entorno…")

    ok_count = warn_count = error_count = 0
    for r in results:
        status = r["status"]
        check = r["check"]
        message = r["message"]
        if status == "ok":
            mark = "[success]✓[/success]"
            ok_count += 1
        elif status == "warn":
            mark = "[warn]![/warn]"
            warn_count += 1
        else:
            mark = "[error]✗[/error]"
            error_count += 1
        if not do_quiet:
            console.print(f"  {mark} [bold cyan]{check}[/bold cyan]: {message}")

    if not do_quiet:
        console.print(f"\n[info]Resumen:[/info] {ok_count} OK, {warn_count} warnings, {error_count} errors")

    if do_fix and (warn_count or error_count):
        if not do_quiet:
            console.print("\n[info]Aplicando fixes…[/info]")
        fixes = apply_fixes(results)
        for fix in fixes:
            if not do_quiet:
                console.print(f"  [success]✓[/success] {fix}")
    elif (warn_count or error_count) and not do_fix and not do_quiet:
        console.print("[dim]Pasá --fix para intentar reparar los issues detectables.[/dim]")
    if not do_quiet:
        console.print()


def _run_deep_checks(session: SessionRuntime) -> list[dict]:
    """Run extended diagnostic checks for --deep mode.

    Adds:
    - Disk free space in working directory
    - Active provider latency probe (no LLM call, just round-trip setup)
    - Network connectivity (DNS lookup of api.openai.com)
    - Number of tool calls recorded in current session
    - Language-server availability for every language supported by Lilith IDE
    """
    import shutil
    import socket
    import subprocess
    import time

    results: list[dict] = []

    # Language servers used by the IDE. This check is intentionally read-only:
    # it reports availability and never installs packages or edits config.
    try:
        from ..ide.lsp.languages import PREFERRED_SERVERS, language_server_command

        available: list[str] = []
        missing: list[str] = []
        for language in PREFERRED_SERVERS:
            command = language_server_command(language)
            if command:
                available.append(f"{language} ({Path(command[0]).name})")
            else:
                missing.append(language)

        if available:
            message = f"Disponibles: {', '.join(available)}"
            if missing:
                message += f"; faltan: {', '.join(missing)}"
            status = "ok"
        else:
            message = (
                "No se detectó ningún servidor; instalá uno de: "
                "pyright, rust-analyzer, typescript-language-server, gopls, "
                "vscode-json-language-server o yaml-language-server"
            )
            status = "warn"
        results.append({"check": "Language servers", "status": status, "message": message})
    except Exception as exc:
        results.append({
            "check": "Language servers",
            "status": "warn",
            "message": f"No se pudo verificar: {exc}",
        })

    # Disk free space
    try:
        usage = shutil.disk_usage(".")
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        used_pct = 100 * usage.used // usage.total
        if free_gb < 1:
            status = "error"
            msg = f"Solo {free_gb:.2f} GB libres de {total_gb:.1f} GB ({used_pct}% usado)"
        elif free_gb < 5:
            status = "warn"
            msg = f"{free_gb:.2f} GB libres de {total_gb:.1f} GB ({used_pct}% usado)"
        else:
            status = "ok"
            msg = f"{free_gb:.2f} GB libres de {total_gb:.1f} GB ({used_pct}% usado)"
        results.append({"check": "Disk space", "status": status, "message": msg})
    except Exception as exc:
        results.append({"check": "Disk space", "status": "warn", "message": f"No se pudo verificar: {exc}"})

    # Network probe
    try:
        start = time.time()
        socket.gethostbyname("api.openai.com")
        latency_ms = int((time.time() - start) * 1000)
        results.append({
            "check": "Network DNS",
            "status": "ok",
            "message": f"DNS resolved api.openai.com en {latency_ms}ms",
        })
    except socket.gaierror as exc:
        results.append({
            "check": "Network DNS",
            "status": "error",
            "message": f"No se pudo resolver api.openai.com: {exc}",
        })
    except Exception as exc:
        results.append({
            "check": "Network DNS",
            "status": "warn",
            "message": f"Error de red: {exc}",
        })

    # Git remote (if in a git repo)
    try:
        proc = subprocess.run(
            ["git", "remote", "-v"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            remote_count = len(proc.stdout.strip().split("\n"))
            results.append({
                "check": "Git remote",
                "status": "ok",
                "message": f"{remote_count} remote(s) configurado(s)",
            })
        else:
            results.append({
                "check": "Git remote",
                "status": "warn",
                "message": "No hay git remote configurado",
            })
    except (subprocess.TimeoutExpired, FileNotFoundError):
        results.append({
            "check": "Git remote",
            "status": "warn",
            "message": "git no disponible o no es un repo",
        })

    # Session tool call count
    try:
        tool_history = getattr(session, "_tool_call_history", None) or []
        count = len(tool_history)
        if count == 0:
            status = "warn"
            msg = "Ninguna herramienta llamada aún en esta sesión"
        elif count > 100:
            status = "warn"
            msg = f"{count} herramientas llamadas (considerá /compact)"
        else:
            status = "ok"
            msg = f"{count} herramientas llamadas en esta sesión"
        results.append({"check": "Session tools", "status": status, "message": msg})
    except Exception as exc:
        results.append({"check": "Session tools", "status": "warn", "message": f"Error: {exc}"})

    return results


# ── /calc command ───────────────────────────────────────────


# ── /cd command ───────────────────────────────────────────────────────────────


async def run_pwd_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Muestra el directorio de trabajo actual sin modificarlo."""
    if args.strip():
        render_error("Uso: /pwd")
        return

    console.print(f"[info]Directorio actual:[/] [bold cyan]{Path.cwd()}[/]")


async def run_cd_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Muestra o cambia el directorio de trabajo de la sesión.

    Examples:
        /cd              — mostrar el directorio actual
        /cd <ruta>       — cambiar al directorio indicado
        /cd ~            — ir al directorio personal
    """
    raw_path = args.strip()
    if not raw_path:
        console.print(
            f"[info]Directorio actual:[/] [bold cyan]{Path.cwd()}[/]",
            soft_wrap=True,
        )
        return

    # Las rutas con espacios se escriben entre comillas por costumbre de shell
    # (y porque otros comandos del REPL, como /random, las parsean con shlex).
    # Sin desenvolverlas la ruta se toma como relativa y en Windows falla
    # siempre: "D:\workspace\private-project" no existe dentro del cwd.
    if len(raw_path) >= 2 and raw_path[0] == raw_path[-1] and raw_path[0] in ('"', "'"):
        raw_path = raw_path[1:-1].strip()
        if not raw_path:
            render_error("Uso: /cd [ruta]")
            return

    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    target = target.resolve()

    if not target.exists():
        render_error(f"El directorio no existe: {target}")
        return
    if not target.is_dir():
        render_error(f"La ruta no es un directorio: {target}")
        return

    try:
        os.chdir(target)
    except OSError as exc:
        render_error(f"No pude cambiar de directorio: {exc}")
        return

    console.print(
        f"[success]✓ Directorio actual:[/] [bold cyan]{Path.cwd()}[/]",
        soft_wrap=True,
    )

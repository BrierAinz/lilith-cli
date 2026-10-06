"""Code-quality slash commands: /lint, /lint-fix, /format, /test and /security-review."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lilith_tools.coding_tools import FormatFileTool, RunLinterTool
from rich.markup import escape

from ..render import console, render_error
from ._shared import _print_tool_result

if TYPE_CHECKING:
    from ..session_runtime import SessionRuntime


def _lint_usage() -> str:
    """Devuelve la ayuda de /lint sin ofrecer un destino implícito peligroso."""
    return (
        "Uso: /lint <ruta-relativa> [--tool <linter>]\n"
        "  /lint lilith_cli/extra_commands.py\n"
        "  /lint lilith_cli/ --tool ruff check\n"
        "  /lint staged [--tool <linter>]"
    )


def _validate_lint_target(target: str) -> str | None:
    """Valida que *target* sea explícito, relativo y esté dentro del cwd."""
    candidate = Path(target).expanduser()
    if candidate.is_absolute() or target in (".", "./"):
        return "La ruta debe ser explícita y relativa; no se acepta '.' ni una ruta absoluta"

    try:
        cwd = Path.cwd().resolve()
        resolved = candidate.resolve()
        resolved.relative_to(cwd)
    except (OSError, RuntimeError, ValueError):
        return "La ruta debe permanecer dentro del repositorio de trabajo"

    if not resolved.exists():
        return f"Ruta no encontrada: {target}"
    return None


def _validate_lint_tool(linter: str | None) -> str | None:
    """Rechaza modos de linter que puedan modificar archivos."""
    if not linter:
        return None
    try:
        tool_tokens = [token.lower() for token in shlex.split(linter, posix=False)]
    except ValueError as exc:
        return f"Linter inválido: {exc}"

    unsafe_flags = {"--fix", "--unsafe-fixes", "--write", "--in-place", "-w"}
    if unsafe_flags.intersection(tool_tokens):
        return "/lint sólo permite modo reporte; quitá --fix/--unsafe-fixes/--write"

    formatter_names = {"black", "isort", "autoflake", "yapf", "prettier", "rustfmt", "gofmt"}
    if tool_tokens and tool_tokens[0] in formatter_names and not {"--check", "--diff"}.intersection(tool_tokens):
        return "Ese formatter requiere --check o --diff para ejecutarse sin modificar archivos"
    if "format" in tool_tokens and not {"--check", "--diff"}.intersection(tool_tokens):
        return "/lint sólo permite reportes; un modo format debe incluir --check o --diff"
    return None


async def run_lint_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /lint sobre una ruta explícita, siempre en modo reporte.

    No acepta una ruta vacía ni ``.``: el destino debe ser relativo y quedar
    dentro del directorio de trabajo. ``staged`` es la única forma abreviada y
    usa exclusivamente los archivos que Git devuelve como preparados.

    Examples:
        /lint lilith_cli/extra_commands.py
        /lint lilith_cli/ --tool ruff check
        /lint staged
    """
    text = (args or "").strip()
    if not text:
        render_error(_lint_usage())
        return

    try:
        tokens = shlex.split(text, posix=False)
    except ValueError as exc:
        render_error(f"Argumentos inválidos: {exc}")
        return

    # posix=False preserves Windows backslashes; remove only syntax quotes.
    tokens = [
        token[1:-1]
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "\\\"'"
        else token
        for token in tokens
    ]
    if not tokens:
        render_error(_lint_usage())
        return

    target = tokens.pop(0)
    linter: str | None = None
    if tokens:
        if len(tokens) >= 2 and tokens[0] == "--tool":
            linter = " ".join(tokens[1:]).strip()
        elif len(tokens) == 1 and tokens[0].startswith("--tool="):
            linter = tokens[0].split("=", 1)[1]
        else:
            render_error(_lint_usage())
            return

    tool_error = _validate_lint_tool(linter)
    if tool_error:
        render_error(tool_error)
        return

    if target.lower() == "staged":
        try:
            staged_proc = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError:
            render_error("git no está disponible en PATH")
            return
        if staged_proc.returncode != 0:
            render_error(f"No se pudo leer archivos staged: {staged_proc.stderr.strip()}")
            return
        staged_files = [path for path in staged_proc.stdout.splitlines() if path.strip()]
        if not staged_files:
            console.print("[dim]No hay archivos staged para lintar.[/dim]")
            return
        targets = staged_files
    else:
        target_error = _validate_lint_target(target)
        if target_error:
            render_error(target_error)
            return
        targets = [target]

    for lint_target in targets:
        target_error = _validate_lint_target(lint_target)
        if target_error:
            render_error(target_error)
            return
        kwargs = {"path": lint_target}
        if linter:
            kwargs["linter"] = linter
        result = RunLinterTool().execute(**kwargs)
        command_str = (result.data or {}).get("command", "") if result.success else (linter or "auto")
        console.print("[bold realm]\\u16ed Lint:[/] [dim]" + str(command_str) + "[/dim]")
        _print_tool_result(result)

_TEST_LAST_FAILED_PATH: Path = Path.home() / ".yggdrasil" / "test_last_failed.json"


def _test_last_failed_path() -> Path:
    return _TEST_LAST_FAILED_PATH


def _set_test_last_failed_path(path: Path) -> None:
    global _TEST_LAST_FAILED_PATH
    _TEST_LAST_FAILED_PATH = path


# ── /test (subprocess pytest runner) ────────────────────────────────────────
# Runs pytest in the working directory (the project /cd points at). Pure
# subprocess wrapper — does NOT mutate the repo. Adds a -k keyword filter and
# parses the pytest summary line into a small dict so the REPL can render a
# clean overview.
_PYTEST_SUMMARY_RE = re.compile(
    r"(?P<passed>\d+)\s+passed|"
    r"(?P<failed>\d+)\s+failed|"
    r"(?P<error>\d+)\s+error",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(r"in\s+(?P<seconds>[0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE)


def _test_repo_root() -> Path:
    """Devuelve la raíz desde la que se ejecuta pytest: el directorio de trabajo."""
    return Path.cwd().resolve()


def _default_test_target() -> str:
    """``tests`` cuando el proyecto lo tiene; si no, pytest descubre desde ``.``."""
    return "tests" if (_test_repo_root() / "tests").is_dir() else "."


def _test_python(cwd: Path) -> str:
    """El intérprete del ``.venv`` del proyecto, o el que ejecuta Lilith."""
    if sys.platform == "win32":
        venv_py = cwd / ".venv" / "Scripts" / "python.exe"
    else:
        venv_py = cwd / ".venv" / "bin" / "python"
    return str(venv_py) if venv_py.is_file() else sys.executable


def _validate_test_target(target: str) -> str | None:
    """Valida que el objetivo de pytest permanezca dentro del directorio de trabajo.

    El objetivo puede incluir un node-id (``archivo.py::test_nombre``), pero
    nunca se ejecutan rutas absolutas o traversal fuera de la raíz del repo.
    """
    path_part = target.split("::", 1)[0].strip()
    if not path_part:
        return "El objetivo de /test no puede estar vacío."

    try:
        candidate = Path(path_part).expanduser()
        resolved = (candidate if candidate.is_absolute() else _test_repo_root() / candidate).resolve()
        resolved.relative_to(_test_repo_root())
    except (OSError, RuntimeError, ValueError):
        return "La ruta de /test está fuera del repositorio o no es válida."
    return None


def _parse_pytest_summary(text: str) -> dict[str, Any]:
    """Parse a pytest output blob into a small summary dict.

    Recognises both the canonical ``475 passed in 28.13s`` line and
    per-segment lines like ``3 failed, 1 passed``. Always returns a dict
    with the four numeric keys plus ``duration`` (float seconds) and
    ``last_failure`` (str | None — last ``FAILED`` line if any).

    Args:
        text: Captured stdout/stderr from a pytest subprocess run.

    Returns:
        ``{"passed": int, "failed": int, "error": int, "duration": float,
           "last_failure": str | None}``.
    """
    passed = failed = error = 0
    duration = 0.0
    last_failure: str | None = None

    for raw in (text or "").splitlines():
        line = raw.strip()
        # last failure line is more useful than the count itself
        if line.startswith("FAILED "):
            last_failure = line
        for match in _PYTEST_SUMMARY_RE.finditer(line):
            kind = match.lastgroup
            if not kind:
                continue
            value = int(match.group(kind))
            if kind == "passed":
                passed += value
            elif kind == "failed":
                failed += value
            elif kind == "error":
                error += value
        dur = _DURATION_RE.search(line)
        if dur and "=" not in line.split("in", 1)[0][-1:]:
            # only take the duration when the line looks like a summary,
            # not e.g. ``passed in 0.01s = setup`` (defensive)
            try:
                duration = float(dur.group("seconds"))
            except ValueError:
                pass

    return {
        "passed": passed,
        "failed": failed,
        "error": error,
        "duration": duration,
        "last_failure": last_failure,
    }


def _render_test_summary(summary: dict[str, Any], returncode: int) -> str:
    """Format a parsed pytest summary into a one-line Spanish status."""
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    error = summary.get("error", 0)
    duration = summary.get("duration", 0.0)
    bits: list[str] = []
    if passed:
        bits.append(f"[success]{passed} passed[/success]")
    if failed:
        bits.append(f"[error]{failed} failed[/error]")
    if error:
        bits.append(f"[error]{error} error[/error]")
    if not bits:
        bits.append("[dim]sin resultados[/dim]")
    bits.append(f"[dim]{duration:.2f}s[/dim]")
    if returncode != 0:
        bits.append(f"[warning]exit={returncode}[/warning]")
    line = " · ".join(bits)
    if summary.get("last_failure"):
        line += f"\n  [error]{summary['last_failure']}[/error]"
    return line


def _run_pytest_subprocess(
    target: str,
    *,
    keyword: str | None = None,
    extra_args: list[str] | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Run pytest via subprocess and return a parsed summary dict.

    Args:
        target: Path or node-id passed to pytest (e.g. ``tests/`` or
            ``tests/test_plan.py::test_x``).
        keyword: Optional ``-k`` expression. ``None`` means no filter.
        extra_args: Extra pytest flags (e.g. ``["--maxfail=1"]``).
        cwd: Working directory for the subprocess. ``None`` uses the
            current working directory.

    Returns:
        ``{"passed": int, "failed": int, "error": int, "duration": float,
           "last_failure": str | None, "returncode": int, "command": list[str]}``.
        ``error`` is pytest's error count; when pytest could not run at all
        the dict also carries a ``run_error`` message.
    """
    if cwd is None:
        cwd = _test_repo_root()

    target_error = _validate_test_target(target)
    if target_error:
        return {
            "passed": 0,
            "failed": 0,
            "error": 0,
            "duration": 0.0,
            "last_failure": None,
            "returncode": -1,
            "command": [],
            "run_error": target_error,
        }

    cmd: list[str] = [_test_python(cwd), "-m", "pytest", target, "-q", "--no-header", "--tb=line"]
    if keyword:
        cmd.extend(["-k", keyword])
    if extra_args:
        cmd.extend(extra_args)

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except FileNotFoundError as exc:
        return {
            "passed": 0,
            "failed": 0,
            "error": 0,
            "duration": time.monotonic() - start,
            "last_failure": None,
            "returncode": -1,
            "command": cmd,
            "run_error": f"pytest no disponible: {exc}",
        }
    except subprocess.TimeoutExpired:
        return {
            "passed": 0,
            "failed": 0,
            "error": 0,
            "duration": time.monotonic() - start,
            "last_failure": None,
            "returncode": -1,
            "command": cmd,
            "run_error": "pytest excedió el timeout (600s)",
        }

    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    summary = _parse_pytest_summary(output)
    # If pytest didn't print a duration, fall back to our own clock.
    if not summary.get("duration"):
        summary["duration"] = time.monotonic() - start
    summary["returncode"] = proc.returncode
    summary["command"] = cmd
    return summary


def _render_test_run_error(message: str) -> None:
    """Report that pytest could not run (as opposed to failing tests)."""
    console.print(f"[error]{escape(message)}[/error]")
    console.print("[dim]tip: verifica que .venv exista y pytest esté instalado[/dim]")
    console.print()


def _render_test_usage() -> None:
    """Muestra la ayuda de /test."""
    console.print("\n[bold realm]᛭ Uso de /test[/]")
    console.print(
        "  [cyan]/test[/]                              — corre la suite por defecto "
        "(tests/ del proyecto, o descubrimiento desde .)"
    )
    console.print(
        "  [cyan]/test <ruta>[/]                       — corre pytest sobre la ruta dada"
    )
    console.print(
        "  [cyan]/test -k <expresión>[/]               — filtra por nombre de test"
    )
    console.print(
        "  [cyan]/test <ruta> -k <expresión>[/]        — ruta + filtro combinados"
    )
    console.print(
        "  [cyan]/test last[/]                         — re-corre los tests que fallaron antes"
    )
    console.print("  [cyan]/test --help[/]                       — muestra esta ayuda")
    console.print()


async def run_test_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /test como wrapper directo de pytest.

    Descripción:
        Lanza pytest en un subproceso desde el directorio de trabajo con el
        intérprete del ``.venv`` del proyecto (o el de Lilith si no hay uno).
        Por defecto corre ``tests/`` si existe. Imprime un resumen corto:
        passed/failed/error, duración y la última línea ``FAILED`` si hubo
        fallos. Nunca modifica archivos del repo.

    Uso:
        /test
        /test <ruta>
        /test -k <expresión>
        /test <ruta> -k <expresión>
        /test last
        /test --help

    Ejemplos:
        /test
        /test tests/test_plan.py
        /test -k hello
        /test tests/ -k smoke
        /test last
    """
    text = (args or "").strip()

    if text in ("--help", "-h", "help"):
        _render_test_usage()
        return

    # /test last  — re-corre los tests fallidos previos
    if text == "last":
        last_file = _test_last_failed_path()
        if not last_file.exists():
            console.print("[dim]No hay tests fallidos previos.[/dim]")
            return
        try:
            import json as _json

            data = _json.loads(last_file.read_text(encoding="utf-8"))
            failed = data.get("failed", [])
        except Exception as exc:
            console.print(f"[error]Error leyendo historial: {exc}[/error]")
            return
        if not failed:
            console.print("[dim]No hay tests fallidos previos.[/dim]")
            return
        console.print(f"[info]Re-corriendo {len(failed)} tests fallidos...[/info]")
        # convert last-failed list into a single -k expression
        pattern = " or ".join(failed)
        summary = _run_pytest_subprocess(
            _default_test_target(), keyword=pattern
        )
        if summary.get("run_error"):
            _render_test_run_error(summary["run_error"])
            return
        console.print(_render_test_summary(summary, summary["returncode"]))
        console.print()
        return

    # Split args into a target path and an optional -k keyword.
    target: str = _default_test_target()
    keyword: str | None = None
    tokens = text.split()
    i = 0
    path_tokens: list[str] = []
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-k" and i + 1 < len(tokens):
            keyword = tokens[i + 1]
            i += 2
            continue
        if tok.startswith("-k="):
            keyword = tok.split("=", 1)[1]
            i += 1
            continue
        path_tokens.append(tok)
        i += 1
    if path_tokens:
        target = " ".join(path_tokens)

    target_error = _validate_test_target(target)
    if target_error:
        console.print(f"[error]{target_error}[/error]")
        console.print()
        return

    summary = _run_pytest_subprocess(target, keyword=keyword)
    if summary.get("run_error"):
        _render_test_run_error(summary["run_error"])
        return
    console.print(_render_test_summary(summary, summary["returncode"]))
    console.print()

# ── /lint-fix command ──────────────────────


async def run_lint_fix_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Audita lint sin modificar archivos (/lint-fix <ruta>).

    El nombre histórico se conserva para no romper scripts, pero este comando
    es deliberadamente de solo lectura: nunca ejecuta ``--fix`` ni un formatter
    que escriba archivos. Requiere una ruta explícita, relativa al directorio
    actual y existente, para evitar auditar por accidente un árbol completo.
    """
    import shlex
    import shutil
    import subprocess

    text = (args or "").strip()
    if not text:
        render_error("Uso: /lint-fix <ruta-relativa> — modo reporte; no modifica archivos")
        return

    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        render_error(f"Argumentos inválidos: {exc}")
        return

    if len(tokens) != 1:
        render_error("Uso: /lint-fix <ruta-relativa> — escribí una sola ruta explícita")
        return

    target = tokens[0]
    candidate = Path(target).expanduser()
    if candidate.is_absolute() or target in (".", "./"):
        render_error("La ruta debe ser explícita y relativa; no se acepta '.' ni una ruta absoluta")
        return

    try:
        cwd = Path.cwd().resolve()
        resolved = candidate.resolve()
        resolved.relative_to(cwd)
    except (OSError, RuntimeError, ValueError):
        render_error("La ruta debe permanecer dentro del repositorio de trabajo")
        return

    if not resolved.exists():
        render_error(f"Ruta no encontrada: {target}")
        return

    # Ruff is preferred because ``check`` is an audit operation. Do not add
    # ``--fix`` here: library re-exports and public APIs must never be edited
    # implicitly by a slash command.
    ruff = shutil.which("ruff")
    if ruff:
        command = [ruff, "check", target]
        console.print(
            "[info]Auditoría:[/info] [bold cyan]"
            + " ".join(command)
            + "[/bold cyan] [dim](solo reporte; sin cambios)[/dim]"
        )
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            render_error("ruff agotó el tiempo después de 60s")
            return
        if proc.stdout:
            console.print(proc.stdout, markup=False)
        if proc.stderr:
            console.print(proc.stderr, markup=False)
        label = "sin problemas" if proc.returncode == 0 else f"exit {proc.returncode}"
        console.print(f"[success]✓ ruff: reporte completado ({label}); no se modificaron archivos[/success]")
        return

    # Black must use --check; invoking it bare would rewrite files.
    black = shutil.which("black")
    if black:
        command = [black, "--check", target]
        console.print(
            "[info]Auditoría:[/info] [bold cyan]"
            + " ".join(command)
            + "[/bold cyan] [dim](solo reporte; sin cambios)[/dim]"
        )
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            render_error("black agotó el tiempo después de 60s")
            return
        if proc.stdout:
            console.print(proc.stdout, markup=False)
        if proc.stderr:
            console.print(proc.stderr, markup=False)
        label = "sin problemas" if proc.returncode == 0 else f"exit {proc.returncode}"
        console.print(f"[success]✓ black: reporte completado ({label}); no se modificaron archivos[/success]")
        return

    render_error("Ni ruff ni black están instalados; la auditoría no ejecutó ningún cambio")


# ── /format command ────────────────────────────────────────────────────


async def run_format_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Formatea un archivo con un formatter externo (/format <ruta> [--check]).

    El comando sigue la misma política de seguridad que ``/lint-fix``:

    * Requiere una ruta **explícita y relativa al directorio de trabajo**;
      no acepta ``.``, ``./``, ni rutas absolutas. Esto evita que un
      slash command recorra por accidente un árbol completo (incluido
      el stdlib, los site-packages o cualquier caché fuera del repo).
    * La ruta debe permanecer **dentro del repositorio** de trabajo.
    * En modo ``--check`` (default cuando el usuario no confirma) sólo se
      reporta: nunca se escribe en disco. Equivale a ``black --check``.
    * En modo de aplicación el comando exige ``session.config.confirm_write``
      encendido (la misma política que ya protege ``file_write`` y
      ``file_edit``), y le pide al usuario que confirme antes de tocar
      el archivo. El respaldo con ``UndoManager`` se hace dentro de
      ``FormatFileTool``; aquí sólo nos aseguramos de que la intención
      del usuario sea explícita.

    Examples:
        /format src/foo.py --check
        /format src/foo.py                  (pide confirmación)
        /format src/foo.py --yes            (omite la confirmación)
    """
    import shlex
    import subprocess

    raw = (args or "").strip()
    if not raw:
        render_error(
            "Uso: /format <ruta-relativa> [--check] [--yes]\n"
            "  --check   Sólo reporta; no modifica archivos (default seguro).\n"
            "  --yes     Omite la confirmación interactiva (sigue creando backup)."
        )
        return

    # Parse the argument list. We avoid a bare ``shlex.split`` for paths that
    # look like a single Windows path (``C:\foo\bar.py``) because the default
    # shlex tokenizer strips backslashes and would corrupt the path. If the
    # input has no spaces we treat the whole string as the path; otherwise we
    # use shlex so that quoted paths and flag tokens round-trip correctly.
    if " " in raw or "\t" in raw:
        try:
            tokens = shlex.split(raw)
        except ValueError as exc:
            render_error(f"Argumentos inválidos: {exc}")
            return
    else:
        tokens = [raw]

    target: str | None = None
    check_only = False
    assume_yes = False
    for token in tokens:
        if token == "--check":
            check_only = True
        elif token in ("--yes", "-y"):
            assume_yes = True
        elif token in ("--help", "-h", "help", "?"):
            console.print(
                "[info]/format[/info] — formatea un archivo con un formatter externo.\n"
                "  /format <ruta> [--check] [--yes]\n"
                "  /format <ruta>                pide confirmación y aplica\n"
                "  /format <ruta> --check        sólo reporta; no modifica"
            )
            return
        else:
            if target is not None:
                render_error("Sólo se acepta una ruta por invocación")
                return
            target = token

    if target is None:
        render_error("Falta la ruta del archivo a formatear")
        return

    candidate = Path(target).expanduser()
    if candidate.is_absolute() or target in (".", "./"):
        render_error(
            "La ruta debe ser explícita y relativa; no se acepta '.' ni una ruta absoluta"
        )
        return

    try:
        cwd = Path.cwd().resolve()
        resolved = candidate.resolve()
        resolved.relative_to(cwd)
    except (OSError, RuntimeError, ValueError):
        render_error("La ruta debe permanecer dentro del repositorio de trabajo")
        return

    if not resolved.exists():
        render_error(f"Ruta no encontrada: {target}")
        return
    if not resolved.is_file():
        render_error(f"La ruta no es un archivo regular: {target}")
        return

    # --check es el default seguro si el usuario no es explícito (consistente
    # con /lint-fix, que también es report-only por default).
    if not check_only and not assume_yes:
        confirm = getattr(getattr(session, "config", None), "confirm_write", True)
        if not confirm:
            render_error(
                "confirm_write está desactivado en la sesión. "
                "Usá `/format <ruta> --yes` o activá confirm_write con `/confirm on`."
            )
            return
        console.print(
            f"[warning]Vas a formatear:[/warning] [bold cyan]{resolved}[/bold cyan]\n"
            "[dim]Se creará un backup automático (UndoManager).[/dim]\n"
            "[dim]Continuar? [y/N]: [/dim]",
            end="",
        )
        try:
            answer = input()
        except (EOFError, KeyboardInterrupt):
            console.print()
            render_error("Cancelado por el usuario")
            return
        if answer.strip().lower() not in {"y", "yes", "s", "si"}:
            render_error("Cancelado por el usuario")
            return

    # --check nunca pasa por FormatFileTool: corre la variante de solo
    # revisión del formatter detectado (``black --check``, ``prettier
    # --check``, ``gofmt -l``...). Si el formatter no tiene una, no se
    # ejecuta nada en lugar de caer en la invocación que escribe.
    if check_only:
        from lilith_tools.coding_tools import (
            _detect_formatter,
            _formatter_check_command,
        )

        detected = _detect_formatter(str(resolved), None)
        if detected is None:
            render_error(
                "No se pudo detectar un formatter para el archivo "
                "(¿está instalado ruff/black/prettier?)."
            )
            return

        check = _formatter_check_command(detected)
        if check is None:
            render_error(
                f"El formatter detectado ({detected}) no tiene un modo de solo "
                "revisión conocido; no se ejecutó nada."
            )
            return
        check_argv, pending_from_stdout = check
        argv = [*check_argv, str(resolved)]
        full_cmd = shlex.join(argv)
        try:
            proc = subprocess.run(
                argv,
                cwd=str(resolved.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            render_error("El formatter agotó el tiempo después de 60s")
            return
        except FileNotFoundError:
            render_error(f"Formatter no encontrado en PATH: {check_argv[0]}")
            return

        console.print(
            "[info]Auditoría:[/info] [bold cyan]"
            + escape(full_cmd)
            + "[/bold cyan] [dim](solo reporte; sin cambios)[/dim]"
        )
        if proc.stdout:
            console.print(proc.stdout, markup=False)
        if proc.stderr:
            console.print(proc.stderr, markup=False)
        pending = proc.returncode != 0 or (pending_from_stdout and bool(proc.stdout.strip()))
        label = (
            f"cambios pendientes, exit {proc.returncode}" if pending else "sin cambios pendientes"
        )
        console.print(
            f"[success]✓ format --check: reporte completado ({label}); no se modificaron archivos[/success]"
        )
        return

    # Modo aplicación: delegamos en la herramienta. FormatFileTool ya hace
    # UndoManager.backup() antes de cualquier mutación, así que un /undo
    # posterior puede revertir el cambio.
    result = FormatFileTool().execute(path=str(resolved), timeout=60)
    if not result.success:
        render_error(result.error or "No se pudo formatear el archivo")
        return

    data = result.data or {}
    cmd = data.get("command", "")
    formatted = data.get("formatted", False)
    console.print(
        "[success]✓ Archivo formateado:[/success] [bold cyan]"
        + str(resolved)
        + "[/bold cyan]"
    )
    if cmd:
        console.print(f"[dim]comando: {cmd}[/dim]")
    if formatted:
        console.print(
            "[dim]El archivo cambió. Usá `/undo pop` si querés revertir el cambio.[/dim]"
        )
    else:
        console.print("[dim]Sin cambios necesarios (formatter no reportó diferencias).[/dim]")
    stderr = data.get("stderr", "")
    if stderr:
        console.print(
            f"[warning]stderr:[/warning] {stderr}", markup=False
        )


# ── /security-review command ────────────────────────────────────────

# Pattern set: each entry is (severity, label, regex, description). Kept
# deterministic so the command is testable without an LLM and the findings
# are reproducible across runs. Severities follow the same convention as
# semgrep: HIGH = exploitable today, MEDIUM = risky pattern, LOW = smell.
#
# The patterns intentionally target Python, JavaScript/TypeScript, generic
# shell, and JSON/YAML since most Lilith projects mix those. Adding a new
# pattern is a one-line change; keep the regex anchored enough to avoid
# noise but loose enough to catch the common cases.
_SECURITY_PATTERNS: list[tuple[str, str, str, str]] = [
    # ── HIGH: exploitable today ───────────────────────────────────
    (
        "HIGH",
        "secret-asignado",
        r"""(?ix)
        (?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|
           private[_-]?key|password|passwd|pwd)\s*[=:]\s*['\"][^'"\s]{8,}['\"]""",
        "Secreto hardcodeado en el código (literal entre comillas).",
    ),
    (
        "HIGH",
        "eval-input",
        r"\beval\s*\(\s*(?:input|request\.|argv|sys\.argv)",
        "eval() sobre entrada del usuario: ejecución arbitraria.",
    ),
    (
        "HIGH",
        "exec-input",
        r"\bexec\s*\(\s*(?:input|request\.|argv|sys\.argv)",
        "exec() sobre entrada del usuario: ejecución arbitraria.",
    ),
    (
        "HIGH",
        "pickle-load",
        r"\bpickle\.loads?\s*\(\s*(?![\"']b?[\"'])[^)]*\)",
        "pickle.load(s) sobre datos no confiables: RCE.",
    ),
    (
        "HIGH",
        "yaml-load",
        r"\byaml\.load\s*\((?![^)]*Loader\s*=\s*yaml\.SafeLoader)[^)]*\)",
        "yaml.load sin SafeLoader: RCE via YAML malformado.",
    ),
    (
        "HIGH",
        "shell-true",
        r"\bsubprocess\.[A-Za-z_]+\s*\([^)]*shell\s*=\s*True",
        "subprocess con shell=True sobre datos del usuario.",
    ),
    (
        "HIGH",
        "md5-para-seguridad",
        r"\bhashlib\.(md5|sha1)\s*\(",
        "MD5/SHA1 para hashing de seguridad: usar sha256/blake2.",
    ),
    # ── MEDIUM: risky pattern, contexto-dependiente ────────────────
    (
        "MEDIUM",
        "sql-string-format",
        r"""(?x)
        (?:execute|cursor\.execute|query)\s*\(\s*
            (?:f['\"][^'\"]*\{|['\"][^'\"]*%\s*\(|['\"][^'\"]*\+[^)]*['\"])""",
        "Query SQL construída por concatenación/formato: riesgo de SQLi.",
    ),
    (
        "MEDIUM",
        "weak-random",
        r"\brandom\.(random|randint|choice|shuffle)\s*\(",
        "random.* en contexto de seguridad: usar secrets.token_*.",
    ),
    (
        "MEDIUM",
        "requests-verify-false",
        r"requests\.[a-z]+\s*\([^)]*verify\s*=\s*False",
        "TLS verification desactivada en requests.",
    ),
    (
        "MEDIUM",
        "debug-true",
        r"(?i)\bdebug\s*=\s*True\b",
        "Modo debug activado: suele exponer trazas y credenciales.",
    ),
    (
        "MEDIUM",
        "todo-secret",
        r"(?i)\b(?:TODO|FIXME|XXX)\b[^\n]{0,40}\b(?:secret|password|key|token)\b",
        "TODO/FIXME con referencia a secretos: revisar antes de release.",
    ),
    # ── LOW: smell que conviene anotar ────────────────────────────
    (
        "LOW",
        "print-stacktrace",
        r"\bprint\s*\(\s*(?:traceback|format_exc|exc_info)\b",
        "Imprimir traceback en producción: leak de paths internos.",
    ),
    (
        "LOW",
        "binding-all-interfaces",
        r"['\"]0\.0\.0\.0['\"]",
        "Bind a 0.0.0.0: expone el servicio en todas las interfaces.",
    ),
]

# Directories that should be excluded from any walk — these never
# contain project source and would only slow the scan + produce false
# positives (migrations, generated code, vendored libraries).
_SECURITY_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "dist",
        "build",
        ".tox",
        ".nox",
        "target",  # rust
        "vendor",
        "Pods",
    }
)

# Hard cap on findings per pattern so a single bad file does not flood
# the report. Keeps the output focused and the JSON payload small.
_MAX_FINDINGS_PER_PATTERN = 25
_MAX_FILE_BYTES = 1_000_000  # skip files > 1 MB (generated, vendored)


def _security_scan_file(path: Path) -> list[dict]:
    """Return findings (list of dicts) for *path*. Empty list on skip."""
    try:
        if not path.is_file():
            return []
        size = path.stat().st_size
        if size == 0 or size > _MAX_FILE_BYTES:
            return []
        # Cheap binary-file detection: NUL byte in first 4 KB.
        with path.open("rb") as f:
            head = f.read(4096)
        if b"\x00" in head:
            return []
        try:
            text = head.decode("utf-8", errors="strict") + path.read_text(
                encoding="utf-8", errors="replace"
            )[len(head):]
        except (UnicodeDecodeError, OSError):
            return []
    except OSError:
        return []

    findings: list[dict] = []
    counters: dict[str, int] = {}
    for severity, label, pattern, description in _SECURITY_PATTERNS:
        try:
            regex = re.compile(pattern)
        except re.error:
            continue
        hits = list(regex.finditer(text))
        if not hits:
            continue
        limit = _MAX_FINDINGS_PER_PATTERN
        kept = 0
        for m in hits:
            if kept >= limit:
                break
            # Compute 1-based line number by counting newlines in the slice
            # up to the match start. fast enough for files ≤ 1 MB.
            line_no = text.count("\n", 0, m.start()) + 1
            snippet = m.group(0)
            if len(snippet) > 80:
                snippet = snippet[:77] + "…"
            findings.append(
                {
                    "severity": severity,
                    "label": label,
                    "file": str(path),
                    "line": line_no,
                    "snippet": snippet,
                    "description": description,
                }
            )
            kept += 1
        counters[label] = kept
    return findings


def _security_walk(root: Path) -> list[dict]:
    """Walk *root* and return all findings across scanned files."""
    results: list[dict] = []
    if root.is_file():
        results.extend(_security_scan_file(root))
        return results
    if not root.exists():
        return results
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in-place so os.walk skips these subtrees.
        dirnames[:] = [d for d in dirnames if d not in _SECURITY_SKIP_DIRS]
        for fname in filenames:
            results.extend(_security_scan_file(Path(dirpath) / fname))
    return results


_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
_SEVERITY_STYLE = {
    "HIGH": "bold red",
    "MEDIUM": "bold yellow",
    "LOW": "dim",
}


async def run_security_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /security-review para auditar patrones de seguridad.

    Recorre un árbol de directorios aplicando un conjunto de regex
    deterministas y reporta los hallazgos agrupados por severidad.
    No usa el LLM — los resultados son reproducibles entre ejecuciones.

    Examples:
        /security-review
        /security-review src/
        /security-review path/al/archivo.py
        /security-review . --json
        /security-review src --max 50
    """
    text = args.strip()
    # Manual flag parsing. We use a permissive approach so paths with
    # backslashes (Windows) and embedded spaces survive: if the input
    # parses cleanly with shlex we use it; otherwise we fall back to
    # whitespace splitting, which works for space-free paths.
    output_json = False
    max_findings: int | None = None
    positional: list[str] = []
    try:
        tokens = shlex.split(text, posix=False) if text else []
    except ValueError:
        tokens = text.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("--json", "-j"):
            output_json = True
            i += 1
            continue
        if tok == "--max" and i + 1 < len(tokens):
            try:
                max_findings = max(1, int(tokens[i + 1]))
            except ValueError:
                render_error("--max requiere un entero positivo")
                return
            i += 2
            continue
        if tok.startswith("--max="):
            try:
                max_findings = max(1, int(tok.split("=", 1)[1]))
            except ValueError:
                render_error("--max requiere un entero positivo")
                return
            i += 1
            continue
        positional.append(tok)
        i += 1

    target_arg = positional[0] if positional else "."
    target = Path(target_arg).expanduser()
    if not target.exists():
        render_error(f"Ruta no encontrada: {target}")
        return

    findings = _security_walk(target.resolve())

    # Apply --max cap across the whole report (after severity ordering
    # so HIGH findings are not silently dropped first).
    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f["severity"], 99), f["file"], f["line"]))
    if max_findings is not None and len(findings) > max_findings:
        findings = findings[:max_findings]

    if output_json:
        import sys as _sys

        by_sev = {
            sev: sum(1 for f in findings if f["severity"] == sev)
            for sev in ("HIGH", "MEDIUM", "LOW")
        }
        # scanned_files is best-effort: we only count files that produced
        # at least one finding (avoids a second walk over the whole tree
        # just to count empty files).
        summary = {
            "target": str(target),
            "scanned_files": len({f["file"] for f in findings}),
            "total": len(findings),
            "by_severity": by_sev,
            "findings": findings,
        }
        _sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
        _sys.stdout.flush()
        return

    if not findings:
        console.print(
            f"[success]✓ Sin hallazgos[/] — {target} pasó las "
            f"{len(_SECURITY_PATTERNS)} reglas de seguridad."
        )
        console.print()
        return

    # Group by severity for the rendered report.
    grouped: dict[str, list[dict]] = {"HIGH": [], "MEDIUM": [], "LOW": []}
    for f in findings:
        grouped.setdefault(f["severity"], []).append(f)

    console.print(
        f"\n[bold realm]᛭ Auditoría de seguridad[/] — "
        f"[info]objetivo:[/] [bold cyan]{target}[/]"
    )
    for sev in ("HIGH", "MEDIUM", "LOW"):
        n = len(grouped[sev])
        if n == 0:
            continue
        console.print(
            f"  [{_SEVERITY_STYLE[sev]}]{sev}[/]: {n} hallazgo(s)"
        )
    console.print(f"  [dim]Total: {len(findings)} · Reglas evaluadas: {len(_SECURITY_PATTERNS)}[/]")
    console.print()

    from rich.table import Table

    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
    )
    table.add_column("Sev", width=8, style="bold")
    table.add_column("Regla", style="tool.name", width=24)
    table.add_column("Archivo:línea", style="white", no_wrap=False)
    table.add_column("Snippet", style="dim", max_width=60)

    for sev in ("HIGH", "MEDIUM", "LOW"):
        for f in grouped[sev]:
            rel = f["file"]
            # Make long paths friendlier in narrow terminals.
            if len(rel) > 80:
                rel = "…" + rel[-78:]
            table.add_row(
                f"[{_SEVERITY_STYLE[sev]}]{sev}[/]",
                f["label"],
                f"{rel}:{f['line']}",
                f["snippet"],
            )

    console.print(table)
    console.print(
        "\n[dim]Estas reglas son heurísticas — revisá cada hallazgo antes de "
        "tomar acción. Para un análisis profundo usá un SAST dedicado "
        "(bandit, semgrep, codeql).[/dim]"
    )
    console.print()

"""Git slash commands: /git, /diff-*, /review, /changelog, /release, /pr and /apply."""

from __future__ import annotations

import argparse
import asyncio
import re
import shlex
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lilith_tools.base import ToolResult
from lilith_tools.git_tools import GitOperationTool

from ..render import console, render_error
from ._shared import _print_tool_result

if TYPE_CHECKING:
    from ..session_runtime import SessionRuntime


async def run_git_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /git <subcomando> [args] usando GitOperationTool.

    Examples:
        /git status
        /git log --oneline -5
    """
    text = args.strip()
    if not text:
        render_error("Uso: /git <subcomando> [args] — por ejemplo /git status")
        return

    parts = text.split(maxsplit=1)
    op = parts[0]
    git_args = parts[1] if len(parts) > 1 else ""

    tool = GitOperationTool()
    result = tool.execute(op=op, args=git_args)
    _print_tool_result(result)


async def run_diff_staged_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /diff-staged para mostrar cambios preparados en git.

    Examples:
        /diff-staged              — patch completo
        /diff-staged stats        — tabla con archivos + +/- counts
        /diff-staged <archivo>    — diff del archivo preparado
    """
    text = args.strip()

    # `--numstat` is the machine-friendly form of `--stat`: one row per
    # file with added/removed counts (or '-' for binary). We use it for
    # both the default and explicit `stats` rendering — then parse it
    # into a Rich Table so the user sees aligned columns instead of the
    # raw `git diff --stat` ASCII output.
    use_stats = text.lower() == "stats"
    if use_stats:
        cmd = ["git", "diff", "--cached", "--numstat"]
    elif text:
        cmd = ["git", "diff", "--cached", "--", text]
    else:
        # Default still runs full diff (preserves the historical behavior);
        # `stats` is the explicit fast path.
        cmd = ["git", "diff", "--cached"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except Exception as exc:
        render_error(f"Error ejecutando git diff --cached: {exc}")
        return

    if result.returncode != 0:
        error = result.stderr.strip() or "Error desconocido ejecutando git diff --cached"
        render_error(error)
        return

    output = result.stdout.strip()
    if not output:
        console.print("[dim]No hay cambios preparados.[/]")
        return

    console.print(
        "\n[bold realm]᛭ Cambios preparadas[/]"
        + (f" — {text}" if text and not use_stats else "")
    )

    if use_stats:
        _render_diff_staged_stats(output)
    else:
        console.print(output, markup=False, highlight=False)
    console.print()


def _render_diff_staged_stats(numstat_output: str) -> None:
    """Render the `--numstat` output as a Rich table with file, +, -.

    `git diff --cached --numstat` returns rows like ``12  3 src/foo.py``
    or ``-\t-\timg.png`` for binary files. We split on tabs, accumulate
    totals, and render a table that survives wrapping in an 80-column
    terminal better than the raw git output.
    """
    from rich.table import Table

    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
    )
    table.add_column("Archivo", style="tool.name")
    table.add_column("+", justify="right", style="green", width=6)
    table.add_column("-", justify="right", style="red", width=6)

    total_add = 0
    total_del = 0
    rows = 0
    for line in numstat_output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_raw, removed_raw, path = parts[0], parts[1], "\t".join(parts[2:])
        # Binary files report '-' for both counts.
        if added_raw == "-" and removed_raw == "-":
            added = "-"
            removed = "-"
        else:
            try:
                added = int(added_raw)
                removed = int(removed_raw)
                total_add += added
                total_del += removed
            except ValueError:
                added = added_raw
                removed = removed_raw
        rows += 1
        table.add_row(path, str(added), str(removed))

    console.print(table)
    if rows:
        console.print(
            f"[dim]{rows} archivo(s) preparado(s) · "
            f"+{total_add} -{total_del} líneas[/]"
        )


async def run_diff_unstaged_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Show unstaged working-tree changes (/diff-unstaged).

    The mirror of /diff-staged but for changes that are NOT yet
    `git add`-ed. Useful when the user has been editing files and wants
    to see what they have pending without confusing it with what's
    already staged for the next commit.

    Examples:
        /diff-unstaged              — full patch
        /diff-unstaged stats        — table with archivos + +/- counts
        /diff-unstaged <archivo>    — diff del archivo no-staged
    """
    text = args.strip()
    use_stats = text.lower() == "stats"

    if use_stats:
        cmd = ["git", "diff", "--numstat"]
    elif text:
        cmd = ["git", "diff", "--", text]
    else:
        cmd = ["git", "diff"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except Exception as exc:
        render_error(f"Error ejecutando git diff: {exc}")
        return

    if result.returncode != 0:
        error = result.stderr.strip() or "Error desconocido ejecutando git diff"
        render_error(error)
        return

    output = result.stdout.strip()
    if not output:
        console.print("[dim]No hay cambios sin preparar.[/]")
        return

    console.print(
        "\n[bold realm]᛭ Cambios sin preparar[/]"
        + (f" — {text}" if text and not use_stats else "")
    )

    if use_stats:
        _render_diff_staged_stats(output)  # same renderer; format is identical
    else:
        console.print(output, markup=False, highlight=False)
    console.print()


# Risk-tag heuristics used by ``/review --summary``. Lowercase.
_REVIEW_PATH_RISK_KEYWORDS: dict[str, str] = {
    "auth": "auth",
    "password": "auth",
    "secret": "secret",
    "token": "secret",
    "db": "db",
    "sql": "db",
    "migration": "db",
    "subprocess": "subprocess",
    "shell": "shell",
}
_REVIEW_CONTENT_RISK_MARKERS: dict[str, str] = {
    "todo": "todo",
    "fixme": "fixme",
    "xxx": "fixme",
    "secret": "secret",
    "password": "auth",
    "subprocess": "subprocess",
    "shell=true": "shell",
    "os.system": "shell",
    "eval(": "eval",
    "exec(": "eval",
}


def _review_summary(diff_text: str) -> dict[str, object]:
    """Resume un diff unificado en una vista apta para triage.

    Devuelve un diccionario con:
      * ``files`` — lista ordenada de rutas tocadas.
      * ``total_added`` / ``total_removed`` — líneas añadidas/eliminadas.
      * ``total_hunks`` — número total de hunks ``@@``.
      * ``per_file`` — lista con un dict por archivo (path, added,
        removed, hunks).
      * ``risk_tags`` — lista ordenada y sin duplicados de etiquetas de
        riesgo heurísticas (auth, db, secret, subprocess, shell, eval,
        todo, fixme).

    La función no llama a subprocess ni al LLM; opera sobre el texto
    crudo del diff y tolera entradas vacías o sin marcadores.
    """
    files: dict[str, dict[str, int]] = {}
    current_path: str | None = None
    total_added = 0
    total_removed = 0
    total_hunks = 0
    risk: set[str] = set()

    for raw_line in diff_text.splitlines():
        line = raw_line
        if line.startswith("diff --git "):
            # Format: ``diff --git a/<path> b/<path>`` (or ``a/<p>/b`` in
            # rename-with-copy cases). Use the b/ side when available.
            parts = line.split()
            if len(parts) >= 4:
                b_part = parts[-1]
                if b_part.startswith("b/"):
                    current_path = b_part[2:]
                else:
                    current_path = parts[2].lstrip("a/")
            if current_path is not None:
                files.setdefault(current_path, {"added": 0, "removed": 0, "hunks": 0})
            continue
        if line.startswith("@@"):
            total_hunks += 1
            if current_path is not None:
                files[current_path]["hunks"] += 1
            continue
        # Skip header / metadata noise.
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        if line.startswith("index ") or line.startswith("new file") or line.startswith("deleted file"):
            continue
        if line.startswith("Only in ") or line.startswith("Binary files"):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            total_added += 1
            if current_path is not None:
                files[current_path]["added"] += 1
            lower = line.lower()
            for needle, tag in _REVIEW_CONTENT_RISK_MARKERS.items():
                if needle in lower:
                    risk.add(tag)
            continue
        if line.startswith("-") and not line.startswith("---"):
            total_removed += 1
            if current_path is not None:
                files[current_path]["removed"] += 1
            continue

    # Path-keyword risk scan (case-insensitive, on the path itself).
    for path in list(files.keys()):
        lower = path.lower()
        for needle, tag in _REVIEW_PATH_RISK_KEYWORDS.items():
            if needle in lower:
                risk.add(tag)

    per_file = [
        {
            "path": p,
            "added": data["added"],
            "removed": data["removed"],
            "hunks": data["hunks"],
        }
        for p, data in (files.items())
    ]

    return {
        "files": sorted(files.keys()),
        "total_added": total_added,
        "total_removed": total_removed,
        "total_hunks": total_hunks,
        "per_file": per_file,
        "risk_tags": sorted(risk),
    }


def _print_review_summary(summary: dict[str, Any]) -> None:
    """Imprime el resumen de ``/review --summary`` en formato legible."""
    files_list: list[Any] = list(summary.get("files") or [])
    files: list[str] = [str(x) for x in files_list]
    added: int = int(summary.get("total_added") or 0)
    removed: int = int(summary.get("total_removed") or 0)
    hunks: int = int(summary.get("total_hunks") or 0)
    tags_list: list[Any] = list(summary.get("risk_tags") or [])
    tags: list[str] = [str(x) for x in tags_list]
    per_file_raw: list[Any] = list(summary.get("per_file") or [])
    print(f"Archivos modificados: {len(files)}")
    print(f"Hunks: {hunks}  |  +{added} líneas  /  -{removed} líneas")
    if tags:
        print(f"Riesgos detectados: {', '.join(tags)}")
    else:
        print("Riesgos detectados: (ninguno)")
    for entry in per_file_raw:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path", "?")
        a = entry.get("added", 0) or 0
        r = entry.get("removed", 0) or 0
        h = entry.get("hunks", 0) or 0
        print(f"  · {path}  +{a}/-{r}  hunks={h}")


async def run_review_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /review para revisar el diff de un PR o rama.

    Examples:
        /review
        /review --files
        /review --staged
        /review --summary [--staged]
        /review --agent [--staged] [nota]
        /review --agent [status|result|cancel]
    """
    try:
        tokens = shlex.split(args, posix=False)
    except ValueError as exc:
        render_error(f"Argumentos inválidos: {exc}")
        return
    if "--agent" in tokens:
        tokens.remove("--agent")
        await _run_review_agent_command(session, tokens)
        return
    summary_mode = False
    if "--summary" in tokens:
        tokens.remove("--summary")
        summary_mode = True
    if summary_mode:
        sub = " ".join(tokens).strip() or "diff"
        op, op_args = {
            "diff": ("diff", ""),
            "staged": ("diff", "--cached"),
        }.get(sub, ("diff", ""))
        result = _run_review_git(op=op, args=op_args)
        if not result.success:
            _print_tool_result(result)
            return
        raw = (result.data or {}).get("output", "") if isinstance(result.data, dict) else ""
        _print_review_summary(_review_summary(raw))
        return

    # Map the documented /review flags onto allowed git operations. Keep the
    # context collection here so every review uses the hardened git runner
    # below instead of an optional implementation with different safeguards.
    sub = args.strip().lstrip("-") or "diff"
    op, op_args = {
        "diff": ("diff", ""),
        "staged": ("diff", "--cached"),
        "files": ("diff", "--name-only"),
    }.get(sub, (sub, ""))
    result = _run_review_git(op=op, args=op_args)
    _print_tool_result(result)


def _run_review_git(*, op: str, args: str = "") -> ToolResult:
    """Recopila contexto Git sin ejecutar configuración activa del repo.

    ``git diff`` puede ejecutar comandos definidos por un repositorio no
    confiable mediante ``core.fsmonitor`` o ``diff.external``. La revisión
    ocurre antes de cualquier aprobación de herramientas, así que anulamos
    esas extensiones y también los drivers ``textconv`` para que obtener el
    diff sea una operación puramente de lectura.
    """
    op = op.strip().lower()
    allowed = {"status", "diff"}
    if op not in allowed:
        return ToolResult(
            success=False,
            data=None,
            error=f"Operación de revisión no permitida: '{op}'. Permitidas: diff, status",
        )

    try:
        extra = shlex.split(args, posix=False) if args else []
    except ValueError as exc:
        return ToolResult(success=False, data=None, error=f"Argumentos Git inválidos: {exc}")
    extra = [
        token[1:-1]
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'"
        else token
        for token in extra
    ]
    command = [
        "git",
        "--no-pager",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "diff.external=",
        op,
    ]
    if op == "diff":
        command.extend(["--no-ext-diff", "--no-textconv"])
    command.extend(extra)

    try:
        completed = subprocess.run(
            command,
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ToolResult(success=False, data=None, error=f"No se pudo ejecutar git: {exc}")

    data = {
        "output": completed.stdout,
        "stderr": completed.stderr,
        "returncode": completed.returncode,
        "command": command,
    }
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "error desconocido"
        return ToolResult(
            success=False,
            data=data,
            error=f"git {op} falló (código {completed.returncode}): {detail}",
        )
    return ToolResult(success=True, data=data)


async def _run_review_agent_task(
    session: SessionRuntime,
    *,
    diff_text: str,
    staged: bool,
    note: str,
) -> None:
    """Ejecuta una revisión aislada sin agregar mensajes al historial del chat."""
    state = getattr(session, "_review_agent_result", None)
    if not isinstance(state, dict):
        state = {}
        session._review_agent_result = state

    scope = "cambios staged" if staged else "cambios sin commit"
    clipped_diff = diff_text[:50_000]
    omitted = len(diff_text) - len(clipped_diff)
    truncated = f"\n\n[Diff truncado: {omitted} caracteres omitidos.]" if omitted else ""
    note_block = f"\nNota del usuario: {note}" if note else ""
    prompt = (
        "Revisá el siguiente diff como revisor senior. No tenés herramientas y no debés "
        "modificar archivos. Priorizá bugs, regresiones, vulnerabilidades, condiciones de "
        "carrera y tests faltantes; evitá comentarios cosméticos. Para cada hallazgo indicá "
        "severidad, archivo y línea cuando sea posible, y una corrección concreta. Si no hay "
        "problemas sustanciales, decilo explícitamente.\n"
        f"Alcance: {scope}.{note_block}\n\n```diff\n{clipped_diff}\n```{truncated}"
    )

    try:
        from lilith_tools.delegate import DelegateSubagentTool

        result = await asyncio.to_thread(
            DelegateSubagentTool().execute,
            preset="revisor-deepseek",
            prompt=prompt,
            max_tokens=3000,
        )
        if result.success:
            data = result.data if isinstance(result.data, dict) else {}
            content = data.get("content") or data.get("raw_content") or result.data or ""
            state.update(status="done", content=str(content), error="")
        else:
            state.update(
                status="error",
                content="",
                error=result.error or "La revisión del sub-agente falló",
            )
    except asyncio.CancelledError:
        state.update(status="cancelled", content="", error="")
        raise
    except Exception as exc:  # pragma: no cover - defensa ante providers externos
        state.update(status="error", content="", error=f"{type(exc).__name__}: {exc}")


async def _run_review_agent_command(session: SessionRuntime, tokens: list[str]) -> None:
    """Inicia, consulta o cancela una revisión de código en segundo plano."""
    action = tokens[0].lower() if len(tokens) == 1 else ""
    task = getattr(session, "_review_agent_task", None)
    state = getattr(session, "_review_agent_result", None)
    if not isinstance(state, dict):
        state = {}

    if action in {"status", "result"}:
        status = str(state.get("status") or ("running" if task and not task.done() else "none"))
        if action == "status":
            labels = {
                "running": "en curso",
                "done": "completada",
                "error": "fallida",
                "cancelled": "cancelada",
                "none": "sin revisión",
            }
            console.print(f"[info]Revisión de sub-agente:[/] {labels.get(status, status)}")
            return
        if status == "running":
            console.print("[dim]La revisión sigue en curso. Usá `/review --agent status`.[/dim]")
            return
        if status == "error":
            render_error(str(state.get("error") or "La revisión del sub-agente falló"))
            return
        if status != "done":
            console.print("[dim]No hay una revisión terminada para mostrar.[/dim]")
            return
        console.print("\n[bold realm]᛭ Revisión del sub-agente[/]")
        console.print(str(state.get("content") or "(sin contenido)"), markup=False)
        console.print()
        return

    if action == "cancel":
        if task is None or task.done():
            console.print("[dim]No hay una revisión activa para cancelar.[/dim]")
            return
        task.cancel()
        state.update(status="cancelled", content="", error="")
        session._review_agent_result = state
        console.print("[success]✓ Revisión cancelada.[/]")
        return

    if task is not None and not task.done():
        console.print("[warning]Ya hay una revisión en curso. Usá `/review --agent status`.[/]")
        return

    staged = "--staged" in tokens
    if staged:
        tokens.remove("--staged")
    unknown_flags = [token for token in tokens if token.startswith("--")]
    if unknown_flags:
        render_error(f"Opción desconocida: {unknown_flags[0]}")
        return
    note = " ".join(tokens).strip()

    diff_result = _run_review_git(
        op="diff",
        args="--cached" if staged else "",
    )
    if not diff_result.success:
        render_error(diff_result.error or "No se pudo obtener el diff para revisar")
        return
    data = diff_result.data if isinstance(diff_result.data, dict) else {}
    diff_text = str(data.get("output") or data.get("stdout") or "")
    if not diff_text.strip():
        scope = "staged" if staged else "sin commit"
        console.print(f"[dim]No hay cambios {scope} para revisar.[/dim]")
        return

    state = {
        "status": "running",
        "content": "",
        "error": "",
        "staged": staged,
        "started_at": datetime.now(UTC).isoformat(),
    }
    session._review_agent_result = state
    session._review_agent_task = asyncio.create_task(
        _run_review_agent_task(session, diff_text=diff_text, staged=staged, note=note),
        name="lilith-review-agent",
    )
    console.print(
        "[success]✓ Revisión iniciada en segundo plano con investigador-minimax.[/] "
        "[dim]Consultá `/review --agent result`.[/dim]"
    )


# ── Changelog helpers ───────────────────────────────────────────────

CHANGELOG_PATH = Path(__file__).resolve().parent.parent.parent / "CHANGELOG.md"
_CHANGELOG_PATH = CHANGELOG_PATH  # back-compat alias


def _parse_changelog_entries(text: str) -> list[dict]:
    """Parse Keep-a-Changelog markdown into list of {version, lines} entries."""
    import re as _re
    entries: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        m = _re.match(r"^##\s*\[([^\]]+)\](?:\s*-\s*\d{4}-\d{2}-\d{2})?", line)
        if m:
            if current is not None:
                entries.append(current)
            current = {"version": m.group(1).strip(), "lines": []}
        elif current is not None and line.strip():
            current["lines"].append(line)
    if current is not None:
        entries.append(current)
    return entries


async def run_changelog_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Show changelog history (/changelog [version|--list])."""
    text = args.strip()

    if not CHANGELOG_PATH.exists():
        render_error("No se encontró CHANGELOG.md")
        return

    try:
        content = CHANGELOG_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        render_error(f"Error leyendo changelog: {exc}")
        return

    entries = _parse_changelog_entries(content)

    if text.lower() in ("--list", "list"):
        if not entries:
            console.print("[dim]No hay entradas en el changelog.[/dim]")
            return
        console.print("[info]Versiones disponibles:[/info]")
        for entry in entries:
            console.print(f"  [bold cyan]v{entry['version']}[/bold cyan]")
        console.print()
        return

    if text:
        target = text
        match = next((e for e in entries if e["version"] == target), None)
        if not match:
            # Show available versions to help the user
            available = [entry["version"] for entry in entries]
            avail_preview = ", ".join(available[:5]) + ("..." if len(available) > 5 else "")
            render_error(f"No se encontró la versión {target}. Disponibles: {avail_preview}")
            return
        console.print(f"\n[bold realm]᛭ v{match['version']}[/]")
        for line in match["lines"]:
            console.print(line)
        console.print()
        return

    console.print(f"\n[bold realm]᛭ Changelog[/]")
    for entry in entries:
        console.print(f"\n[bold cyan]v{entry['version']}[/]")
        for line in entry["lines"]:
            console.print(line)
    console.print()
# ── /release (version bump + CHANGELOG entry + commit) ──────────────────────


_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][\w.]+)?$")


def _parse_version(text):
    """Return (major, minor, patch) for a semver-ish string, else None."""
    m = _VERSION_RE.match(text.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _format_version(v):
    return str(v[0]) + "." + str(v[1]) + "." + str(v[2])


def _bump_version(current, level):
    major, minor, patch = current
    if level == "major":
        return (major + 1, 0, 0)
    if level == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


_VERSION_LINE_RE = re.compile(r"""__version__\s*=\s*["']([^"']+)["']""")


def _read_package_version():
    """Read __version__ from lilith_cli/__init__.py."""
    init_path = Path(__file__).resolve().parent.parent / "__init__.py"
    try:
        text = init_path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _VERSION_LINE_RE.search(text)
    if not m:
        return None
    return _parse_version(m.group(1))


def _write_package_version(new_version):
    init_path = Path(__file__).resolve().parent.parent / "__init__.py"
    text = init_path.read_text(encoding="utf-8")
    new_text = _VERSION_LINE_RE.sub(
        '__version__ = "' + new_version + '"',
        text,
        count=1,
    )
    init_path.write_text(new_text, encoding="utf-8")


def _prepend_changelog(new_version, today):
    changelog = Path(__file__).resolve().parent.parent.parent / "CHANGELOG.md"
    if not changelog.exists():
        return False
    text = changelog.read_text(encoding="utf-8")
    entry = "## [" + new_version + "] - " + today + "\n\n- Bumped version to " + new_version + "\n\n"
    lines = text.splitlines(keepends=True)
    out = []
    inserted = False
    for i, line in enumerate(lines):
        out.append(line)
        if not inserted and line.startswith("## ") and i > 0:
            out.insert(-1, entry)
            inserted = True
    if not inserted:
        out.insert(0, entry)
    changelog.write_text("".join(out), encoding="utf-8")
    return True


async def run_release_command(session, args):  # noqa: ARG001
    """Bump version, update CHANGELOG, commit (no push).

    Usage: /release [patch|minor|major] [--dry-run]
    """
    raw = args.strip()
    dry_run = "--dry-run" in raw.split()
    level = next(
        (tok for tok in raw.split() if tok in {"patch", "minor", "major"}),
        "patch",
    )

    current = _read_package_version()
    if current is None:
        console.print("[error]No se pudo leer __version__ desde lilith_cli/__init__.py[/error]")
        return

    new = _bump_version(current, level)
    new_str = _format_version(new)
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    console.print("[info]Versión actual:[/info] " + _format_version(current))
    console.print("[info]Versión nueva:[/info]  " + new_str + " (" + level + ")")
    console.print("[info]Fecha:[/info]         " + today)
    console.print("[info]Dry-run:[/info]       " + ("sí" if dry_run else "no"))
    console.print()

    if dry_run:
        console.print(
            "[dim]DRY-RUN: se actualizaría __init__.py a "
            + new_str
            + " y se antepondría entrada al CHANGELOG[/dim]"
        )
        console.print(
            "[dim]DRY-RUN: se crearía commit 'chore(release): v"
            + new_str
            + "' (no se ejecuta)[/dim]"
        )
        return

    try:
        _write_package_version(new_str)
    except OSError as exc:
        console.print("[error]Error escribiendo __init__.py: " + str(exc) + "[/error]")
        return

    changelog_written = _prepend_changelog(new_str, today)
    if not changelog_written:
        console.print("[warning]⚠ CHANGELOG.md no existe; sólo se actualizó __init__.py[/warning]")

    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    # Only stage the files we just touched — never git add -A (rule #7).
    staged_paths: list[str] = []
    if changelog_written:
        staged_paths.append("CHANGELOG.md")
    staged_paths.append("lilith_cli/__init__.py")
    try:
        subprocess.run(
            ["git", "add", "--", *staged_paths],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        commit = subprocess.run(
            [
                "git",
                "-c",
                "user.email=hermes@nous.local",
                "-c",
                "user.name=Hermes",
                "commit",
                "-m",
                "chore(release): v" + new_str,
            ],
            cwd=str(repo_root),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        console.print("[error]git no está disponible en PATH[/error]")
        return

    if commit.returncode != 0:
        err_msg = (commit.stderr or commit.stdout or "").strip()
        console.print("[error]git commit falló: " + err_msg + "[/error]")
        return

    console.print("[success]✓ Released v" + new_str + "[/success]")


# ── /random command ─────────────────────────────────────────────────────────


# ── /pr — Push branch & open GitHub PR ─────────────────────────────


def _pr_detect_branch() -> str | None:
    """Lee la rama actual del repo anclado al CWD del proceso.

    Devuelve ``None`` si no estamos en un repo git o si la rama está detached.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    branch = result.stdout.strip()
    if not branch or branch == "HEAD":
        return None
    return branch


def _pr_remote_url(remote: str = "origin") -> str | None:
    """Devuelve la URL del remoto (SSH o HTTPS) o ``None`` si no existe."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", remote],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    url = result.stdout.strip()
    return url or None


def _pr_compare_url(remote_url: str, base: str, head: str) -> str | None:
    """Convierte una URL de remoto de GitHub en un enlace de compare.

    Soporta tanto ``git@github.com:owner/repo(.git)`` como
    ``https://github.com/owner/repo(.git)``. Devuelve ``None`` si el remoto
    no parece ser de GitHub.
    """
    m = re.match(r"git@github\.com[:/](?P<path>[^/]+/[^/]+?)(?:\.git)?$", remote_url)
    if not m:
        m = re.match(r"https?://github\.com/(?P<path>[^/]+/[^/]+?)(?:\.git)?/?$", remote_url)
    if not m:
        return None
    path = m.group("path")
    return f"https://github.com/{path}/compare/{base}...{head}?expand=1"


def _pr_render_usage() -> None:
    console.print(
        "[info]Uso:[/info] [bold cyan]/pr [base] [--no-push] [--dry-run] [--draft] "
        "[--title \"...\"] [--body \"...\"]\n"
        "    [dim]base[/dim]          Rama destino (default: [cyan]main[/cyan]).\n"
        "    [dim]--no-push[/dim]     No subir al remoto.\n"
        "    [dim]--dry-run[/dim]     Mostrar plan sin ejecutar nada.\n"
        "    [dim]--draft[/dim]       Abrir el PR como borrador (requiere [cyan]gh[/cyan]).\n"
        "    [dim]--title \"…\"[/dim]   Título del PR (por defecto: --fill usa el branch).\n"
        "    [dim]--body \"…\"[/dim]    Descripción del PR (por defecto: --fill usa commits)."
    )


def _pr_parse_option_value(args: str, flag: str) -> str | None:
    """Extrae el valor de un flag ``--flag valor`` (con comillas opcionales).

    Devuelve ``None`` si el flag no está presente o si el valor siguiente es
    otro flag (``--algo``). Soporta las formas ``--flag=valor`` y
    ``--flag=valor con espacios`` (re-uniendo los tokens que ``shlex``
    haya partido por el espacio). Solo se respeta el primero de los
    flags repetidos.
    """
    try:
        tokens = shlex.split(args)
    except ValueError:
        tokens = args.split()
    for index, token in enumerate(tokens):
        if token == flag and index + 1 < len(tokens):
            next_token = tokens[index + 1]
            if next_token.startswith("--"):
                return None
            return next_token
        if token.startswith(f"{flag}="):
            # ``shlex.split`` conserva ``--title=Hola`` como un único token,
            # pero parte ``--body=una desc`` en ``["--body=una", "desc"]``.
            # Devolvemos la parte tras el ``=`` y, si quedaron tokens
            # sueltos no-flag a continuación, los re-unimos con espacios.
            value = token.split("=", 1)[1]
            rest: list[str] = []
            for following in tokens[index + 1 :]:
                if following.startswith("--"):
                    break
                rest.append(following)
            if rest:
                value = f"{value} {' '.join(rest)}" if value else " ".join(rest)
            return value or None
    return None


def _pr_extract_base(args: str, all_tokens: list[str], flags_with_value: tuple[str, ...]) -> str:
    """Devuelve el primer token que no es flag ni valor de flag con argumento.

    Si no hay ninguno, devuelve ``"main"`` por defecto.
    """
    skip_next = False
    for token in all_tokens:
        if skip_next:
            skip_next = False
            continue
        if token in flags_with_value:
            skip_next = True
            continue
        if token.startswith("--"):
            continue
        # También saltamos tokens de la forma ``--flag=value``.
        if any(token.startswith(f"{f}=") for f in flags_with_value):
            continue
        return token
    return "main"


async def run_pr_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Empuja la rama actual y abre un Pull Request en GitHub.

    Examples:
        /pr
        /pr main
        /pr develop --draft
        /pr --dry-run
        /pr main --no-push
        /pr --title "Fix login bug" --body "Closes #42"
    """
    # Parseamos flags primero (pueden estar en cualquier posición).
    try:
        all_tokens = shlex.split(args)
    except ValueError:
        all_tokens = args.split()
    no_push = "--no-push" in all_tokens
    dry_run = "--dry-run" in all_tokens
    draft = "--draft" in all_tokens
    pr_title = _pr_parse_option_value(args, "--title")
    pr_body = _pr_parse_option_value(args, "--body")
    base = _pr_extract_base(args, all_tokens, ("--title", "--body"))

    branch = _pr_detect_branch()
    if not branch:
        render_error("No se pudo detectar la rama actual. ¿Estás dentro de un repo git?")
        return
    if branch == base:
        render_error(f"La rama actual ({branch!r}) es la misma que el destino ({base!r}).")
        return

    remote_url = _pr_remote_url("origin")
    if not remote_url:
        render_error(
            "No hay remoto 'origin' configurado. Configurá uno con `git remote add origin …`."
        )
        return
    compare = _pr_compare_url(remote_url, base, branch)
    if not compare:
        render_error(
            f"El remoto 'origin' ({remote_url!r}) no parece ser de GitHub. "
            "Por ahora /pr solo soporta GitHub."
        )
        return

    console.print(f"[info]Rama:[/info]   [bold cyan]{branch}[/bold cyan]")
    console.print(f"[info]Hacia:[/info]  [bold cyan]{base}[/bold cyan]")
    console.print(f"[info]Remoto:[/info] [dim]{remote_url}[/dim]")
    console.print(f"[info]URL:[/info]    [link={compare}]{compare}[/link]")
    if dry_run:
        console.print(
            "[info]Modo dry-run:[/info] no se ejecutó [bold]git push[/bold] "
            "ni [bold]gh pr create[/bold]."
        )
        return

    if not no_push:
        push = subprocess.run(
            ["git", "push", "-u", "origin", branch],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if push.returncode != 0:
            render_error(
                "git push falló:\n"
                + (push.stderr.strip() or push.stdout.strip() or "sin salida")
            )
            return
        if push.stdout.strip():
            console.print(f"[dim]{push.stdout.strip()}[/dim]")

    # Detectar `gh` para abrir el PR programáticamente.
    gh_path = shutil.which("gh")
    if not gh_path:
        console.print(
            "[info]No se encontró [bold]gh[/bold] en el PATH.[/info]\n"
            "Subí la rama manualmente y abrí el PR desde la URL de arriba."
        )
        return

    gh_cmd = [
        gh_path,
        "pr",
        "create",
        "--base",
        base,
        "--head",
        branch,
    ]
    if draft:
        gh_cmd.append("--draft")
    if pr_title is not None:
        gh_cmd.extend(["--title", pr_title])
    if pr_body is not None:
        gh_cmd.extend(["--body", pr_body])
    # Si no se pasaron title/body, --fill usa el commit/branch como base.
    if pr_title is None and pr_body is None:
        gh_cmd.append("--fill")

    gh = subprocess.run(gh_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if gh.returncode != 0:
        # No abortamos: el push ya se hizo, el usuario puede abrir el PR manual.
        console.print(
            "[warning]gh pr create falló. La rama YA está en el remoto; "
            "abrí el PR desde la URL.[/warning]\n"
            f"[dim]{gh.stderr.strip() or gh.stdout.strip() or 'sin salida'}[/dim]"
        )
        return
    pr_url = gh.stdout.strip().splitlines()[-1] if gh.stdout.strip() else compare
    console.print(f"[success]✓ PR abierto:[/success] [link={pr_url}]{pr_url}[/link]")


async def run_diff_branch_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Muestra el diff entre HEAD y una referencia arbitraria (/diff-branch).

    Acepta un branch, tag o commit arbitrario como ref y compara contra HEAD.
    Por defecto usa ``git diff <ref>...HEAD`` (cambios introducidos en HEAD
    desde <ref>) — el triple-punto estándar que ya entienden los usuarios de
    git. Soporta el modo ``stats`` para una tabla compacta de archivos +/-
    y un path opcional al final para acotar la salida a un archivo.

    Examples:
        /diff-branch main                — diff HEAD vs main
        /diff-branch main stats          — tabla con archivos + +/- counts
        /diff-branch v1.2.0              — diff contra un tag
        /diff-branch origin/main src/foo.py  — un solo archivo
    """
    tokens = args.split()
    if not tokens:
        render_error(
            "Uso: /diff-branch <ref> [stats] [<archivo>] — por ejemplo "
            "/diff-branch main"
        )
        return

    ref = tokens[0]
    # ``stats`` se consume como flag, no como parte del ref. Si aparece
    # más tarde (después de un path) la ignoramos para no romper el contrato.
    rest = tokens[1:]
    use_stats = bool(rest) and rest[0].lower() == "stats"
    if use_stats:
        rest = rest[1:]
    file_filter = rest[0] if rest else ""

    if use_stats:
        if file_filter:
            cmd = ["git", "diff", "--numstat", f"{ref}...HEAD", "--", file_filter]
        else:
            cmd = ["git", "diff", "--numstat", f"{ref}...HEAD"]
    elif file_filter:
        cmd = ["git", "diff", f"{ref}...HEAD", "--", file_filter]
    else:
        cmd = ["git", "diff", f"{ref}...HEAD"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except Exception as exc:
        render_error(f"Error ejecutando git diff {ref}...HEAD: {exc}")
        return

    if result.returncode != 0:
        error = result.stderr.strip() or (
            f"Error desconocido ejecutando git diff {ref}...HEAD"
        )
        render_error(error)
        return

    output = result.stdout.strip()
    if not output:
        console.print(
            f"[dim]Sin cambios entre [bold cyan]{ref}[/] y HEAD.[/]"
        )
        return

    console.print(
        f"\n[bold realm]᛭ Diff[/] [bold cyan]{ref}[/] → "
        f"[bold cyan]HEAD[/]"
        + (f" — {file_filter}" if file_filter else "")
    )

    if use_stats:
        _render_diff_branch_stats(output, ref)
    else:
        console.print(output, markup=False, highlight=False)
    console.print()


def _render_diff_branch_stats(numstat_output: str, ref: str) -> None:
    """Render the ``--numstat`` output of ``git diff <ref>...HEAD``.

    Reutiliza exactamente el mismo formato Rich Table que
    ``_render_diff_staged_stats`` para que el usuario vea el mismo look
    en ``/diff-staged stats`` y ``/diff-branch <ref> stats``. La única
    diferencia visible es el footer, que incluye la referencia contra
    la que se compara.
    """
    from rich.table import Table

    table = Table(
        show_header=True,
        header_style="bold cyan",
        border_style="cyan",
        expand=False,
    )
    table.add_column("Archivo", style="tool.name")
    table.add_column("+", justify="right", style="green", width=6)
    table.add_column("-", justify="right", style="red", width=6)

    total_add = 0
    total_del = 0
    rows = 0
    for line in numstat_output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_raw, removed_raw, path = parts[0], parts[1], "\t".join(parts[2:])
        # Binary files report '-' for both counts.
        if added_raw == "-" and removed_raw == "-":
            added = "-"
            removed = "-"
        else:
            try:
                added = int(added_raw)
                removed = int(removed_raw)
                total_add += added
                total_del += removed
            except ValueError:
                added = added_raw
                removed = removed_raw
        rows += 1
        table.add_row(path, str(added), str(removed))

    console.print(table)
    if rows:
        console.print(
            f"[dim]{rows} archivo(s) entre [bold cyan]{ref}[/] y HEAD · "
            f"+{total_add} -{total_del} líneas[/]"
        )
# ── /apply ────────────────────────────────────────────────────────────────────
# Aplica un parche unified-diff (formato git) al árbol de trabajo.
# Pensado para que un agente pegue un diff propuesto y el usuario lo apruebe
# con un solo comando. Por seguridad: rechaza rutas fuera del repo y exige
# confirmación cuando --check falla. Delega TODO el trabajo a `git apply`,
# sin reinventar el parser de unified diff (riesgo de corrupcion).

_APPLY_HELP_LINES = (
    "  [cyan]/apply <archivo.diff>[/]            — aplicar parche desde archivo",
    "  [cyan]/apply --stdin[/]                   — leer el parche de stdin (pegar)",
    "  [cyan]/apply --check[/]                   — solo verificar, no escribe",
    "  [cyan]/apply --dry-run[/]                 — alias de --check (verbose)",
    "  [cyan]/apply --reverse[/]                 — revertir el parche (git apply -R)",
    "  [cyan]/apply --3way[/]                    — fusión de 3 vías si falla",
    "  [cyan]/apply --include <patrón>[/]        — limitar archivos tocados",
)


def _render_apply_usage() -> None:
    """Muestra la ayuda de /apply."""
    console.print("\n[bold realm]᛭ Uso de /apply[/]")
    for line in _APPLY_HELP_LINES:
        console.print(line)
    console.print(
        "\n[dim]El parche debe estar en formato unified diff (lo que produce "
        "`git diff` o `git format-patch`). Por seguridad no se aplica a rutas "
        "fuera del repositorio actual.[/]\n"
    )


def _resolve_repo_root(start: Path | None = None) -> Path | None:
    """Devuelve la raíz del repo git que contiene ``start`` (o CWD)."""
    cwd = (start or Path.cwd()).resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return Path(result.stdout.strip()).resolve()
    except OSError:
        return None


def _check_patch_targets_in_repo(patch_text: str, repo_root: Path) -> list[str]:
    """Inspecciona las cabeceras ``+++`` y devuelve las que salen del repo.

    No rechaza ``/dev/null`` (creación/eliminación legítima). Devuelve lista
    vacía si todos los targets están dentro del repo.
    """
    bad: list[str] = []
    for line in patch_text.splitlines():
        if not line.startswith("+++ "):
            continue
        parts = line[4:].split("\t", 1)[0].strip()
        if parts == "/dev/null":
            continue
        if parts.startswith(("a/", "b/")):
            parts = parts[2:]
        if not parts:
            continue
        try:
            target = (repo_root / parts).resolve()
        except OSError:
            bad.append(parts)
            continue
        try:
            target.relative_to(repo_root)
        except ValueError:
            bad.append(parts)
    return bad


async def run_apply_command(session: SessionRuntime, args: str) -> None:  # noqa: ARG001
    """Ejecuta /apply para aplicar un parche unified-diff al árbol de trabajo.

    Examples:
        /apply fix.patch
        /apply --stdin
        /apply fix.patch --check
        /apply fix.patch --reverse
        /apply fix.patch --include 'src/*.py'
        /apply fix.patch --3way
    """
    text = args.strip()

    if not text or text.lower() in ("help", "--help", "-h", "?"):
        _render_apply_usage()
        return

    parser = argparse.ArgumentParser(prog="/apply", add_help=False)
    parser.add_argument("file", nargs="?")
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reverse", "-R", action="store_true")
    parser.add_argument("--3way", dest="threeway", action="store_true")
    parser.add_argument("--include", dest="include", default=None)
    parser.add_argument("--quiet", "-q", action="store_true")
    # posix=False preserva las barras invertidas de rutas Windows; luego
    # limpiamos comillas envolventes para que `/apply "ruta con espacios.patch"`
    # siga funcionando.
    raw_tokens = shlex.split(text, posix=False) if text else []
    clean_tokens: list[str] = []
    for tok in raw_tokens:
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ('"', "'"):
            tok = tok[1:-1]
        clean_tokens.append(tok)
    try:
        parsed, unknown = parser.parse_known_args(clean_tokens)
    except SystemExit:
        return
    if unknown:
        render_error(f"Argumentos no reconocidos para /apply: {' '.join(unknown)}")
        return

    patch_text: str | None = None
    source_desc = ""
    try:
        if parsed.stdin:
            patch_text = sys.stdin.read()
            source_desc = "<stdin>"
        elif parsed.file:
            patch_path = Path(parsed.file).expanduser()
            if not patch_path.exists():
                render_error(f"No existe el archivo de parche: {parsed.file}")
                return
            try:
                patch_text = patch_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                patch_text = patch_path.read_text(encoding="latin-1")
            source_desc = str(patch_path)
        else:
            render_error("Uso: /apply <archivo.diff> | /apply --stdin")
            return
    except KeyboardInterrupt:
        render_error("Lectura de parche cancelada.")
        return

    if not patch_text or not patch_text.strip():
        render_error("El parche está vacío.")
        return

    repo_root = _resolve_repo_root()
    if repo_root is None:
        render_error(
            "No se encontró raíz de repositorio git. /apply requiere estar "
            "dentro de un repo (o subdirectorio del mismo)."
        )
        return

    bad_targets = _check_patch_targets_in_repo(patch_text, repo_root)
    if bad_targets:
        joined = ", ".join(bad_targets)
        render_error(
            f"El parche apunta a rutas fuera del repositorio [{repo_root}]: "
            f"{joined}. Rechazado por seguridad."
        )
        return

    git_args = ["git", "apply"]
    if parsed.check or parsed.dry_run:
        git_args.append("--check")
    if parsed.reverse:
        git_args.append("--reverse")
    if parsed.threeway:
        git_args.append("--3way")
    if parsed.include:
        git_args.extend(["--include", parsed.include])
    if parsed.quiet:
        git_args.append("--quiet")
    git_args.append("-")  # leemos el parche de stdin para evitar quoting.

    try:
        result = subprocess.run(
            git_args,
            input=patch_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=repo_root,
            check=False,
        )
    except FileNotFoundError:
        render_error("git no está disponible en el PATH; no se puede aplicar.")
        return

    if result.returncode == 0:
        if parsed.check or parsed.dry_run:
            console.print(
                f"[success]✓ Parche válido (no aplicado) · fuente: {source_desc}[/]"
            )
            return
        try:
            list_proc = subprocess.run(
                ["git", "diff", "--name-only"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            affected = [
                ln.strip() for ln in list_proc.stdout.splitlines() if ln.strip()
            ]
        except FileNotFoundError:
            affected = []
        if affected:
            preview = affected[:8]
            more = "" if len(affected) <= 8 else f" (+{len(affected) - 8} más)"
            joined = ", ".join(preview)
            console.print(
                f"[success]✓ Parche aplicado ({len(affected)} archivo(s)): "
                f"{joined}{more}[/]"
            )
            console.print(
                "[dim]  Usa /diff-unstaged para revisar o /git para commitear.[/]"
            )
        else:
            console.print(f"[success]✓ Parche aplicado · fuente: {source_desc}[/]")
        return

    err = (result.stderr or result.stdout or "git apply falló sin mensaje").strip()
    render_error(f"No se pudo aplicar el parche: {err}")
    console.print(
        "[dim]Sugerencias: probá /apply --check para validar primero, --reverse "
        "si lo aplicaste dos veces, o --3way si hay drift.[/]"
    )

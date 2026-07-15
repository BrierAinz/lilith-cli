"""Yggdrasil CLI v6.0 — Unified entry point.

Usage:
  yggdrasil              # Launch interactive REPL
  yggdrasil "prompt"     # One-shot mode
  yggdrasil chat          # Explicit REPL mode
  yggdrasil status        # Show realm status
  yggdrasil launch        # Launch services
  yggdrasil config        # Show/edit configuration
"""

from __future__ import annotations

import asyncio
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from cyclopts import App, Parameter

from .config import CONFIG_DIR, YggdrasilConfig, load_config, save_config
from .ops import agents as _agents_cmd
from .ops import bus_app as _bus_app
from .ops_do import do_app as _do_app
from .ops_knowledge import ask as _ask_cmd
from .ops_knowledge import memory_app as _memory_app
from .ops_pantheon import goals_app as _goals_app
from .ops_pantheon import policy_app as _policy_app
from .ops_queue import queue_app as _queue_app
from .ops_queue import work_app as _work_app
from .ops_spawn import spawn_app as _spawn_app


if TYPE_CHECKING:
    import types


# ── Version ─────────────────────────────────────────────────────────

from . import __version__  # single-source-of-truth

# ── Cyclopts app ────────────────────────────────────────────────────

app = App(
    name="yggdrasil",
    help="Yggdrasil CLI v6.0 — Where Ancient Meets Digital",
    version=__version__,
)

# Operator console (plan-29 A1): agents + bus subcommand group.
app.command(_agents_cmd)
app.command(_bus_app)

# Operator console (plan-29 A2): ask (Mimir RAG) + memory subcommand group.
app.command(_ask_cmd)
app.command(_memory_app)

# Operator console (plan-29 A3): spawn a Vanaheim agent as a real subprocess.
app.command(_spawn_app)

# Operator console (plan-29 C): queue + work — the work-driven successor
# to the retired cron loop. ``queue add`` enqueues a ``queue.task`` on
# the bus; ``work`` claims and executes via the shared spawn core.
app.command(_queue_app)
app.command(_work_app)

# Operator console (plan-29 B): `do` — natural-language routing + spawn
# composition. Composes C's card loader + AgentRouter with A3's spawn
# core into a single end-to-end command.
app.command(_do_app)

# Operator console (plan-29 A4): pantheon passthrough — read-only mirror
# of ``ygg context goals`` + ``ygg context eval`` / ``ygg context policies``.
# Writes (goal-new/turn/complete, policy init) stay on the ``ygg`` CLI to
# avoid two surfaces competing for the same state.
app.command(_goals_app)
app.command(_policy_app)


# ── Helpers ─────────────────────────────────────────────────────────


def _is_wsl() -> bool:
    """Check if running under WSL."""
    return platform.system() == "Linux" and "microsoft" in platform.release().lower()


def _resolve_yggdrasil_root() -> Path:
    """Find the Yggdrasil workspace root.

    Order: ``YGGDRASIL_ROOT`` env var, then the nearest ancestor that
    contains the hub's ``ygg.py``. The fixed-depth fallback only holds for
    the historical ``Asgard/lilith-cli`` layout, not for standalone
    checkouts of lilith-stack.
    """
    env_root = os.environ.get("YGGDRASIL_ROOT")
    if env_root:
        return Path(env_root)
    for parent in Path(__file__).resolve().parents:
        if (parent / "ygg.py").is_file():
            return parent
    return Path(__file__).resolve().parents[3]


def _lazy_import_ygg() -> types.ModuleType:
    """Import the hub's ygg module, adding the root to sys.path."""
    root = str(_resolve_yggdrasil_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    import ygg

    return ygg


def _apply_overrides(
    cfg: YggdrasilConfig,
    *,
    model: str | None = None,
    provider: str | None = None,
    local: bool = False,
    no_tools: bool = False,
) -> None:
    """Apply CLI flag overrides to a loaded config."""
    if model:
        cfg.model = model
    if provider:
        cfg.provider = provider
    if local:
        cfg.provider = "local"
        if cfg.base_url is None:
            cfg.base_url = "http://localhost:1234/v1"
        # If the user hasn't explicitly picked a model, fall back to
        # the local default. This used to check for ``gpt-4o-mini``
        # (the old OpenAI default); now it also recognises the
        # Sakana Fugu default so ``--local`` works out of the box
        # whether the active provider is OpenAI or Sakana.
        if cfg.model in ("gpt-4o-mini", "fugu-ultra"):
            cfg.model = "local-model"
    if no_tools:
        cfg.tools.filesystem = False
        cfg.tools.coding = False
        cfg.tools.web_search = False
        cfg.tools.browser = False
        cfg.tools.system = False


# ── Commands ────────────────────────────────────────────────────────


@app.command
def chat(
    model: Annotated[str | None, Parameter(name=["--model", "-m"], help="Override model")] = None,
    provider: Annotated[
        str | None,
        Parameter(name=["--provider", "-p"], help="Override provider"),
    ] = None,
    local: Annotated[bool, Parameter(name="--local", help="Use local LM Studio")] = False,
    no_tools: Annotated[bool, Parameter(name="--no-tools", help="Disable tools")] = False,
    verbose: Annotated[bool, Parameter(name=["--verbose", "-v"], help="Debug output")] = False,
    config_path: Annotated[str | None, Parameter(name="--config", help="Config file path")] = None,
) -> None:
    """Iniciar el REPL interactivo de Yggdrasil Agent."""
    import logging

    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    cfg = load_config(config_path)
    _apply_overrides(cfg, model=model, provider=provider, local=local, no_tools=no_tools)

    from .agent import AgentSession
    from .repl import run_repl

    session = AgentSession(cfg)
    asyncio.run(run_repl(session))


@app.command
def ide(
    root: Annotated[str | None, Parameter(name=["--root", "-r"], help="Project root to browse")] = None,
    model: Annotated[str | None, Parameter(name=["--model", "-m"], help="Override model")] = None,
    provider: Annotated[
        str | None,
        Parameter(name=["--provider", "-p"], help="Override provider"),
    ] = None,
    local: Annotated[bool, Parameter(name="--local", help="Use local LM Studio")] = False,
    no_tools: Annotated[bool, Parameter(name="--no-tools", help="Disable tools")] = False,
    config_path: Annotated[str | None, Parameter(name="--config", help="Config file path")] = None,
) -> None:
    """Lanzar el modo IDE TUI de Lilith (file tree + chat + code preview)."""
    cfg = load_config(config_path)
    _apply_overrides(cfg, model=model, provider=provider, local=local, no_tools=no_tools)

    from .agent import AgentSession
    from .ide import run_ide

    session = AgentSession(cfg)
    project_root = Path(root).resolve() if root else Path.cwd()
    run_ide(session, root=project_root)


@app.command
def prompt(
    text: Annotated[str, Parameter(help="Prompt para enviar al agente")],
    model: Annotated[str | None, Parameter(name=["--model", "-m"], help="Override model")] = None,
    provider: Annotated[
        str | None,
        Parameter(name=["--provider", "-p"], help="Override provider"),
    ] = None,
    local: Annotated[bool, Parameter(name="--local", help="Use local LM Studio")] = False,
    no_tools: Annotated[bool, Parameter(name="--no-tools", help="Disable tools")] = False,
    config_path: Annotated[str | None, Parameter(name="--config", help="Config file path")] = None,
    yes: Annotated[
        bool,
        Parameter(
            name="--yes",
            help="Skip destructive-write confirmation for the duration of this run",
        ),
    ] = False,
    max_iterations: Annotated[
        int | None,
        Parameter(
            name="--max-iterations",
            help="Override the tool-calling loop cap for this run",
        ),
    ] = None,
) -> None:
    """Modo one-shot: enviar un prompt y mostrar la respuesta."""
    cfg = load_config(config_path)
    _apply_overrides(cfg, model=model, provider=provider, local=local, no_tools=no_tools)
    if yes:
        cfg.confirm_write = False
    if max_iterations is not None:
        if max_iterations < 1:
            from .render import console
            console.print("[error]--max-iterations debe ser >= 1[/]")
            raise SystemExit(2)
        cfg.max_iterations = max_iterations

    from .agent import AgentSession
    from .repl import run_oneshot

    session = AgentSession(cfg)
    asyncio.run(run_oneshot(session, text))


@app.command
def status() -> None:
    """Mostrar estado de salud de los reinos y servicios de Yggdrasil."""
    try:
        ygg_cli = _lazy_import_ygg()
        ygg_cli.status()
    except (ImportError, ModuleNotFoundError):
        from .render import console

        console.print("[error]No se pudo importar ygg. Verifica la instalación.[/]")


@app.command
def launch() -> None:
    """Abrir menú interactivo para lanzar servicios de Yggdrasil."""
    # ygg.py no expone un comando 'launch' Cyclopts (el menú interactivo vivía
    # en yggdrasil_cli.py, eliminado en el dedup). Se delega a ygg.status() como
    # vista de servicios hasta que se añada el equivalente en el hub.
    try:
        ygg_cli = _lazy_import_ygg()
        ygg_cli.status()
    except (ImportError, ModuleNotFoundError):
        from .render import console

        console.print("[error]No se pudo importar ygg. Verifica la instalación.[/]")


@app.command
def config(
    _show: Annotated[bool, Parameter(name="--show", help="Mostrar configuración")] = True,
    edit: Annotated[bool, Parameter(name="--edit", help="Abrir configuración en editor")] = False,
    reset: Annotated[
        bool,
        Parameter(name="--reset", help="Restablecer configuración por defecto"),
    ] = False,
    config_path: Annotated[
        str | None,
        Parameter(name="--path", help="Ruta del archivo de config"),
    ] = None,
) -> None:
    """Mostrar o editar la configuración de Yggdrasil."""
    from .render import console

    if reset:
        path = Path(config_path) if config_path else CONFIG_DIR / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        save_config(YggdrasilConfig(), config_path=str(path))
        console.print(f"[success]✓ Configuración restablecida en: {path}[/]")
        return

    if edit:
        path = Path(config_path) if config_path else CONFIG_DIR / "config.yaml"
        if not path.exists():
            load_config(str(path))  # bootstrap
        editor = "nano" if _is_wsl() else ("notepad" if platform.system() == "Windows" else "vi")
        try:
            subprocess.run([editor, str(path)], check=False)
        except FileNotFoundError:
            console.print(f"[error]Editor '{editor}' no encontrado. Edita manualmente: {path}[/]")
        return

    # Default: show.
    cfg = load_config(config_path)
    console.print(cfg.model_dump_json(indent=2))


# ── Delegate (Hlidskjalf preset → provider profile) ────────────────


def _load_subagent_presets(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load Hlidskjalf subagent presets from ``~/.yggdrasil/hlidskjalf_subagents.yaml``.

    The file is a flat mapping of preset name → {provider, model, temperature,
    system_prompt}. Missing or empty → return {}.
    """
    preset_file = Path.home() / ".yggdrasil" / "hlidskjalf_subagents.yaml"
    if not preset_file.exists():
        return {}
    try:
        import yaml as _yaml

        data = _yaml.safe_load(preset_file.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


@app.command
def delegate(
    target: Annotated[
        str,
        Parameter(help="Provider profile name (sakana, kimi, deepseek, mimo, m2, ...)"),
    ],
    text: Annotated[str, Parameter(help="Prompt to send")],
    model: Annotated[str | None, Parameter(name=["--model", "-m"])] = None,
    config_path: Annotated[str | None, Parameter(name="--config")] = None,
) -> None:
    """Shortcut: send a one-shot prompt to a specific provider profile.

    Example:
        lilith delegate sakana "summarise the Asgard realm"
        lilith delegate kimi "refactor this loop" --model kimi-k2
    """
    cfg = load_config(config_path)

    target_lower = target.lower()
    if target_lower not in (cfg.providers or {}):
        available = sorted(cfg.providers.keys()) if cfg.providers else []
        from .render import console

        console.print(
            f"[error]Provider '{target}' not in config. "
            f"Available: {available}[/]"
        )
        raise SystemExit(2)

    provider_profile = cfg.providers[target_lower]
    cfg.provider = target_lower
    if model:
        cfg.model = model
    elif provider_profile.model:
        cfg.model = provider_profile.model

    from .agent import AgentSession
    from .repl import run_oneshot

    session = AgentSession(cfg)
    asyncio.run(run_oneshot(session, text))


@app.command
def subagent(
    preset: Annotated[
        str,
        Parameter(help="Hlidskjalf preset name (ejecutor-kimi, investigador-minimax, batch-deepseek, orquestador-fugu)"),
    ],
    text: Annotated[str, Parameter(help="Prompt to send via the preset")],
    config_path: Annotated[str | None, Parameter(name="--config")] = None,
) -> None:
    """Route a prompt through a Hlidskjalf sub-agent preset.

    Presets live in ``~/.yggdrasil/hlidskjalf_subagents.yaml`` and map
    (provider, model, temperature, system_prompt) → preset name.
    """
    presets = _load_subagent_presets(config_path)
    if preset not in presets:
        available = sorted(presets.keys())
        from .render import console

        console.print(
            f"[error]Preset '{preset}' not declared. "
            f"Available: {available or '(none — check hlidskjalf_subagents.yaml)'}[/]"
        )
        raise SystemExit(2)

    p = presets[preset]
    cfg = load_config(config_path)

    provider_name = (p.get("provider") or cfg.provider).lower()
    if provider_name not in (cfg.providers or {}):
        from .render import console

        console.print(
            f"[error]Preset '{preset}' targets provider '{provider_name}' "
            f"which is missing from config.yaml providers map.[/]"
        )
        raise SystemExit(2)

    provider_profile = cfg.providers[provider_name]
    cfg.provider = provider_name
    cfg.model = p.get("model") or provider_profile.model
    if p.get("temperature") is not None:
        cfg.temperature = float(p["temperature"])
    if p.get("system_prompt"):
        cfg.system_prompt = p["system_prompt"]

    from .agent import AgentSession
    from .repl import run_oneshot

    session = AgentSession(cfg)
    asyncio.run(run_oneshot(session, text))


# ── Default handler (no subcommand) ────────────────────────────────


@app.default
def default_command(
    args: Annotated[tuple[str, ...] | None, Parameter(show=False)] = (),
    model: Annotated[str | None, Parameter(name=["--model", "-m"])] = None,
    provider: Annotated[str | None, Parameter(name=["--provider", "-p"])] = None,
    local: Annotated[bool, Parameter(name="--local")] = False,
    no_tools: Annotated[bool, Parameter(name="--no-tools")] = False,
    verbose: Annotated[bool, Parameter(name=["--verbose", "-v"])] = False,
    config_path: Annotated[str | None, Parameter(name="--config")] = None,
    version: Annotated[bool, Parameter(name=["--version"])] = False,
) -> None:
    """Punto de entrada por defecto — lanza REPL o procesa un prompt directo."""
    from .render import console

    if version:
        console.print(f"Yggdrasil CLI v{__version__}")
        return

    # If positional args look like a prompt, go one-shot.
    if args:
        prompt_text = " ".join(args)
        cfg = load_config(config_path)
        _apply_overrides(cfg, model=model, provider=provider, local=local, no_tools=no_tools)

        from .agent import AgentSession
        from .repl import run_oneshot

        session = AgentSession(cfg)
        asyncio.run(run_oneshot(session, prompt_text))
        return

    # No args → interactive REPL.
    chat(
        model=model,
        provider=provider,
        local=local,
        no_tools=no_tools,
        verbose=verbose,
        config_path=config_path,
    )


# ── Entry point ─────────────────────────────────────────────────────

def _reconfigure_stdio() -> None:
    """Force UTF-8 on stdout/stderr to survive cp1252 consoles.

    Windows consoles default to cp1252, which cannot encode the ▸,
    ─, é, and other glyphs that render.py / commands.py /
    Rich panels emit.  Without this reconfigure, ``lilith.exe`` crashes with
    ``UnicodeEncodeError: 'charmap' codec can't encode character '\u25b8'``
    when invoked from a non-UTF-8 terminal (Git Bash on Windows, cmd.exe,
    any pipe into a non-UTF-8 consumer).  ``PYTHONIOENCODING=utf-8`` already
    fixes it at the OS env level; this reconfigure is the in-process equivalent.

    Safe no-op when stdout is already UTF-8 (the typical case on Linux/macOS,
    or on Windows when the user has ``PYTHONUTF8=1``).  ``reconfigure`` is a
    ``TextIOWrapper`` method (Python 3.7+); we guard with ``getattr`` so the
    helper does not blow up if stdout was replaced by a third-party wrapper
    that does not expose ``reconfigure``.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # Never block the CLI on a stream that refuses to reconfigure;
            # the user can still set ``PYTHONIOENCODING=utf-8`` themselves.
            pass




def main() -> None:
    """CLI entry point."""
    _reconfigure_stdio()
    app()

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
import copy
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
        Parameter(help="Provider profile name (sakana, kimi, deepseek, mimo, m2, ...) OR Hlidskjalf preset name when --preset is used"),
    ],
    text: Annotated[str, Parameter(help="Prompt to send")],
    model: Annotated[str | None, Parameter(name=["--model", "-m"])] = None,
    config_path: Annotated[str | None, Parameter(name="--config")] = None,
    preset: Annotated[str | None, Parameter(name=["--preset"], help="Use a Hlidskjalf sub-agent preset from ~/.yggdrasil/hlidskjalf_subagents.yaml (overrides target as provider profile)")] = None,
    agentic: Annotated[bool, Parameter(name=["--agentic"], help="Run the preset with the agentic mini-loop (file tools in workdir)")] = False,
    structured: Annotated[bool, Parameter(name=["--structured"], help="Force the preset response to validate against TASK_SCHEMA (degradation chain: json_schema -> json_object -> prompt)")] = False,
    max_tokens: Annotated[int | None, Parameter(name=["--max-tokens"], help="Override max_tokens for this preset call")] = None,
    max_turns: Annotated[int | None, Parameter(name=["--max-turns"], help="Cap the agentic mini-loop turns (default 10 in DelegateSubagentTool)")] = None,
) -> None:
    """Shortcut: send a one-shot prompt to a specific provider profile.

    Example:
        lilith delegate sakana "summarise the Asgard realm"
        lilith delegate kimi "refactor this loop" --model kimi-k2
        lilith delegate ejecutor-kimi "write tests" --preset ejecutor-kimi --agentic --max-turns 5

    Compatibility: without any of the new flags (--preset/--agentic/--structured/
    --max-tokens/--max-turns) the command keeps the original one-shot behaviour
    (AgentSession + run_oneshot). When ANY of those flags is supplied, the call
    routes through ``lilith_tools.delegate.DelegateSubagentTool`` so the delegation
    gets the full agentic/structured/multi-turn surface and is automatically
    recorded in the orchestration state (delegated -> completada/fallida).
    """
    use_tool_path = bool(preset) or agentic or structured or (max_tokens is not None) or (max_turns is not None)

    if not use_tool_path:
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
        return

    # ── Tool-routed path (tanda 14): DelegateSubagentTool ─────────────────────────────────────────────────────────────────────────────────────────
    # When any delegation flag is set we want the same machinery the
    # orchestrator/REPL already use, so the call lands in orchestration
    # state and supports agentic/structured/max_tokens/max_turns.
    preset_name = (preset or target).strip()
    if not preset_name:
        from .render import render_error
        render_error("delegate: preset o target requerido para rutear por DelegateSubagentTool")
        raise SystemExit(2)

    try:
        from lilith_tools.delegate import DelegateSubagentTool  # type: ignore[import-not-found]
        from lilith_tools.base import ToolResult  # type: ignore[import-not-found]
    except Exception as exc:
        from .render import render_error
        render_error(f"No se pudo cargar DelegateSubagentTool: {exc}")
        raise SystemExit(2)

    kwargs: dict[str, Any] = {
        "preset": preset_name,
        "prompt": text,
        "agentic": agentic,
        "structured": structured,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = int(max_tokens)
    if max_turns is not None:
        kwargs["max_turns"] = int(max_turns)
    # --model only applies to the one-shot path; the tool derives the
    # model from the preset YAML. We surface this as a warning rather
    # than silently ignoring it, so the user knows.
    if model:
        from .render import console
        console.print(
            "[warning]--model se ignora al rutear por DelegateSubagentTool "
            "(el modelo viene del preset)[/]"
        )

    result = DelegateSubagentTool().execute(**kwargs)
    # Duck-typed: any object with success/data/error attributes works.
    # The renderer is defensive and falls back gracefully if a field is missing.
    if not hasattr(result, "success") or not hasattr(result, "data"):
        from .render import render_error
        render_error(f"DelegateSubagentTool devolvió un resultado inesperado: {type(result).__name__}")
        raise SystemExit(2)

    _render_delegate_tool_result(preset_name, result)
    if not result.success:
        raise SystemExit(1)


def _render_delegate_tool_result(preset_name: str, result: Any) -> None:
    """Print a DelegateSubagentTool ToolResult in a CLI-friendly way.

    One-shot path: prints the content (or structured JSON pretty-printed)
    plus a footer with files_written / turns_used / usage when present.
    Agentic path: prints content plus the same footer; files_written is
    always populated and turns_used/partial flag are surfaced.
    """
    from .render import console, render_error

    data = result.data if isinstance(result.data, dict) else {}

    if not result.success:
        err = result.error or "delegación fallida"
        render_error(f"delegate ({preset_name}): {err}")

    structured_payload = data.get("structured")
    if structured_payload:
        import json as _json
        console.print(_json.dumps(structured_payload, ensure_ascii=False, indent=2))
    else:
        content = data.get("content") or data.get("raw_content") or ""
        if content:
            console.print(content)

    # Footer: files_written / turns_used / usage.
    files_written = data.get("files_written") or []
    turns_used = data.get("turns_used")
    usage = data.get("usage") or {}
    footer: list[str] = []
    if files_written:
        footer.append(f"files_written={len(files_written)}")
        for fw in files_written[:5]:
            footer.append(f"  - {fw}")
        if len(files_written) > 5:
            footer.append(f"  ... (+{len(files_written) - 5} mas)")
    if turns_used is not None:
        footer.append(f"turns_used={turns_used}")
    if usage:
        prompt_t = usage.get("prompt_tokens")
        completion_t = usage.get("completion_tokens")
        if prompt_t is not None or completion_t is not None:
            footer.append(
                f"usage(prompt={prompt_t or 0}, completion={completion_t or 0})"
            )
    if footer:
        console.print("[dim]" + " | ".join(footer) + "[/]")



# ── Doctor (healthcheck) ────────────────────────────────────────────


# Default cost ceiling for the per-provider 1-token ping. Same rationale
# as ``/subagents test``: small enough to be cheap on every provider,
# large enough to leave room for reasoning_content on Kimi / DeepSeek /
# GLM-5.1 (which burn the budget on chain-of-thought).
_DOCTOR_PING_MAX_TOKENS = 64

# Hard wall-clock cap on the whole ping. Even though the provider may
# have its own per-call timeout, the doctor never blocks longer than
# this so a single bad provider cannot stall the CLI.
_DOCTOR_PING_TIMEOUT_SECONDS = 15.0


def _check_config_parses() -> dict[str, str]:
    """Return one ``{check, status, message}`` row for config.yaml."""
    from .config import load_config

    try:
        load_config()
    except Exception as exc:
        return {
            "check": "config.yaml",
            "status": "error",
            "message": f"no se pudo parsear: {exc}",
        }
    return {
        "check": "config.yaml",
        "status": "ok",
        "message": "config.yaml parsea correctamente",
    }


def _check_provider_keys(cfg: Any) -> list[dict[str, str]]:
    """For each provider profile, check that an API key is resolvable.

    "Resoluble" means the value is non-empty AFTER substitution — we
    never print the value itself, only its presence. Profiles that
    don't declare an API key (e.g. a local-only setup) are reported
    as ``warn`` so the operator can see them but the doctor doesn't
    fail.
    """
    rows: list[dict[str, str]] = []
    providers = (cfg.providers or {}) if cfg else {}
    if not providers:
        return [{
            "check": "API keys",
            "status": "warn",
            "message": "no hay providers declarados en config.yaml",
        }]
    for name, profile in providers.items():
        # ``profile`` may be a Pydantic model or a SimpleNamespace in
        # tests; ``getattr`` keeps us agnostic.
        raw = getattr(profile, "api_key", None)
        if raw is None or not str(raw).strip():
            rows.append({
                "check": f"api_key:{name}",
                "status": "warn",
                "message": f"provider '{name}' sin api_key declarada",
            })
            continue
        # We never echo the key value. We only confirm it is non-empty
        # AND that, if it's a ${VAR} reference, the env var is set.
        # ``load_config`` performs the substitution; if the result is
        # the literal "${VAR}" string, the env var is missing.
        if str(raw).startswith("${") and str(raw).endswith("}"):
            var = str(raw)[2:-1]
            present = bool(os.environ.get(var))
            rows.append({
                "check": f"api_key:{name}",
                "status": "ok" if present else "error",
                "message": (
                    f"provider '{name}' -> env {var} presente"
                    if present
                    else f"provider '{name}' -> env {var} NO esta definida"
                ),
            })
            continue
        # Key is a literal non-empty value.
        rows.append({
            "check": f"api_key:{name}",
            "status": "ok",
            "message": f"provider '{name}' tiene api_key configurada",
        })
    return rows


async def _ping_one_provider(
    name: str, profile: Any, *, parent_cfg: Any
) -> dict[str, Any]:
    """Run a single tiny ``complete()`` against one provider profile.

    Returns a row dict (``check``, ``status``, ``message``, ``latency_ms``)
    so the doctor can surface a uniform table; reuses the per-call
    config-building idiom from ``/subagents test`` so behaviour matches
    production.
    """
    import time as _time

    from .providers import LLMProviderWrapper

    row: dict[str, Any] = {
        "check": f"ping:{name}",
        "status": "error",
        "message": "",
        "latency_ms": 0,
    }
    try:
        # Pydantic v2 models expose ``model_copy(deep=True)``; test
        # fixtures (and any non-Pydantic config in the wild) only need a
        # shallow ``copy.copy`` because we only mutate top-level
        # ``provider`` / ``model`` / ``max_tokens`` attributes before
        # handing the object to the wrapper.
        copier = getattr(parent_cfg, "model_copy", None)
        if callable(copier):
            local_cfg = copier(deep=True)
        else:
            local_cfg = copy.copy(parent_cfg)
        local_cfg.provider = name
        local_cfg.model = (
            getattr(profile, "model", None) or parent_cfg.model
        )
        if getattr(profile, "max_tokens", None) is not None:
            local_cfg.max_tokens = profile.max_tokens
        wrapper = LLMProviderWrapper(local_cfg)
    except Exception as exc:
        row["message"] = f"init: {type(exc).__name__}: {exc}"
        return row
    t0 = _time.perf_counter()
    try:
        response = await wrapper.complete(
            [{"role": "user", "content": "PONG"}],
            tools=None,
            max_tokens=_DOCTOR_PING_MAX_TOKENS,
        )
    except Exception as exc:
        row["message"] = f"{type(exc).__name__}: {exc}"
        return row
    finally:
        try:
            await wrapper.close()
        except Exception:
            pass

    elapsed_ms = int((_time.perf_counter() - t0) * 1000)
    row["latency_ms"] = elapsed_ms
    content = response.get("content") or ""
    reasoning = response.get("reasoning_content") or ""
    if content or reasoning:
        row["status"] = "ok"
        row["message"] = (
            f"responde en {elapsed_ms}ms "
            f"({len(content)}c visibles, {len(reasoning)}c reasoning)"
        )
    else:
        row["status"] = "warn"
        row["message"] = f"respondio en {elapsed_ms}ms pero sin contenido"
    return row


async def _run_provider_pings(cfg: Any) -> list[dict[str, str]]:
    """Ping every provider profile in parallel with a hard ceiling."""
    providers = (cfg.providers or {}) if cfg else {}
    if not providers:
        return [{
            "check": "ping",
            "status": "warn",
            "message": "no hay providers para pinguear",
        }]

    async def _guarded(name: str) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                _ping_one_provider(name, providers[name], parent_cfg=cfg),
                timeout=_DOCTOR_PING_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return {
                "check": f"ping:{name}",
                "status": "error",
                "message": f"timeout {_DOCTOR_PING_TIMEOUT_SECONDS:.0f}s",
                "latency_ms": int(_DOCTOR_PING_TIMEOUT_SECONDS * 1000),
            }
        except Exception as exc:
            return {
                "check": f"ping:{name}",
                "status": "error",
                "message": f"{type(exc).__name__}: {exc}",
                "latency_ms": 0,
            }

    rows = await asyncio.gather(*(_guarded(n) for n in providers))
    out: list[dict[str, str]] = []
    for r in rows:
        out.append({
            "check": r["check"],
            "status": r["status"],
            "message": r["message"],
        })
    return out


def _check_mcp_servers(cfg: Any) -> list[dict[str, str]]:
    """Try to start every declared MCP server.

    Mirrors the boot-time path: hand the configs to
    :class:`MCPClientManager` and let ``start_all`` decide which
    subprocesses it can launch. A broken server is reported as ``error``
    on its own row; servers in ``disabled`` state are reported as
    ``ok`` (the operator opted out).
    """
    rows: list[dict[str, str]] = []
    # Accept both the real Pydantic ``YggdrasilConfig`` (which exposes
    # ``effective_mcp_servers()``) and test fixtures that only carry the
    # raw ``mcp_servers`` attribute as a dict. ``getattr`` keeps the
    # helper agnostic without forcing every test to spin up the full
    # config model.
    if cfg is None:
        servers: dict[str, Any] = {}
    else:
        getter = getattr(cfg, "effective_mcp_servers", None)
        if callable(getter):
            servers = getter() or {}
        else:
            servers = getattr(cfg, "mcp_servers", None) or {}
    if not servers:
        rows.append({
            "check": "mcp_servers",
            "status": "ok",
            "message": "sin servidores declarados",
        })
        return rows
    try:
        from lilith_tools.mcp_client import MCPClientManager
    except Exception as exc:
        rows.append({
            "check": "mcp_servers",
            "status": "error",
            "message": f"MCPClientManager no disponible: {exc}",
        })
        return rows
    try:
        def _server_payload(c: Any) -> dict[str, Any]:
            """Best-effort serialisation of an ``MCPServerConfig``-like object.

            Pydantic v2 models expose ``model_dump()``; test fixtures (and
            any non-Pydantic config in the wild) fall back to ``dict(c)``
            or ``vars(c)``.
            """
            dumper = getattr(c, "model_dump", None)
            if callable(dumper):
                return dumper()
            if isinstance(c, dict):
                return dict(c)
            return dict(vars(c))
        manager = MCPClientManager(
            {n: _server_payload(c) for n, c in servers.items()}
        )
        statuses = manager.start_all()
    except Exception as exc:
        rows.append({
            "check": "mcp_servers",
            "status": "error",
            "message": f"start_all fallo: {exc}",
        })
        return rows
    for name, status in statuses.items():
        if status == "ok":
            rows.append({
                "check": f"mcp:{name}",
                "status": "ok",
                "message": "arrancado",
            })
        elif status == "disabled":
            rows.append({
                "check": f"mcp:{name}",
                "status": "ok",
                "message": "deshabilitado (enabled=false)",
            })
        else:
            rows.append({
                "check": f"mcp:{name}",
                "status": "error",
                "message": str(status),
            })
    return rows


def _check_memory_db(cfg: Any) -> dict[str, str]:
    """Open the memory DB (creating the parent dir if needed) and run
    a trivial SELECT to confirm it's a real SQLite file.
    """
    import sqlite3

    try:
        db_path = Path(getattr(cfg.memory, "db_path", "~/.yggdrasil/memory.db"))
        db_path = db_path.expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {
            "check": "memory.db",
            "status": "error",
            "message": f"no se pudo preparar ruta: {exc}",
        }
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
    except Exception as exc:
        return {
            "check": "memory.db",
            "status": "error",
            "message": f"sqlite error: {exc}",
        }
    return {
        "check": "memory.db",
        "status": "ok",
        "message": f"accesible en {db_path}",
    }


def _check_package_versions() -> list[dict[str, str]]:
    """Report the installed version of every lilith-* package."""
    import importlib.metadata as md

    rows: list[dict[str, str]] = []
    for dist in sorted(md.distributions(), key=lambda d: d.metadata["Name"] or ""):
        name = dist.metadata["Name"] or ""
        if not name.startswith("lilith-"):
            continue
        try:
            version = dist.version
        except Exception as exc:
            rows.append({
                "check": f"pkg:{name}",
                "status": "warn",
                "message": f"version no resoluble: {exc}",
            })
            continue
        rows.append({
            "check": f"pkg:{name}",
            "status": "ok",
            "message": version,
        })
    if not rows:
        rows.append({
            "check": "pkg",
            "status": "warn",
            "message": "ningun paquete lilith-* instalado",
        })
    return rows


def run_doctor_checks() -> list[dict[str, str]]:
    """Run every diagnostic check and return the flat row list.

    Public entry point for tests; the ``doctor`` command below is a
    thin wrapper that renders these rows as a Rich table and chooses an
    exit code.
    """
    rows: list[dict[str, str]] = []
    rows.append(_check_config_parses())

    # All other checks need a parsed config; if config.yaml is broken
    # we still report the rest, but mark each as ``error`` since the
    # config is required to know what to check.
    cfg: Any = None
    try:
        from .config import load_config

        cfg = load_config()
    except Exception:
        cfg = None

    if cfg is None:
        rows.append({
            "check": "providers",
            "status": "error",
            "message": "config invalida; resto de chequeos no aplicables",
        })
        return rows

    rows.extend(_check_provider_keys(cfg))

    # Pings are async; the sync ``run_doctor_checks`` runs them in a
    # fresh event loop. Tests that need finer control can call
    # ``_run_provider_pings`` directly.
    try:
        ping_rows = asyncio.run(_run_provider_pings(cfg))
    except Exception as exc:
        ping_rows = [{
            "check": "ping",
            "status": "error",
            "message": f"asyncio fallo: {exc}",
        }]
    rows.extend(ping_rows)

    rows.extend(_check_mcp_servers(cfg))
    rows.append(_check_memory_db(cfg))
    rows.extend(_check_package_versions())
    return rows


@app.command
def doctor() -> None:
    """Chequeo de salud: config, providers, MCP, memory, paquetes.

    Imprime una tabla con una fila por chequeo (estado: ok/warn/error)
    y sale con codigo 0 si todo esta bien, 1 si algun chequeo reporta
    error. Las API keys nunca se imprimen: solo se confirma su
    presencia o el nombre de la variable de entorno esperada.
    """
    from rich.table import Table

    from .render import console

    rows = run_doctor_checks()
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Message")
    for r in rows:
        status = r.get("status", "?")
        if status == "ok":
            status_cell = "[status.ok]ok[/]"
        elif status == "warn":
            status_cell = "[warning]warn[/]"
        else:
            status_cell = "[error]error[/]"
        table.add_row(
            str(r.get("check", "?")),
            status_cell,
            str(r.get("message", "")),
        )
    console.print(table)
    if any(r.get("status") == "error" for r in rows):
        raise SystemExit(1)


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

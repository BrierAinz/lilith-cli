"""Operator console — spawn slice of the Lilith operator console (plan-29 A3).

Adds the ``lilith spawn`` command on top of the A1/A2 ``ops`` modules:

- ``lilith spawn <agente> "<tarea>" [--channel ...] [--timeout S] [--dry-run]``
  Resolves a Vanaheim :class:`AgentCard` to a
  :class:`~lilith_orchestrator.subagents.SubAgentDefinition`, runs a real
  ``opencode run`` subprocess against the chosen channel's model, and
  records the run as a turn on the cross-cutting state (goal + handoff
  + bus publish + audit entry).

The module is intentionally lean — it delegates every primitive:

- card loading → :class:`lilith_skills.agent_cards.AgentCardLoader`
  (same loader :mod:`lilith_orchestrator.card_bridge` uses)
- card → subagent → :func:`lilith_orchestrator.card_bridge.card_to_subagent`
- bus pub/sub → :class:`lilith_core.bus.LilithBus`
- goals / handoffs / audit → :class:`lilith_skills.cross_context.CrossContext`
  (constructor receives the ``.ygg`` dir itself, **not** the project root)

``_CHANNELS`` is a **module-level dict** so tests can monkeypatch it with
a fake command (a Python ``-c`` shim) without ever touching a real LLM
provider — see ``Asgard/lilith-cli/tests/test_ops_spawn.py``.

Design choices specific to this slice:

- The spawn is **a turn of the pantheon**, not a one-shot. Even a 1-turn
  invocation creates a goal so other agents (Skadi's cron loop, the
  upcoming ``ygg do`` orchestrator, …) can resume / inspect it later.
- ``--dry-run`` prints the resolved :class:`SubAgentDefinition` AND the
  exact command that *would* be executed, then exits without touching
  the filesystem, the bus, or any subprocess. Tests rely on this to
  assert wiring without side effects.
- The log file is written **before** the subprocess starts (header
  metadata) so a crash in the subprocess still leaves a trail on disk.

Timeout handling: the subprocess streams straight into the log file (no
pipes — inherited pipe handles are how the first real smoke deadlocked a
plain ``subprocess.run(capture_output=True, timeout=...)`` on Windows:
opencode's grandchildren kept the pipes open and ``communicate()`` never
returned). On timeout the whole tree is killed via ``taskkill /T /F``
(Windows) or ``proc.kill()`` (POSIX).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter
from lilith_skills.agent_cards import AgentCardLoader

from lilith_core.bus import LilithBus
from lilith_orchestrator.card_bridge import card_to_subagent


__all__ = [
    "_CHANNELS",
    "run_spawn",
    "run_spawn_status",
    "run_spawn_kill",
    "spawn",
    "spawn_app",
    "spawns_log_dir",
]


# ── Channels ──────────────────────────────────────────────────────────────
#
# Module-level dict (intentionally monkeypatcheable in tests).  Keys are
# the short channel names exposed on the CLI; values are the model ids
# the opencode CLI expects via ``-m``.
# ─────────────────────────────────────────────────────────────────────────


_CHANNELS: dict[str, str | list[str]] = {
    # Lilith's main session itself runs on Sakana Fugu Ultra (see the
    # default config in ``lilith_cli/config.py``). Spawned sub-agents
    # are routed through the opencode CLI, which exposes multiple
    # models under different channels. The two defaults below match
    # the per-provider profiles declared in the default config:
    #   * minimax      → MiniMax-M3 (Anthropic-compatible)
    #   * opencode-go  → glm-5.2 (OpenCode Go gateway, hard-pinned)
    # ``sakana`` is still available so a sub-agent can opt into the
    # same model the orchestrator is running, when a task needs it.
    "minimax": "minimax/MiniMax-M3",
    "opencode-go": "opencode-go/glm-5.2",
    "sakana": "sakana/fugu-ultra",
}


def _resolve_channel_command(
    channel_value: str | list[str],
    *,
    opencode_bin: str | None,
    prompt: str,
) -> tuple[list[str], str]:
    """Resolve a channel entry into ``(cmd, model_label)``.

    Two shapes are accepted:

    - **string** (production): treated as a model id. The returned cmd
      invokes ``opencode run <prompt> -m <model>``.
    - **list[str]** (tests): treated as a **full argv override** that
      replaces the opencode call entirely — used by test fixtures to
      drive a ``python -c`` shim without ever invoking an LLM.

    Returns ``(argv, model_label_for_bus_publish)``.
    """
    if isinstance(channel_value, list):
        # Full-argv override (test seam). ``model_label`` becomes the
        # repr of the override so the bus message stays informative.
        return list(channel_value), "<override>"
    if opencode_bin is None:
        raise FileNotFoundError("opencode binary not on PATH")
    return [opencode_bin, "run", prompt, "-m", channel_value], channel_value


# ── Paths ────────────────────────────────────────────────────────────────


def _resolve_yggdrasil_root() -> Path:
    """Find the Yggdrasil workspace root (same helper used in :mod:`ops`)."""
    from lilith_cli.main import _resolve_yggdrasil_root as _real_resolve

    return _real_resolve()


def default_bus_db_path() -> Path:
    """Resolve the default LilithBus DB path used by spawn."""
    return _resolve_yggdrasil_root() / ".ygg" / "lilith_bus.db"


def spawns_log_dir() -> Path:
    """Return ``<ROOT>/.ygg/spawns/`` (created lazily by callers)."""
    return _resolve_yggdrasil_root() / ".ygg" / "spawns"


# ── FUGU_API_KEY resolver (Windows) ──────────────────────────────────────
#
# The sakana channel needs ``FUGU_API_KEY`` in the subprocess env. We
# read HKCU\Environment as a fallback when the variable is missing from
# the parent env, so users only have to set it in the Windows registry
# (one place) instead of editing every shell launcher.
# ─────────────────────────────────────────────────────────────────────────


def _read_fugu_key_from_windows_registry() -> str | None:
    """Best-effort read of ``FUGU_API_KEY`` from ``HKCU\\Environment``.

    Returns ``None`` on any failure (no winreg, key absent, value missing,
    non-Windows host). Never raises.
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg  # stdlib, lazy-imported (test hosts may not be Windows)

        with winreg.OpenKey(  # type: ignore[attr-defined]
            winreg.HKEY_CURRENT_USER,  # type: ignore[attr-defined]
            r"Environment",
        ) as hkey:
            value, _regtype = winreg.QueryValueEx(hkey, "FUGU_API_KEY")  # type: ignore[attr-defined]
    except (OSError, FileNotFoundError, ImportError):
        return None
    if isinstance(value, str) and value:
        return value
    return None


def _build_subprocess_env(channel: str) -> dict[str, str]:
    """Return the env passed to the opencode subprocess.

    The sakana channel additionally needs ``FUGU_API_KEY``; we copy the
    parent env, then inject the key from the Windows registry if it's
    not already present.
    """
    env = os.environ.copy()
    if channel == "sakana" and not env.get("FUGU_API_KEY"):
        fugu = _read_fugu_key_from_windows_registry()
        if fugu:
            env["FUGU_API_KEY"] = fugu
    return env


def _resolve_opencode_bin() -> str | None:
    """Locate the opencode executable, bypassing npm's .CMD shim.

    ``shutil.which("opencode")`` resolves to ``opencode.CMD`` on a npm
    install, and cmd.exe batch argument re-parsing mangles multi-line
    prompt arguments (verified 2026-07-04: the ``-m`` flag after a
    multi-line prompt was silently dropped, so every spawn ran on the
    default build agent instead of the requested channel). Call the
    real ``opencode.exe`` the shim wraps whenever we can find it.
    """
    exe = shutil.which("opencode")
    if exe and exe.lower().endswith((".cmd", ".bat")):
        real = Path(exe).parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
        if real.exists():
            return str(real)
    return exe


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill *proc* and every descendant it spawned.

    On Windows a plain ``proc.kill()`` only terminates the direct child;
    opencode's own children survive it (verified 2026-07-04: a timed-out
    spawn's tree kept running for 12+ minutes until killed by hand).
    ``taskkill /T /F`` walks the whole tree instead.
    """
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover — last resort
        pass


# ── Prompt builder ───────────────────────────────────────────────────────


def _build_spawn_prompt(card_system_prompt: str, task: str) -> str:
    """Compose the final prompt handed to the opencode subprocess.

    Layout:

      <card system prompt>
      ─── ROLE ───
      sub-agent under Lilith (the orchestrator) — execute and report
      ─── TASK ───
      <task verbatim>
      ─── RULES ───
      cwd = repo root
      temp files inside the repo
      no git add/commit (operator commits explicitly)
      final report via stdout
    """
    role = (
        "─── ROLE ───\n"
        "You are a SUB-AGENT spawned by Lilith, the Yggdrasil "
        "orchestrator (which itself runs on Sakana Fugu Ultra). "
        "Execute the task and report back — do not try to orchestrate "
        "other sub-agents yourself."
    )
    rules = (
        "─── RULES ───\n"
        "1. Your working directory is the Yggdrasil repository root.\n"
        "2. Place any temporary files inside the repository (e.g. "
        "``./tmp_<agent>.txt``); never write to the user's home or "
        "system temp.\n"
        "3. Do NOT run ``git add`` or ``git commit`` — the operator "
        "reviews and commits manually.\n"
        "4. End your turn with a concise final report printed to stdout."
    )
    parts = [
        card_system_prompt.strip(),
        role,
        "─── TASK ───",
        task.strip(),
        rules,
    ]
    return "\n\n".join(p for p in parts if p)


# ── spawn core ───────────────────────────────────────────────────────────
#
# ``run_spawn`` is the headless body shared by the ``lilith spawn`` CLI
# handler and by the ``lilith work`` queue consumer (plan-29 slice C).
# It returns the exit code as an ``int`` instead of raising ``SystemExit``,
# so other call sites can decide how to react (the CLI raises; ``work``
# releases the bus message and moves on).
#
# Return codes (mirrors the convention of the original ``spawn()``):
#   0   success (or dry-run completed)
#   1   missing agent / missing opencode bin / missing yaml
#   2   unknown channel
#   124 subprocess timed out
#   *   the subprocess's own exit code (cast to int, falling back to 1)
# ─────────────────────────────────────────────────────────────────────────


def run_spawn(
    agent: str,
    task: str,
    *,
    channel: str = "minimax",
    timeout: int = 300,
    db: Path | None = None,
    repo_root: Path | None = None,
    console: object | None = None,
    dry_run: bool = False,
) -> int:
    """Headless spawn body — see module docstring for return codes.

    Parameters mirror the ``spawn`` CLI handler 1:1 so the wrapper can
    delegate cleanly without re-parsing arguments. ``console`` is
    injected by the caller (Cyclopts handlers pass a ``rich.Console``;
    the queue consumer can pass ``None`` to stay silent on success and
    still use rich for error frames via :func:`_silent_console`).
    """
    if console is None:
        from rich.console import Console as _Console

        console = _Console()

    # ── validate channel ─────────────────────────────────────────────
    if channel not in _CHANNELS:
        available = ", ".join(sorted(_CHANNELS)) or "(none)"
        console.print(
            f"[error]✗ Unknown channel '{channel}'.[/]\n[dim]Available channels: {available}[/]"
        )
        return 2
    channel_value = _CHANNELS[channel]

    # ── resolve workspace ────────────────────────────────────────────
    root = Path(repo_root).resolve() if repo_root is not None else _resolve_yggdrasil_root()
    bus_path = Path(db).resolve() if db is not None else default_bus_db_path()
    log_dir = root / ".ygg" / "spawns"

    # ── load agent card ──────────────────────────────────────────────
    try:
        cards = AgentCardLoader.from_vanaheim(root).list_agents()
    except FileNotFoundError as exc:
        console.print(f"[error]✗ {exc}[/]")
        return 1
    match = next((c for c in cards if c.name.casefold() == agent.casefold()), None)
    if match is None:
        available = ", ".join(c.name for c in cards) or "(none)"
        console.print(
            f"[error]✗ No agent named '{agent}'.[/]\n[dim]Available agents: {available}[/]"
        )
        return 1

    # ── resolve SubAgentDefinition ───────────────────────────────────
    defn = card_to_subagent(match)
    full_prompt = _build_spawn_prompt(defn.system_prompt, task)

    # ── resolve opencode executable (only needed for string channels) ──
    needs_opencode = isinstance(channel_value, str)
    opencode_bin = _resolve_opencode_bin() if needs_opencode else None
    if needs_opencode and opencode_bin is None:
        console.print(
            "[error]✗ 'opencode' executable not found on PATH.[/]\n"
            "[dim]Install opencode or adjust PATH before spawning.[/]"
        )
        return 1

    try:
        cmd, model = _resolve_channel_command(
            channel_value,
            opencode_bin=opencode_bin,
            prompt=full_prompt,
        )
    except FileNotFoundError:
        console.print(
            "[error]✗ 'opencode' executable not found on PATH.[/]\n"
            "[dim]Install opencode or adjust PATH before spawning.[/]"
        )
        return 1

    # ── --dry-run ────────────────────────────────────────────────────
    if dry_run:
        _render_dry_run(console, match, defn, channel, model, cmd, root, bus_path, log_dir)
        return 0

    # ── side effects: goal + audit + bus publish (start) ─────────────
    log_dir.mkdir(parents=True, exist_ok=True)
    bus_path.parent.mkdir(parents=True, exist_ok=True)

    cross = _open_cross_context(root)

    goal = cross.goals.create(
        name=f"spawn:{match.name}",
        project=str(root.name),
        description=task,
        quota_max_calls=1,
    )
    cross.audit.append(
        policy="spawn",
        agent=match.name,
        hook_type="spawn.start",
        action="spawn",
        message=f"spawning {match.name} via {channel}",
        data={
            "channel": channel,
            "model": model,
            "goal_id": goal.id,
            "log_dir": str(log_dir),
        },
    )

    bus = LilithBus(bus_path)
    try:
        bus.publish(
            f"spawn.{match.name.casefold()}",
            {
                "goal_id": goal.id,
                "channel": channel,
                "model": model,
                "task": task,
                "started_at": time.time(),
            },
            role=match.role or None,
        )
    finally:
        bus.close()

    # ── log file (header written BEFORE the subprocess runs) ─────────
    ts_label = time.strftime("%Y%m%dT%H%M%S")
    log_path = log_dir / f"{ts_label}-{match.name.casefold()}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = _build_subprocess_env(channel)
    console.print(
        f"[bold realm]Spawning[/] [cyan]{match.name}[/] "
        f"via [dim]{channel}[/] ([dim]{model}[/]) → [dim]{log_path}[/]"
    )

    # The subprocess streams straight into the log file: no pipes means
    # no handle for opencode's grandchildren to inherit and hold open
    # (which would block a ``communicate()`` forever after a timeout
    # kill), and the log can be tailed live while the agent works.
    with log_path.open("w", encoding="utf-8") as log_fh:
        log_fh.write(
            f"# lilith spawn\n"
            f"agent: {match.name}\n"
            f"channel: {channel}\n"
            f"model: {model}\n"
            f"goal_id: {goal.id}\n"
            f"timeout: {timeout}s\n"
            f"---\n"
            f"## output (stdout+stderr)\n"
        )
        log_fh.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            exit_code = proc.wait(timeout=max(1, timeout))
            timed_out = False
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            exit_code = -1
            timed_out = True
        log_fh.write(f"\n---\ntimed_out: {timed_out}\nexit_code: {exit_code}\n")

    # ── side effects: turn + handoff + bus publish (done) + audit ───
    goal.add_turn(
        match.name,
        "spawn-run",
        evidence=f"exit={exit_code} timed_out={timed_out}",
        channel=channel,
        model=model,
        log=str(log_path),
    )
    cross.goals.save(goal)
    cross.handoffs.write_for(goal)
    cross.audit.append(
        policy="spawn",
        agent=match.name,
        hook_type="spawn.done",
        action="spawn",
        message=f"spawn {match.name} finished exit={exit_code} timed_out={timed_out}",
        data={
            "goal_id": goal.id,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "log": str(log_path),
        },
    )

    bus = LilithBus(bus_path)
    try:
        bus.publish(
            f"spawn.{match.name.casefold()}.done",
            {
                "goal_id": goal.id,
                "channel": channel,
                "model": model,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "log": str(log_path),
            },
            role=match.role or None,
        )
    finally:
        bus.close()

    # ── final report ────────────────────────────────────────────────
    if timed_out:
        console.print(
            f"[error]✗ Spawn of {match.name} timed out after {timeout}s[/] (see [dim]{log_path}[/])"
        )
        return 124

    if exit_code != 0:
        console.print(
            f"[error]✗ Spawn of {match.name} exited {exit_code}[/] (see [dim]{log_path}[/])"
        )
        return int(exit_code) or 1

    console.print(
        f"[success]✓ Spawn of {match.name} completed (exit=0)[/] — log: [dim]{log_path}[/]"
    )
    return 0


# ── spawn status / kill (operator dashboard) ───────────────────────────


def run_spawn_status(
    *,
    db: Path | None = None,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return active subagent spawns by pairing ``spawn.{agent}`` start and
    ``spawn.{agent}.done`` events on the bus.

    A spawn is considered active when a start event exists whose ``goal_id``
    has not yet been observed in a matching ``.done`` event.  Returns a list
    of dicts with ``goal_id``, ``agent``, ``channel``, ``model``, ``task``,
    ``started_at`` and ``topic``.
    """
    root = Path(repo_root).resolve() if repo_root is not None else _resolve_yggdrasil_root()
    bus_path = Path(db).resolve() if db is not None else default_bus_db_path()
    if not bus_path.exists():
        return []

    bus = LilithBus(bus_path)
    try:
        msgs = bus.poll("spawn.**", limit=200)
    finally:
        bus.close()

    starts: dict[str, dict[str, Any]] = {}
    dones: set[str] = set()
    for m in msgs:
        payload = m.payload if isinstance(m.payload, dict) else {}
        goal_id = payload.get("goal_id")
        if goal_id is None:
            continue
        key = str(goal_id)
        if m.topic.endswith(".done"):
            dones.add(key)
        elif "." in m.topic:
            starts[key] = {
                "goal_id": goal_id,
                "agent": m.topic.split(".")[1],
                "channel": payload.get("channel"),
                "model": payload.get("model"),
                "task": payload.get("task", ""),
                "started_at": payload.get("started_at"),
                "topic": m.topic,
            }

    active = [info for gid, info in starts.items() if gid not in dones]
    active.sort(key=lambda x: x.get("started_at") or 0, reverse=True)
    return active


def run_spawn_kill(
    agent_name: str,
    *,
    console: object | None = None,
) -> int:
    """Request cancellation of an active spawn.

    The current spawn core streams subprocess output straight to a log file
    and does not persist PIDs, so a hard kill is not available.  This helper
    returns ``1`` and reports that limitation; callers may override it with
    platform-specific process tracking in the future.
    """
    if console is None:
        from rich.console import Console as _Console

        console = _Console()

    console.print(
        f"[warning]⚠ Kill de spawn '{agent_name}' no implementado "
        f"(sin tracking de PID)[/]"
    )
    return 1


# ── spawn command (CLI handler) ──────────────────────────────────────────


spawn_app = App(
    name="spawn",
    help="Spawn a Vanaheim agent as a real subprocess (lilith 4.0).",
)


@spawn_app.default
def spawn(
    agent: Annotated[
        str,
        Parameter(help="Vanaheim agent name (case-insensitive; e.g. 'Odin', 'hela')"),
    ],
    task: Annotated[
        str,
        Parameter(help="The task / prompt handed verbatim to the sub-agent"),
    ],
    channel: Annotated[
        str,
        Parameter(
            name="--channel",
            help=(
                "Execution channel (model provider); one of: "
                + ", ".join(sorted(_CHANNELS))
                + ". Default 'minimax' is a sub-agent profile; the "
                "orchestrator (Lilith) itself runs on 'sakana'."
            ),
        ),
    ] = "minimax",
    timeout: Annotated[
        int,
        Parameter(
            name="--timeout",
            help="Wall-clock cap for the subprocess, in seconds",
        ),
    ] = 300,
    dry_run: Annotated[
        bool,
        Parameter(
            name="--dry-run",
            help="Print the resolved definition + command and exit (no side effects)",
        ),
    ] = False,
    db: Annotated[
        Path | None,
        Parameter(name="--db", help="Override bus DB path"),
    ] = None,
    repo_root: Annotated[
        Path | None,
        Parameter(name="--repo-root", help="Override Yggdrasil repo root"),
    ] = None,
) -> None:
    """Spawn *agent* on *task* via the opencode CLI; records turn + handoff + bus event.

    Thin wrapper around :func:`run_spawn` — exists to wire Cyclopts
    parameters and translate the int return code into the conventional
    ``SystemExit`` that POSIX shells rely on for control flow.
    """
    from rich.console import Console

    code = run_spawn(
        agent=agent,
        task=task,
        channel=channel,
        timeout=timeout,
        db=db,
        repo_root=repo_root,
        console=Console(),
        dry_run=dry_run,
    )
    if code != 0:
        raise SystemExit(code)


def _open_cross_context(root: Path):
    """Open a :class:`CrossContext` rooted at the *actual* ``.ygg`` dir.

    Mirrors ``.ygg/context_cli.py::_cx`` — the constructor expects the
    ``.ygg`` directory itself, not the project root.
    """
    from lilith_skills.cross_context import CrossContext

    ygg_dir = root / ".ygg"
    return CrossContext(ygg_dir)


def _render_dry_run(
    console: object,
    card: object,
    defn: object,
    channel: str,
    model: str,
    cmd: list[str],
    root: Path,
    bus_path: Path,
    log_dir: Path,
) -> None:
    """Render the ``--dry-run`` preview (definition + command, no effects)."""
    from rich.table import Table

    console.print("[bold realm]--dry-run[/]  [dim](no side effects)[/]")
    console.print()

    info = Table(
        show_header=False,
        title="[bold realm]Resolved SubAgentDefinition[/]",
        expand=False,
    )
    info.add_column("Field", style="dim")
    info.add_column("Value")
    info.add_row("agent (card)", card.name)
    info.add_row("role", card.role or "—")
    info.add_row("level", str(card.level))
    info.add_row("agent_type", defn.agent_type)
    info.add_row("model_preference", defn.model_preference or "—")
    info.add_row("when_to_use", (defn.when_to_use or "")[:120])
    info.add_row("allowed_tools", ", ".join(defn.allowed_tools) or "—")
    info.add_row("disallowed_tools", ", ".join(defn.disallowed_tools) or "—")
    info.add_row("tags", ", ".join(defn.tags) or "—")
    info.add_row("channel", channel)
    info.add_row("model (effective)", model)
    console.print(info)

    console.print()
    console.print("[bold realm]Effective command[/]")
    console.print(f"[dim]  cwd:    {root}[/]")
    console.print(f"[dim]  log:    {log_dir}[/]")
    console.print(f"[dim]  bus:    {bus_path}[/]")
    console.print(f"[dim]  binary: {cmd[0]}[/]")
    # Render the prompt separately so long task prompts don't dominate.
    if len(cmd) >= 5 and cmd[-2] == "-m":
        head = cmd[:3]
        tail = cmd[3:-2]
        model_arg = cmd[-1]
        console.print(
            f"  [cyan]{' '.join(head)}[/] "
            f"[dim]{' '.join(tail) if tail else ''}[/] "
            f"[frost]-m {model_arg}[/]"
        )
    else:
        # List-override channel (test seam): just dump the full argv.
        console.print(f"  [cyan]{' '.join(cmd)}[/]")


# Public alias so tests / other callers can import the entry point by
# name (matches the ``app.command(_spawn_cmd)`` pattern in main.py).
spawn_cmd = spawn

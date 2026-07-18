"""Operator console — queue & work slice of the Lilith operator console (plan-29 C).

Adds the work-driven successor to the retired cron loop (2026-07-04):

- ``lilith queue add "<tarea>" [--role R] [--agent <card>]``
    Publishes a ``queue.task`` message on the LilithBus (anycast by
    ``role``, default ``"worker"``). ``--agent`` pins a specific card so
    the next ``work`` run skips routing; the ``role`` is still the
    claim key (``role`` controls *who* picks the task; ``--agent``
    controls *who* does it).

- ``lilith queue list``
    Tails ``queue.**`` with state (unclaimed / claimed), so the operator
    can see what's pending before kicking off a worker.

- ``lilith work --as <claimer> [--role R] [--once|--watch] [--channel X]
  [--timeout S] [--interval I]``
    ``--once``: ``bus.claim_any(role, claimer)``; empty queue → exit 0.
    Otherwise resolve the agent (``payload["agent"]`` if pinned, else
    :class:`lilith_orchestrator.agent_router.AgentRouter` against the
    14 Vanaheim cards) and run it via :func:`lilith_cli.ops_spawn.run_spawn`
    — the headless body the ``spawn`` CLI shares with this loop. Success
    → ``bus.ack``; failure → ``bus.release`` (so a peer worker can
    retry) + clear message + non-zero exit.
    ``--watch``: loop with ``time.sleep(interval)`` (default 30s);
    Ctrl+C → friendly summary of what was processed.

The router can also be wired via ``--router-factory`` (Callable[[list
[SubAgentDefinition]], AgentRouter]) for deterministic tests — the CLI
surface never exposes this, but ``run_work_once`` accepts it as a kwarg.

No LLM is ever invoked here in tests: ``ops_spawn._CHANNELS`` is the
same monkeypatch seam A3 uses (``python -c`` shim returning 0).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from cyclopts import App, Parameter
from lilith_skills.agent_cards import AgentCardLoader

from lilith_cli.ops_spawn import _CHANNELS, run_spawn
from lilith_core.bus import LilithBus
from lilith_orchestrator.agent_router import AgentRouter
from lilith_orchestrator.card_bridge import card_to_subagent


if TYPE_CHECKING:
    from collections.abc import Callable

    from lilith_orchestrator.subagents import SubAgentDefinition


__all__ = [
    "queue_app",
    "run_queue_add",
    "run_queue_list",
    "run_queue_cancel",
    "run_work_once",
    "work_app",
]


# ── Topic / defaults ──────────────────────────────────────────────────────


QUEUE_TOPIC = "queue.task"
QUEUE_PATTERN = "queue.**"
DEFAULT_ROLE = "worker"
DEFAULT_TIMEOUT = 300
DEFAULT_INTERVAL = 30


# ── Path helpers (mirrors ops / ops_spawn) ───────────────────────────────


def _resolve_yggdrasil_root() -> Path:
    """Find the Yggdrasil workspace root (same helper the other ops modules use)."""
    from lilith_cli.main import _resolve_yggdrasil_root as _real_resolve

    return _real_resolve()


def default_bus_db_path() -> Path:
    """Resolve the default LilithBus DB path used by queue/work."""
    return _resolve_yggdrasil_root() / ".ygg" / "lilith_bus.db"


def _resolve_root(repo_root: Path | None) -> Path:
    return Path(repo_root).resolve() if repo_root is not None else _resolve_yggdrasil_root()


def _open_bus(db: Path | None, root: Path) -> LilithBus:
    """Open a :class:`LilithBus` rooted at ``db`` (defaulting to .ygg/lilith_bus.db)."""
    path = Path(db).resolve() if db is not None else root / ".ygg" / "lilith_bus.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return LilithBus(path)


# ── Routing ──────────────────────────────────────────────────────────────


def _load_card_definitions(root: Path) -> list[SubAgentDefinition]:
    """Load every Vanaheim card as a :class:`SubAgentDefinition`.

    Reuses the same loader :mod:`lilith_cli.ops_spawn` uses so cards
    behave identically across all operator commands. ``FileNotFoundError``
    bubbles up so the caller can report it.
    """
    cards = AgentCardLoader.from_vanaheim(root).list_agents()
    return [card_to_subagent(c) for c in cards]


def _resolve_agent_from_payload(
    payload: dict,
    *,
    route_lookup: Callable[[str], str | None],
) -> tuple[str | None, str | None]:
    """Return ``(agent_name, decision_source)`` for a queue payload.

    - ``payload["agent"]`` (whitespace-stripped, non-empty) if present
      → ``(agent, "pinned")``.
    - else ``route_lookup(payload["task"])`` → ``(agent, "router")`` or
      ``(None, "router-miss")`` when the router finds nothing.
    - whitespace-only pinned values are treated as no-pin and fall
      through to the router (a small robustness win — empty strings
      leaking from the CLI shouldn't silently pin to a phantom agent).

    ``route_lookup`` is a callable returning the agent name (or None).
    Keeping it injectable lets tests bypass the live router.
    """
    pinned = payload.get("agent")
    if pinned:
        stripped = str(pinned).strip()
        if stripped:
            return stripped, "pinned"
        # whitespace-only pinned → fall through to router below.
    decision = route_lookup(str(payload.get("task", "")))
    if decision:
        return decision, "router"
    return None, "router-miss"


def _build_live_route_lookup(definitions: list[SubAgentDefinition]) -> Callable[[str], str | None]:
    """Return a ``task → agent_name`` callable backed by a live AgentRouter."""

    router = AgentRouter(registry=definitions)
    min_score = 0.0  # every scored candidate passes — slice C keeps it permissive

    def lookup(task: str) -> str | None:
        ranked = router.rank(task, limit=1)
        if not ranked:
            return None
        if ranked[0].score < min_score:
            return None
        return ranked[0].agent_type

    return lookup


# ── queue add ────────────────────────────────────────────────────────────


def run_queue_add(
    task: str,
    *,
    role: str = DEFAULT_ROLE,
    agent: str | None = None,
    queued_by: str = "operator",
    db: Path | None = None,
    repo_root: Path | None = None,
    console: object | None = None,
) -> int:
    """Publish a ``queue.task`` message.

    Returns ``0`` on success, ``1`` if the task string is empty (we don't
    enqueue empty prompts).
    """
    if console is None:
        from rich.console import Console as _Console

        console = _Console()

    if not task or not task.strip():
        console.print("[error]✗ task must be a non-empty string[/]")
        return 1

    root = _resolve_root(repo_root)
    bus = _open_bus(db, root)
    try:
        msg_id = bus.publish(
            QUEUE_TOPIC,
            {
                "task": task,
                "agent": agent,
                "queued_by": queued_by,
                "queued_at": time.time(),
            },
            role=role,
        )
    finally:
        bus.close()

    pinned = f" → [cyan]{agent}[/]" if agent else " ([dim]router pick[/])"
    console.print(f"[success]✓ queued[/] [bold realm]id={msg_id}[/]  [dim]role={role}[/]{pinned}")
    return 0


def run_queue_list(
    *,
    db: Path | None = None,
    repo_root: Path | None = None,
    console: object | None = None,
    limit: int = 20,
) -> int:
    """Tail ``queue.**`` with state (unclaimed / claimed). Exit ``0``."""
    if console is None:
        from rich.console import Console as _Console

        console = _Console()

    root = _resolve_root(repo_root)
    bus = _open_bus(db, root)
    try:
        msgs = bus.poll(QUEUE_PATTERN, limit=max(1, limit))
    finally:
        bus.close()

    if not msgs:
        console.print(f"[dim]No pending queue messages in {root / '.ygg' / 'lilith_bus.db'}[/]")
        return 0

    from rich.table import Table

    table = Table(
        show_header=True,
        header_style="bold gold1",
        title=f"[bold realm]Queue tail[/]  [dim]pattern='{QUEUE_PATTERN}'[/]",
        expand=True,
    )
    table.add_column("ID", justify="right", no_wrap=True)
    table.add_column("Topic")
    table.add_column("Role", style="dim")
    table.add_column("Pinned agent", style="cyan")
    table.add_column("Task", overflow="fold")
    table.add_column("State", style="dim")
    table.add_column("At", style="dim")

    for m in msgs:
        state = "[dim]free[/]" if m.claimed_by is None else f"→ [yellow]{m.claimed_by}[/]"
        table.add_row(
            str(m.id),
            m.topic,
            m.role or "—",
            (m.payload.get("agent") or "—") if isinstance(m.payload, dict) else "—",
            (m.payload.get("task", "") if isinstance(m.payload, dict) else ""),
            state,
            m.published_at,
        )

    console.print(table)
    console.print(f"[dim]{len(msgs)} message(s)[/]")
    return 0


def run_queue_cancel(
    msg_id: int,
    *,
    claimer: str = "yggdrasil-panel",
    db: Path | None = None,
    repo_root: Path | None = None,
    console: object | None = None,
) -> int:
    """Cancel a queued message by claiming and acking it.

    Returns ``0`` when the message was free and is now acked, ``1`` when
    the message is already claimed/delivered/missing or the ack failed,
    and ``2`` when *claimer* is empty.
    """
    if console is None:
        from rich.console import Console as _Console

        console = _Console()

    if not claimer or not claimer.strip():
        console.print("[error]✗ claimer is required[/]")
        return 2

    root = _resolve_root(repo_root)
    bus = _open_bus(db, root)
    try:
        msg = bus.claim_by_id(msg_id, claimer)
        if msg is None:
            console.print(
                f"[error]✗ No se pudo cancelar id={msg_id} "
                f"(ya reclamada, entregada o inexistente)[/]"
            )
            return 1
        ok = bus.ack(msg_id, claimer)
    finally:
        bus.close()

    if ok:
        console.print(f"[success]✓ Cancelada tarea id={msg_id}[/]")
        return 0

    console.print(f"[error]✗ No se pudo ack id={msg_id}[/]")
    return 1


# ── work ─────────────────────────────────────────────────────────────────


def run_work_once(
    *,
    claimer: str,
    role: str = DEFAULT_ROLE,
    channel: str = "minimax",
    timeout: int = DEFAULT_TIMEOUT,
    db: Path | None = None,
    repo_root: Path | None = None,
    console: object | None = None,
    route_lookup: Callable[[str], str | None] | None = None,
) -> int:
    """Process ONE ``queue.task`` if any is pending.

    Returns:

    - ``0`` when the queue is empty for ``role`` (legitimate idle exit).
    - ``0`` when a task is processed **and** the spawn exited 0 (acked).
    - ``1`` when a task was claimed but rejected (no match, unknown agent,
      channel error, or the subprocess failed) — the message is released
      back to the queue so a peer worker can retry.

    The ``route_lookup`` kwarg lets tests inject a deterministic router;
    the CLI handler builds one from the live registry every call.

    Note: the *channel* default is ``minimax`` (a sub-agent profile),
    not ``sakana`` — the orchestrator (Lilith) is already running on
    Sakana Fugu Ultra in the main session; the queue worker just
    forwards tasks to spawned sub-agents, so it picks one of the
    sub-agent channels by default. Pass ``channel="sakana"`` to push
    a task onto the orchestrator's own model.
    """
    if console is None:
        from rich.console import Console as _Console

        console = _Console()

    if not claimer or not claimer.strip():
        console.print("[error]✗ --as claimer is required[/]")
        return 2

    root = _resolve_root(repo_root)

    bus = _open_bus(db, root)
    try:
        msg = bus.claim_any(role, claimer)
    finally:
        bus.close()

    if msg is None:
        console.print(f"[dim]queue vacía para role='{role}' — nada que procesar[/]")
        return 0

    payload = msg.payload if isinstance(msg.payload, dict) else {}
    task = str(payload.get("task") or "")
    if not task:
        # Defensive: nothing to do → release + warn, never ack.
        bus = _open_bus(db, root)
        try:
            bus.release(msg.id, claimer)
        finally:
            bus.close()
        console.print(f"[error]✗ queue message id={msg.id} had no 'task' field — released[/]")
        return 1

    # ── resolve agent ────────────────────────────────────────────────
    if route_lookup is None:
        try:
            definitions = _load_card_definitions(root)
        except FileNotFoundError as exc:
            bus = _open_bus(db, root)
            try:
                bus.release(msg.id, claimer)
            finally:
                bus.close()
            console.print(f"[error]✗ {exc}[/]")
            return 1
        lookup = _build_live_route_lookup(definitions)
    else:
        lookup = route_lookup

    agent, source = _resolve_agent_from_payload(payload, route_lookup=lookup)

    if agent is None:
        bus = _open_bus(db, root)
        try:
            bus.release(msg.id, claimer)
        finally:
            bus.close()
        console.print(
            f"[error]✗ sin agente para la tarea id={msg.id}: "
            f"[dim]'{task[:80]}'[/]. Encolá con --agent para fijarlo."
        )
        return 1

    console.print(
        f"[bold realm]work[/] [cyan]{claimer}[/] toma id={msg.id} "
        f"([dim]{source}[/]) → [cyan]{agent}[/]"
    )

    # ── execute via the shared spawn core ────────────────────────────
    bus_path = Path(db).resolve() if db is not None else root / ".ygg" / "lilith_bus.db"
    exit_code = run_spawn(
        agent=agent,
        task=task,
        channel=channel,
        timeout=timeout,
        db=bus_path,
        repo_root=root,
        console=console,
    )

    # ── ack / release the queue message ──────────────────────────────
    bus = _open_bus(db, root)
    try:
        if exit_code == 0:
            bus.ack(msg.id, claimer)
            console.print(f"[success]✓ id={msg.id} acked por {claimer}[/]")
            return 0
        bus.release(msg.id, claimer)
    finally:
        bus.close()

    console.print(f"[error]✗ id={msg.id} released — spawn exit={exit_code}[/]")
    return exit_code if exit_code != 0 else 1


def run_work_watch(
    *,
    claimer: str,
    role: str = DEFAULT_ROLE,
    channel: str = "minimax",
    timeout: int = DEFAULT_TIMEOUT,
    interval: int = DEFAULT_INTERVAL,
    db: Path | None = None,
    repo_root: Path | None = None,
    console: object | None = None,
    route_lookup: Callable[[str], str | None] | None = None,
) -> int:
    """Loop ``run_work_once`` until interrupted. Returns ``0`` on Ctrl+C."""
    if console is None:
        from rich.console import Console as _Console

        console = _Console()

    processed = 0
    console.print(
        f"[bold realm]work --watch[/] [cyan]{claimer}[/]  [dim]role={role}[/] "
        f"[dim]interval={interval}s[/]  [dim]Ctrl+C to stop[/]"
    )
    try:
        while True:
            code = run_work_once(
                claimer=claimer,
                role=role,
                channel=channel,
                timeout=timeout,
                db=db,
                repo_root=repo_root,
                console=console,
                route_lookup=route_lookup,
            )
            if code == 0:
                processed += 1
            # Idle (code 0, queue empty) or rejected (code 1) → sleep and retry.
            time.sleep(max(0, interval))
    except KeyboardInterrupt:
        console.print(
            f"\n[bold realm]work --watch[/] interrupted: [success]{processed}[/] task(s) processed"
        )
        return 0


# ── CLI surface ──────────────────────────────────────────────────────────


queue_app = App(
    name="queue",
    help="Queue tasks onto the LilithBus (lilith 4.1).",
)


@queue_app.command(name="add")
def queue_add(
    task: Annotated[
        str,
        Parameter(help="Task description (enqueued verbatim as the agent prompt)"),
    ],
    role: Annotated[
        str,
        Parameter(name="--role", help="Anycast role tag (default 'worker')"),
    ] = DEFAULT_ROLE,
    agent: Annotated[
        str | None,
        Parameter(name="--agent", help="Pin a specific Vanaheim card by name"),
    ] = None,
    queued_by: Annotated[
        str,
        Parameter(
            name="--queued-by",
            help="Originator tag stored in the payload (default 'operator')",
        ),
    ] = "operator",
    db: Annotated[
        Path | None,
        Parameter(name="--db", help="Override bus DB path"),
    ] = None,
    repo_root: Annotated[
        Path | None,
        Parameter(name="--repo-root", help="Override Yggdrasil repo root"),
    ] = None,
) -> None:
    """Enqueue *task* onto the bus for a future ``lilith work`` to pick up."""
    from rich.console import Console

    code = run_queue_add(
        task,
        role=role,
        agent=agent,
        queued_by=queued_by,
        db=db,
        repo_root=repo_root,
        console=Console(),
    )
    if code != 0:
        raise SystemExit(code)


@queue_app.command(name="list")
def queue_list(
    limit: Annotated[
        int,
        Parameter(name=["--limit", "-n"], help="Max messages to show"),
    ] = 20,
    db: Annotated[
        Path | None,
        Parameter(name="--db", help="Override bus DB path"),
    ] = None,
    repo_root: Annotated[
        Path | None,
        Parameter(name="--repo-root", help="Override Yggdrasil repo root"),
    ] = None,
) -> None:
    """List pending queue messages with state."""
    from rich.console import Console

    run_queue_list(db=db, repo_root=repo_root, console=Console(), limit=limit)


work_app = App(
    name="work",
    help="Process queued tasks via Vanaheim agents (lilith 4.1).",
)


@work_app.default
def work(
    as_: Annotated[
        str,
        Parameter(
            name=["--as", "-a"],
            help="Claimer name (e.g. 'skadi', 'sakana')",
        ),
    ],
    role: Annotated[
        str,
        Parameter(name="--role", help="Anycast role to claim from (default 'worker')"),
    ] = DEFAULT_ROLE,
    once: Annotated[
        bool,
        Parameter(
            name="--once",
            help="Process one task (or report empty queue) and exit",
        ),
    ] = False,
    watch: Annotated[
        bool,
        Parameter(
            name="--watch",
            help="Loop claim→work→sleep until Ctrl+C",
        ),
    ] = False,
    channel: Annotated[
        str,
        Parameter(
            name="--channel",
            help="Execution channel; one of: " + ", ".join(sorted(_CHANNELS)),
        ),
    ] = "minimax",
    timeout: Annotated[
        int,
        Parameter(name="--timeout", help="Wall-clock cap for each spawn, in seconds"),
    ] = DEFAULT_TIMEOUT,
    interval: Annotated[
        int,
        Parameter(
            name="--interval",
            help="Seconds to sleep between empty-queue polls (--watch only)",
        ),
    ] = DEFAULT_INTERVAL,
    db: Annotated[
        Path | None,
        Parameter(name="--db", help="Override bus DB path"),
    ] = None,
    repo_root: Annotated[
        Path | None,
        Parameter(name="--repo-root", help="Override Yggdrasil repo root"),
    ] = None,
) -> None:
    """Claim a queued task and run it via the same machinery as ``lilith spawn``.

    Exactly one of ``--once`` / ``--watch`` must be specified.
    """
    from rich.console import Console

    if once == watch:
        Console().print("[error]✗ Pass exactly one of --once or --watch.[/]")
        raise SystemExit(2)

    if channel not in _CHANNELS:
        available = ", ".join(sorted(_CHANNELS)) or "(none)"
        Console().print(f"[error]✗ Unknown channel '{channel}'.[/]\n[dim]Available: {available}[/]")
        raise SystemExit(2)

    console = Console()
    common = {
        "claimer": as_,
        "role": role,
        "channel": channel,
        "timeout": timeout,
        "db": db,
        "repo_root": repo_root,
        "console": console,
    }
    if once:
        code = run_work_once(**common)
    else:
        code = run_work_watch(**common, interval=interval)

    if code != 0:
        raise SystemExit(code)


# Re-export so tests / other callers can import by name pattern.
queue_cmd = queue_add
work_cmd = work

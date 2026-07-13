"""Operator console — `do` slice of the Lilith operator console (plan-29 B).

Adds the natural-language end-to-end loop of the pantheon:

- ``lilith do "<pedido>" [--channel minimax|sakana|glm] [--timeout S]
  [--dry-run] [--db]``
    Loads the 14 Vanaheim agent cards, lets
    :class:`lilith_orchestrator.agent_router.AgentRouter` pick the best
    match for *pedido*, prints the routing decision (agent + score +
    reason), and hands off to :func:`lilith_cli.ops_spawn.run_spawn` —
    the same headless core ``spawn`` and ``work`` use. ``--dry-run``
    prints the decision only and exits 0 without touching the bus,
    filesystem, or subprocess. Router miss → exit 3 with a hint pointing
    to the bypass routes.

The module is intentionally a thin composition — every primitive lives
elsewhere:

- card loading → :func:`lilith_cli.ops_queue._load_card_definitions`
- live router lookup → :func:`lilith_cli.ops_queue._build_live_route_lookup`
- spawn body → :func:`lilith_cli.ops_spawn.run_spawn`
- channel registry → :data:`lilith_cli.ops_spawn._CHANNELS`

``route_lookup`` is a **kwarg** on :func:`run_do` (injectable for
tests) — the CLI handler builds one from the live registry every
call. No LLM is ever invoked here in tests: ``ops_spawn._CHANNELS`` is
the same monkeypatch seam A3/C use (``python -c`` shim returning 0).

Versioning: 4.2.0 (B closes the loop B on top of C's queue/work + A3
spawn).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from cyclopts import App, Parameter

from lilith_cli.ops_queue import _load_card_definitions
from lilith_cli.ops_spawn import _CHANNELS, run_spawn
from lilith_orchestrator.agent_router import AgentRoute


if TYPE_CHECKING:
    from collections.abc import Callable


__all__ = [
    "do_app",
    "do_cmd",
    "run_do",
]


# ── Defaults / exit codes ────────────────────────────────────────────────

DEFAULT_TIMEOUT = 300
EXIT_NO_MATCH = 3  # router had no candidate — distinct from queue's exit 1

_HINT_NO_MATCH = (
    "ningún agente matchea; usa `lilith spawn <agente>` directo o `lilith queue add --agent`"
)


# ── Path helpers (mirrors ops / ops_spawn / ops_queue) ───────────────────


def _resolve_yggdrasil_root() -> Path:
    """Find the Yggdrasil workspace root (same helper the other ops modules use)."""
    from lilith_cli.main import _resolve_yggdrasil_root as _real_resolve

    return _real_resolve()


def _resolve_root(repo_root: Path | None) -> Path:
    return Path(repo_root).resolve() if repo_root is not None else _resolve_yggdrasil_root()


def _build_live_do_route_lookup(
    definitions: list,
) -> Callable[[str], AgentRoute | None]:
    """Return a ``task → AgentRoute`` callable backed by a live AgentRouter.

    Mirrors :func:`lilith_cli.ops_queue._build_live_route_lookup` but
    preserves the full :class:`AgentRoute` (score + scoring breakdown)
    so :func:`run_do` can print *why* the router chose what it chose.
    The string-only seam C uses is enough for ``work`` (which only
    needs the agent name) — B's hint surface demands the richer one.
    """
    from lilith_orchestrator.agent_router import AgentRouter

    router = AgentRouter(registry=definitions)
    min_score = 0.0  # every scored candidate passes — matches C's permissiveness

    def lookup(task: str) -> AgentRoute | None:
        ranked = router.rank(task, limit=1)
        if not ranked:
            return None
        if ranked[0].score < min_score:
            return None
        return ranked[0]

    return lookup


# ── Routing reason text ──────────────────────────────────────────────────


def _format_route_reason(route: AgentRoute) -> str:
    """Render a short, human-readable *reason* for the routing decision.

    Uses the transparent scoring fields :class:`AgentRoute` already
    exposes (we never call an LLM to explain). Falls back to a one-word
    summary when no signals matched.
    """
    parts: list[str] = []
    if route.matched_tags:
        parts.append("tags=" + ",".join(route.matched_tags))
    if route.matched_tools:
        parts.append("tools=" + ",".join(route.matched_tools))
    if not parts:
        parts.append(f"jaccard={route.token_overlap:.2f}")
    return " ".join(parts)


# ── Headless core ────────────────────────────────────────────────────────


def run_do(
    task: str,
    *,
    channel: str = "minimax",
    timeout: int = DEFAULT_TIMEOUT,
    dry_run: bool = False,
    db: Path | None = None,
    repo_root: Path | None = None,
    console: object | None = None,
    route_lookup: Callable[[str], AgentRoute | str | None] | None = None,
) -> int:
    """Headless ``do`` body — see module docstring for return codes.

    Returns:

    - ``0`` on successful spawn (or ``--dry-run`` completed).
    - the subprocess's exit code on spawn failure.
    - ``1`` on unknown channel / missing agent / missing opencode bin.
    - ``2`` on empty task string (the CLI is a natural-language entry
      point — an empty prompt is a usage error).
    - ``3`` (``EXIT_NO_MATCH``) when the router finds no candidate; the
      CLI handler translates this into a hint instead of an error
      frame.
    - ``124`` on subprocess timeout (mirrors :func:`run_spawn`).

    ``route_lookup`` is injectable for tests. When ``None`` the live
    :class:`AgentRouter` over the 14 Vanaheim cards is used.
    """
    if console is None:
        from rich.console import Console as _Console

        console = _Console()

    if not task or not task.strip():
        console.print("[error]✗ task must be a non-empty string[/]")
        return 2

    # ── validate channel ─────────────────────────────────────────────
    if channel not in _CHANNELS:
        available = ", ".join(sorted(_CHANNELS)) or "(none)"
        console.print(
            f"[error]✗ Unknown channel '{channel}'.[/]\n[dim]Available channels: {available}[/]"
        )
        return 1
    channel_value = _CHANNELS[channel]

    # ── resolve workspace ────────────────────────────────────────────
    root = _resolve_root(repo_root)
    bus_path = Path(db).resolve() if db is not None else root / ".ygg" / "lilith_bus.db"
    log_dir = root / ".ygg" / "spawns"

    # ── route ────────────────────────────────────────────────────────
    if route_lookup is None:
        try:
            definitions = _load_card_definitions(root)
        except FileNotFoundError as exc:
            console.print(f"[error]✗ {exc}[/]")
            return 1
        lookup = _build_live_do_route_lookup(definitions)
    else:
        lookup = route_lookup

    # Tests inject either an AgentRoute-returning lookup (preferred —
    # lets us assert scoring details) or a string-returning one (C's
    # contract, kept for back-compat with the shared fixtures).
    raw = lookup(task)
    route: AgentRoute | None
    if raw is None:
        route = None
    elif isinstance(raw, str):
        # String back-compat — wrap into a synthetic AgentRoute so the
        # printing path doesn't have to special-case it.
        route = AgentRoute(
            agent_type=raw,
            score=0.0,
            token_overlap=0.0,
            tag_hit=0.0,
            tool_fit=0.0,
        )
    else:
        route = raw

    if route is None:
        console.print(f"[error]✗ {_HINT_NO_MATCH}[/]\n[dim]task: '{task[:80]}'[/]")
        return EXIT_NO_MATCH

    console.print(
        f"[bold realm]do[/] → [cyan]{route.agent_type}[/] "
        f"([dim]score={route.score:.4f}[/], [dim]{_format_route_reason(route)}[/])"
    )

    # ── --dry-run (no subprocess, no bus) ────────────────────────────
    if dry_run:
        console.print(
            f"[dim]--dry-run: would execute via channel='{channel}' "
            f"(model={channel_value}) → log {log_dir}[/]"
        )
        return 0

    # ── execute via the shared spawn core ────────────────────────────
    return run_spawn(
        agent=route.agent_type,
        task=task,
        channel=channel,
        timeout=timeout,
        db=bus_path,
        repo_root=root,
        console=console,
    )


# ── CLI surface ──────────────────────────────────────────────────────────


do_app = App(
    name="do",
    help="Route a natural-language pedido to the best Vanaheim agent and run it (lilith 4.2).",
)


@do_app.default
def do(
    task: Annotated[
        str,
        Parameter(help="Natural-language pedido; routed to the best matching agent"),
    ],
    channel: Annotated[
        str,
        Parameter(
            name="--channel",
            help="Execution channel (model provider); one of: " + ", ".join(sorted(_CHANNELS)),
        ),
    ] = "minimax",
    timeout: Annotated[
        int,
        Parameter(
            name="--timeout",
            help="Wall-clock cap for the subprocess, in seconds",
        ),
    ] = DEFAULT_TIMEOUT,
    dry_run: Annotated[
        bool,
        Parameter(
            name="--dry-run",
            help="Print the routing decision and exit (no subprocess, no bus)",
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
    """Route *task* to the best Vanaheim agent and execute it.

    Thin wrapper around :func:`run_do` — exists to wire Cyclopts
    parameters and translate the int return code into the conventional
    ``SystemExit`` that POSIX shells rely on for control flow.
    """
    from rich.console import Console

    code = run_do(
        task=task,
        channel=channel,
        timeout=timeout,
        dry_run=dry_run,
        db=db,
        repo_root=repo_root,
        console=Console(),
    )
    if code != 0:
        raise SystemExit(code)


# Public alias so tests / other callers can import the entry point by
# name (matches the ``app.command(_do_cmd)`` pattern in main.py).
do_cmd = do

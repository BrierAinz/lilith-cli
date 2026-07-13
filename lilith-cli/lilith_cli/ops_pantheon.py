"""Operator console — pantheon passthrough slice of the Lilith operator console (plan-29 A4).

Final slice of plan-29 — closes the A-stream with a **read-only passthrough**
to the cross-cutting ``.ygg/`` state, so the operator console can see what
the panteón is doing without duplicating write surfaces.

Design (decided by Claude 2026-07-05):

- **Read-only.** Goal creation, turn logging, completion, policy mutation
  remain on ``ygg context`` / ``ygg policy`` (the existing write surfaces).
  This module **does not** reimplement them — it would only invite drift
  between two CLI surfaces that mean the same thing.
- **Direct CrossContext.** Each command calls
  ``CrossContext(<root>/.ygg)`` itself; we don't import
  ``.ygg/context_cli.py`` (the hub script) to avoid coupling the
  operator console to a CLI dispatch entry-point owned by ``ygg``.

Commands exposed on top of A1/A2/A3/B/C:

- ``lilith goals [--goal-id ID] [--ygg-dir PATH]``
    Without ``--goal-id``: render the goals table (id, name, status,
    project, completion %, turns, pending gates) — mirrors
    ``ygg context goals``.
    With ``--goal-id``: render one goal's full detail — last 10 turns,
    all gates, quota remaining — mirrors ``ygg context goal-show``.

- ``lilith policy eval <tool_name> [--ygg-dir PATH]``
    Run ``CrossContext.policies.evaluate({"tool_name": ...})`` and render
    the resulting ``(action, rule)`` pair with the same color mapping as
    ``ygg context eval`` (``deny→error``, ``allow→success``,
    ``flag→warning``, ``log→frost``). When no rule matches, prints a
    dimmed hint that the default ``log`` action applies.

- ``lilith policy list [--ygg-dir PATH]``
    Render the rules table from ``.ygg/policies.yaml`` — mirrors
    ``ygg context policies``.

The ``--ygg-dir`` default is ``<YGGDRASIL_ROOT>/.ygg``, resolved through
:func:`lilith_cli.main._resolve_yggdrasil_root` (the same helper every
other ops module uses).

No LLM is ever invoked. Tests build a tmp_path ``.ygg/`` with a couple of
goal/handoff/policies fixtures and assert table content via ``capsys``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter
from lilith_skills.cross_context import CrossContext


__all__ = [
    "goals",
    "goals_app",
    "policy_app",
    "policy_eval",
    "policy_list",
    "run_goals",
    "run_policy_eval",
    "run_policy_list",
]


# ── Path helpers ─────────────────────────────────────────────────────


def _resolve_yggdrasil_root() -> Path:
    """Find the Yggdrasil workspace root (same helper the other ops modules use)."""
    from lilith_cli.main import _resolve_yggdrasil_root as _real_resolve

    return _real_resolve()


def default_ygg_dir() -> Path:
    """Resolve the default ``.ygg`` directory used by the pantheon passthrough."""
    return _resolve_yggdrasil_root() / ".ygg"


def _resolve_ygg_dir(ygg_dir: Path | None) -> Path:
    """Return the effective ``.ygg`` directory, defaulting to ``<root>/.ygg``."""
    return Path(ygg_dir).resolve() if ygg_dir is not None else default_ygg_dir()


def _open_context(ygg_dir: Path) -> CrossContext:
    """Open a :class:`CrossContext` against ``ygg_dir``.

    The constructor receives the ``.ygg`` directory itself, **not** the
    project root — CrossContext then wires goals / handoffs / audit /
    policies / workflows against ``<ygg>/{goals,handoffs,workflows}/``
    and ``<ygg>/{audit.jsonl,policies.yaml}``.
    """
    return CrossContext(ygg_dir)


# ── Policy eval — color mapping (mirrors .ygg/context_cli.cmd_eval) ──


_POLICY_ACTION_COLORS: dict[str, str] = {
    "deny": "error",
    "allow": "success",
    "flag": "warning",
    "log": "frost",
}


# ── goals — table ────────────────────────────────────────────────────


def _render_goals_table(console: object, goals: list) -> None:
    """Render the goals table (mirrors ``.ygg/context_cli.cmd_goals``)."""
    from rich.table import Table

    if not goals:
        console.print("[dim]No goals found.[/dim]")
        return

    table = Table(
        show_header=True,
        header_style="bold gold1",
        title="[bold realm]Goals[/]",
        expand=False,
    )
    table.add_column("ID", style="bold cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Project", style="dim")
    table.add_column("Done", justify="right")
    table.add_column("Turns", justify="right")
    table.add_column("Gates", justify="right")

    for g in goals:
        table.add_row(
            g.id,
            g.name,
            g.status,
            g.project or "—",
            f"{g.completion_pct:.0%}",
            str(len(g.turns)),
            str(len(g.pending_gates())),
        )

    console.print(table)
    console.print(f"[dim]{len(goals)} goal(s)[/dim]")


def _render_goal_detail(console: object, goal) -> None:
    """Render one goal's full detail (mirrors ``.ygg/context_cli.cmd_goal_show``)."""
    console.print(f"[bold realm]Goal:[/] {goal.name}  [dim]({goal.id})[/dim]")
    console.print(f"[bold]Status:[/]      {goal.status}")
    console.print(f"[bold]Project:[/]     {goal.project or '—'}")
    console.print(f"[bold]Description:[/] {goal.description or '—'}")
    console.print(f"[bold]Completion:[/]  {goal.completion_pct:.0%}")
    console.print(f"[bold]Quota left:[/]  {goal.quota_remaining()}")

    if goal.turns:
        console.print()
        console.print("[bold]Turns (last 10):[/]")
        for turn in goal.turns[-10:]:
            evidence = f" — {turn.evidence}" if turn.evidence else ""
            console.print(
                f"  [dim]{turn.timestamp}[/dim] [bold cyan]{turn.agent}[/] {turn.action}{evidence}"
            )

    if goal.gates:
        console.print()
        console.print("[bold]Gates:[/]")
        for gate in goal.gates:
            if gate.status in ("approved", "skipped"):
                mark = "[success]✓[/]"
            elif gate.status == "rejected":
                mark = "[error]✗[/]"
            else:
                mark = "[dim]○[/]"
            console.print(f"  {mark} [{gate.id}] {gate.description} ({gate.status})")


def run_goals(
    goal_id: str | None = None,
    *,
    ygg_dir: Path | None = None,
    console: object | None = None,
) -> int:
    """List every goal in ``--ygg-dir`` (default ``<root>/.ygg``), or show one.

    Returns ``0`` on success, ``1`` when ``--goal-id`` does not match any
    known goal. The function is side-effect free — no goal mutation, no
    audit append, no bus publish.
    """
    if console is None:
        from rich.console import Console as _Console

        console = _Console()

    cx = _open_context(_resolve_ygg_dir(ygg_dir))
    goals = cx.goals.list()

    if goal_id is None:
        _render_goals_table(console, goals)
        return 0

    match = cx.goals.get(goal_id)
    if match is None:
        console.print(f"[error]✗ No goal with id {goal_id}[/]")
        return 1
    _render_goal_detail(console, match)
    return 0


# ── policy eval ──────────────────────────────────────────────────────


def run_policy_eval(
    tool_name: str,
    *,
    ygg_dir: Path | None = None,
    console: object | None = None,
) -> int:
    """Evaluate ``tool_name`` against ``.ygg/policies.yaml``.

    Returns ``0`` on success. The output mirrors
    ``.ygg/context_cli.cmd_eval``: a colored ``POLICY DECISION`` line when
    a rule matches, or a dimmed hint when nothing matches and the
    default ``log`` action applies.

    No mutation — no audit append, no bus publish. The eval is a pure
    function over the YAML file at the time of the call.
    """
    if console is None:
        from rich.console import Console as _Console

        console = _Console()

    if not tool_name or not tool_name.strip():
        console.print("[error]✗ tool_name must be a non-empty string[/]")
        return 2

    cx = _open_context(_resolve_ygg_dir(ygg_dir))
    action, rule = cx.policies.evaluate({"tool_name": tool_name})

    if rule is None:
        console.print(f"[frost]No policy matches {tool_name!r} → default '{action}'[/frost]")
        return 0

    color = _POLICY_ACTION_COLORS.get(action, "frost")
    console.print(
        f"[{color}]Policy decision:[/{color}] "
        f"[bold]{action.upper()}[/bold] via rule [bold]{rule.name}[/bold] "
        f"(priority {rule.priority})"
    )
    return 0


# ── policy list ──────────────────────────────────────────────────────


def run_policy_list(
    *,
    ygg_dir: Path | None = None,
    console: object | None = None,
) -> int:
    """List every rule in ``.ygg/policies.yaml`` (mirrors cmd_policies)."""
    if console is None:
        from rich.console import Console as _Console

        console = _Console()

    cx = _open_context(_resolve_ygg_dir(ygg_dir))
    ps = cx.policies.load()

    if not ps.rules:
        console.print(f"[dim]No policies in {cx.policies.path}[/dim]")
        return 0

    from rich.table import Table

    table = Table(
        show_header=True,
        header_style="bold gold1",
        title=f"[bold realm]Policies[/]  [dim]{cx.policies.path}[/dim]",
        expand=False,
    )
    table.add_column("Name", style="bold cyan")
    table.add_column("Type")
    table.add_column("Action")
    table.add_column("Priority", justify="right")
    table.add_column("Enabled", justify="right")

    for rule in ps.rules:
        table.add_row(
            rule.name,
            rule.type,
            rule.action,
            str(rule.priority),
            "yes" if rule.enabled else "no",
        )

    console.print(table)
    console.print(f"[dim]{len(ps.rules)} rule(s)[/dim]")
    return 0


# ── CLI surface ──────────────────────────────────────────────────────


goals_app = App(
    name="goals",
    help="Inspect cross-cutting goals (lilith 4.3).",
)


@goals_app.default
def goals(
    goal_id: Annotated[
        str | None,
        Parameter(
            name="--goal-id",
            help="Show full detail for one goal (default: list all)",
        ),
    ] = None,
    ygg_dir: Annotated[
        Path | None,
        Parameter(
            name="--ygg-dir",
            help="Path to .ygg directory (default: <root>/.ygg)",
        ),
    ] = None,
) -> None:
    """List pantheon goals, or show full detail for one goal by id."""
    from rich.console import Console

    code = run_goals(goal_id, ygg_dir=ygg_dir, console=Console())
    if code != 0:
        raise SystemExit(code)


policy_app = App(
    name="policy",
    help="Inspect default policies from .ygg/policies.yaml (lilith 4.3).",
)


@policy_app.command(name="eval")
def policy_eval(
    tool_name: Annotated[
        str,
        Parameter(help="Tool name to evaluate against the policy set"),
    ],
    ygg_dir: Annotated[
        Path | None,
        Parameter(
            name="--ygg-dir",
            help="Path to .ygg directory (default: <root>/.ygg)",
        ),
    ] = None,
) -> None:
    """Evaluate which rule (if any) applies to ``tool_name``."""
    from rich.console import Console

    code = run_policy_eval(tool_name, ygg_dir=ygg_dir, console=Console())
    if code != 0:
        raise SystemExit(code)


@policy_app.command(name="list")
def policy_list(
    ygg_dir: Annotated[
        Path | None,
        Parameter(
            name="--ygg-dir",
            help="Path to .ygg directory (default: <root>/.ygg)",
        ),
    ] = None,
) -> None:
    """List every rule in ``.ygg/policies.yaml``."""
    from rich.console import Console

    code = run_policy_list(ygg_dir=ygg_dir, console=Console())
    if code != 0:
        raise SystemExit(code)

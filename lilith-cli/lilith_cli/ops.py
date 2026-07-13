"""Operator console — first slice of the Lilith operator console (plan-29 A1).

Adds two operator-facing commands on top of the existing chat-only CLI:

- ``lilith agents [--show <nombre>]``
    Lists every Vanaheim agent card (name, role, model, level, #tools,
    #hooks) or, with ``--show``, prints the full detail of one card
    (tools, hooks, description).

- ``lilith bus tail|publish|claim|ack``
    Thin operator wrapper around :class:`lilith_core.bus.LilithBus`,
    backed by the SQLite file at
    ``<YGGDRASIL_ROOT>/.ygg/lilith_bus.db`` (path resolved through
    :func:`lilith_cli.main._resolve_yggdrasil_root`).

The YAML loader is **not reimplemented**: agent cards are loaded via
:class:`lilith_skills.agent_cards.AgentCardLoader` — the same loader that
:mod:`lilith_orchestrator.card_bridge` wraps (its ``register_vanaheim_subagents``
function calls ``AgentCardLoader.from_vanaheim`` internally and only adds
SubAgent registry side effects we don't need for a read-only display).
"""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003 — cyclopts resolves annotations at runtime
from typing import Annotated

from cyclopts import App, Parameter
from lilith_skills.agent_cards import AgentCard, AgentCardLoader

from lilith_core.bus import BusError, LilithBus


__all__ = [
    "agents",
    "bus_app",
    "default_bus_db_path",
]


# ── DB path resolution ──────────────────────────────────────────────


def default_bus_db_path() -> Path:
    """Resolve the default LilithBus DB path.

    Uses :func:`lilith_cli.main._resolve_yggdrasil_root` to find the
    workspace root, then returns ``<root>/.ygg/lilith_bus.db``. The
    directory is **not** created here — :class:`LilithBus` does that on
    its own — so this stays side-effect free for tests and ``--help``.
    """
    from lilith_cli.main import _resolve_yggdrasil_root

    root = _resolve_yggdrasil_root()
    return root / ".ygg" / "lilith_bus.db"


# ── agents command ──────────────────────────────────────────────────


@App(name="agents", help="List and inspect Vanaheim agent cards.").default
def agents(
    show: Annotated[
        str | None,
        Parameter(name="--show", help="Show full detail for one card by name"),
    ] = None,
    repo_root: Annotated[
        Path | None,
        Parameter(name="--repo-root", help="Override Yggdrasil repo root"),
    ] = None,
) -> None:
    """List Vanaheim agent cards (or show detail for one)."""
    from rich.console import Console

    if repo_root is None:
        from lilith_cli.main import _resolve_yggdrasil_root

        repo_root = _resolve_yggdrasil_root()

    cards = AgentCardLoader.from_vanaheim(repo_root).list_agents()

    console = Console()

    if show is not None:
        _render_card_detail(console, cards, show)
        return

    _render_card_table(console, cards, repo_root)


def _render_card_detail(console: object, cards: list[AgentCard], show: str) -> None:
    """Render the detail panel for one card (or a friendly error)."""
    match = next((c for c in cards if c.name.lower() == show.lower()), None)
    if match is None:
        console.print(f"[error]✗ No card named '{show}'[/]")
        available = ", ".join(c.name for c in cards) or "(none)"
        console.print(f"[dim]Available: {available}[/]")
        raise SystemExit(1)

    console.print(f"[bold realm]{match.name}[/]  [dim](level {match.level})[/]")
    console.print(f"[dim]Role:[/]        {match.role or '—'}")
    console.print(f"[dim]Model:[/]       {match.model or '—'}")
    if match.description:
        console.print("[dim]Description:[/]")
        console.print(match.description)
    console.print()
    console.print(f"[dim]Tools ({len(match.tools)}):[/]")
    if match.tools:
        for t in match.tools:
            console.print(f"  • {t}")
    else:
        console.print("  [dim](none)[/]")
    console.print()
    console.print(f"[dim]Hooks ({len(match.hooks)}):[/]")
    if match.hooks:
        for h in match.hooks:
            console.print(f"  • {h}")
    else:
        console.print("  [dim](none)[/]")


def _render_card_table(console: object, cards: list[AgentCard], repo_root: Path) -> None:
    """Render the summary table of every loaded card."""
    from rich.table import Table

    table = Table(
        show_header=True,
        header_style="bold gold1",
        title="[bold realm]Vanaheim Agent Cards[/]",
        expand=False,
    )
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Role")
    table.add_column("Model", style="dim")
    table.add_column("Lvl", justify="right")
    table.add_column("Tools", justify="right")
    table.add_column("Hooks", justify="right")

    for c in cards:
        table.add_row(
            c.name,
            c.role or "—",
            c.model or "—",
            str(c.level),
            str(len(c.tools)),
            str(len(c.hooks)),
        )

    console.print(table)
    source = repo_root / "Vanaheim" / "Agents" / "agent_cards.yaml"
    console.print(f"[dim]{len(cards)} card(s) from {source}[/]")


# ── bus subcommand group ────────────────────────────────────────────


bus_app = App(
    name="bus",
    help="Interact with the LilithBus (publish/poll/claim/ack).",
)


def _open_bus(db: Path | None) -> tuple[LilithBus, Path]:
    """Open a :class:`LilithBus` against ``db`` (or the default path).

    The default DB's parent directory is created on demand to match the
    convention used by LilithBus itself (mkdir parents=True, exist_ok=True).
    """
    path = db if db is not None else default_bus_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return LilithBus(path), path


@bus_app.command
def tail(
    topic: Annotated[
        str | None,
        Parameter(
            name=["--topic", "-t"],
            help="Topic pattern (dot-hierarchy; '*' = one segment, '**' = recursive)",
        ),
    ] = "**",
    limit: Annotated[
        int,
        Parameter(name=["--limit", "-n"], help="Max messages to show"),
    ] = 20,
    db: Annotated[
        Path | None,
        Parameter(name="--db", help="Override bus DB path"),
    ] = None,
) -> None:
    """Show recent messages from the bus (non-destructive poll)."""
    from rich.console import Console
    from rich.table import Table

    pattern = topic if topic is not None else "**"
    bus, db_path = _open_bus(db)
    try:
        msgs = bus.poll(pattern, limit=limit)
    finally:
        bus.close()

    console = Console()
    if not msgs:
        console.print(f"[dim]No messages matching '{pattern}' in {db_path}[/]")
        return

    table = Table(
        show_header=True,
        header_style="bold gold1",
        title=f"[bold realm]Bus tail[/]  [dim]{db_path} · pattern='{pattern}'[/]",
        expand=False,
    )
    table.add_column("ID", justify="right", no_wrap=True)
    table.add_column("Topic")
    table.add_column("Role", style="dim")
    table.add_column("Payload", overflow="fold")
    table.add_column("Claimed by", style="dim")
    table.add_column("At", style="dim")

    for m in msgs:
        table.add_row(
            str(m.id),
            m.topic,
            m.role or "—",
            json.dumps(m.payload, ensure_ascii=False),
            m.claimed_by or "—",
            m.published_at,
        )

    console.print(table)
    console.print(f"[dim]{len(msgs)} message(s)[/]")


@bus_app.command
def publish(
    topic: Annotated[str, Parameter(help="Topic string (dot-hierarchy)")],
    payload_json: Annotated[
        str,
        Parameter(name="payload", help="JSON object payload as a single string"),
    ],
    role: Annotated[
        str | None,
        Parameter(name="--role", help="Role tag for anycast claim"),
    ] = None,
    db: Annotated[
        Path | None,
        Parameter(name="--db", help="Override bus DB path"),
    ] = None,
) -> None:
    """Publish a message to the bus."""
    from rich.console import Console

    console = Console()
    try:
        payload_obj = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        console.print(f"[error]✗ Payload is not valid JSON: {exc}[/]")
        raise SystemExit(2) from None

    if not isinstance(payload_obj, dict):
        console.print("[error]✗ Payload must be a JSON object (dict)[/]")
        raise SystemExit(2)

    bus, _db_path = _open_bus(db)
    try:
        try:
            msg_id = bus.publish(topic, payload_obj, role=role)
        except BusError as exc:
            console.print(f"[error]✗ Publish failed: {exc}[/]")
            raise SystemExit(1) from None
    finally:
        bus.close()

    console.print(f"[success]✓ Published id={msg_id} topic='{topic}' role={role or '—'}[/]")


@bus_app.command
def claim(
    role: Annotated[
        str,
        Parameter(name="--role", help="Role to claim from"),
    ],
    claimer: Annotated[
        str,
        Parameter(
            name=["--claimer", "-c"],
            help="Claimer name (e.g. 'skadi', 'sakana')",
        ),
    ],
    db: Annotated[
        Path | None,
        Parameter(name="--db", help="Override bus DB path"),
    ] = None,
) -> object:
    """Atomically claim the next message for a role (anycast).

    Returns the :class:`~lilith_core.bus.BusMessage` on success or
    ``None`` when no pending message exists for ``role``.
    """
    from rich.console import Console

    bus, _db_path = _open_bus(db)
    try:
        msg = bus.claim_any(role, claimer)
    finally:
        bus.close()

    console = Console()
    if msg is None:
        console.print(f"[dim]vacío — no pending message for role '{role}'[/]")
        return None

    console.print(
        f"[bold realm]Claimed id={msg.id}[/]  topic='{msg.topic}'  role={msg.role or '—'}"
    )
    console.print(f"[dim]Payload:[/] {json.dumps(msg.payload, ensure_ascii=False)}")
    console.print(f"[dim]Published at:[/] {msg.published_at}")
    return msg


@bus_app.command
def ack(
    msg_id: Annotated[int, Parameter(help="Message ID to ack")],
    claimer: Annotated[
        str,
        Parameter(
            name=["--claimer", "-c"],
            help="Claimer name (must match the original claim)",
        ),
    ],
    db: Annotated[
        Path | None,
        Parameter(name="--db", help="Override bus DB path"),
    ] = None,
) -> None:
    """Acknowledge delivery of a previously claimed message."""
    from rich.console import Console

    bus, _db_path = _open_bus(db)
    try:
        ok = bus.ack(msg_id, claimer)
    finally:
        bus.close()

    console = Console()
    if ok:
        console.print(f"[success]✓ Acked id={msg_id} claimer={claimer}[/]")
        return

    console.print(f"[error]✗ Could not ack id={msg_id} (wrong claimer, or already delivered)[/]")
    raise SystemExit(1)

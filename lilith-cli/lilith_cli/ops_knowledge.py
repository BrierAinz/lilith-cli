"""Operator console — knowledge slice of the Lilith operator console (plan-29 A2).

Adds two operator-facing commands on top of the A1 ``ops`` module:

- ``lilith ask "<pregunta>" [--k N] [--vector] [--index] [--db PATH] [--root PATH]``
    Thin passthrough to Mimir's RAG (``Vanaheim/Agents/Mimir/cli.py``).
    The Mimir module is **loaded by file path with importlib** (same
    pattern as ``ygg.py`` uses for ``.ygg/subagents_cli.py`` and
    ``.ygg/route_cli.py``) so the workspace root is the source of
    truth and there is no editable-install dance.

    With ``--index`` the local index is rebuilt (calls Mimir's
    ``cmd_index``) before the query is sent. Without an index the user
    gets a one-line hint instead of a stacktrace.

- ``lilith memory search "<query>" [--limit N] [--db PATH]``
- ``lilith memory recent [--limit N] [--db PATH]``
    Operator wrappers over :class:`lilith_memory.store.MemoryStore`.
    The default DB path is whatever the lilith-cli config says (the
    same path ``agent.py:_init_memory`` uses).  ``requester="operator"``
    is stamped on every recall so the read-guard policy treats these
    reads as operator-grade (not user-grade).  An explicit ``--db``
    override is provided for testing and for cross-DB triage.

    A missing DB prints a clear hint and exits non-zero — we do **not**
    silently bootstrap an empty DB on search/recent.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from lilith_memory.store import MemoryStore


__all__ = [
    "ask",
    "default_memory_db_path",
    "load_mimir_cli",
    "memory_app",
]


# ── Mimir loader ────────────────────────────────────────────────────


def _resolve_yggdrasil_root() -> Path:
    """Find the Yggdrasil workspace root (same helper used in :mod:`ops`)."""
    from lilith_cli.main import _resolve_yggdrasil_root as _real_resolve

    return _real_resolve()


def _mimir_cli_path() -> Path:
    """Path to Mimir's CLI module inside the workspace."""
    return _resolve_yggdrasil_root() / "Vanaheim" / "Agents" / "Mimir" / "cli.py"


def load_mimir_cli() -> object:
    """Load Mimir's CLI module via :mod:`importlib.util`.

    Mirrors the loader style used in ``ygg.py`` for
    ``.ygg/subagents_cli.py`` and ``.ygg/route_cli.py``: load by file
    path so the workspace root owns the source of truth and no
    editable-install step is required.

    Raises
    ------
    FileNotFoundError
        If the Mimir CLI path does not exist on disk.
    RuntimeError
        If the loader cannot produce a module spec (e.g. corrupted
        environment).
    """
    cli_path = _mimir_cli_path()
    if not cli_path.is_file():
        raise FileNotFoundError(f"Mimir CLI not found at {cli_path}")
    spec = importlib.util.spec_from_file_location("_lilith_operator_mimir", str(cli_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build importlib spec for {cli_path}")
    mod = importlib.util.module_from_spec(spec)

    # Mimir's cli.py does ``from lilith_memory.chunker import ...`` etc.,
    # so the workspace root must be on sys.path before exec_module runs.
    workspace_root = str(_resolve_yggdrasil_root())
    if workspace_root not in sys.path:
        sys.path.insert(0, workspace_root)

    # Also make sure the Asgard workspace (where lilith_memory lives) is
    # importable.  Falls back gracefully when run from non-Asgard hosts.
    asgard_root = str(_resolve_yggdrasil_root() / "Asgard")
    if asgard_root not in sys.path:
        sys.path.insert(0, asgard_root)

    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ── ask command ─────────────────────────────────────────────────────


def ask(  # cyclopts command is intentionally named "ask" (intentional shadow)
    query: Annotated[
        str,
        Parameter(help="Question / search string passed to Mimir"),
    ],
    k: Annotated[
        int,
        Parameter(name=["--k", "-k"], help="Top-k passages to retrieve"),
    ] = 5,
    vector: Annotated[
        bool,
        Parameter(
            name="--vector",
            help="Use the vector backend (HashEmbedder) instead of fts5",
        ),
    ] = False,
    index: Annotated[
        bool,
        Parameter(
            name="--index",
            help="Rebuild the local Mimir index before querying",
        ),
    ] = False,
    db: Annotated[
        Path | None,
        Parameter(name="--db", help="Override Mimir index DB path"),
    ] = None,
    root: Annotated[
        Path | None,
        Parameter(
            name=["--root", "-r"],
            help="Override Yggdrasil knowledge root",
        ),
    ] = None,
) -> None:
    """Passthrough to Mimir RAG — recall top-k passages for *query*."""
    from rich.console import Console

    console = Console()

    root_path = Path(root).resolve() if root is not None else _resolve_yggdrasil_root()
    index_db: Path | None = Path(db).resolve() if db is not None else None

    try:
        mimir = load_mimir_cli()
    except FileNotFoundError as exc:
        console.print(f"[error]✗ {exc}[/]")
        raise SystemExit(1) from None
    except RuntimeError as exc:
        console.print(f"[error]✗ Mimir loader failure: {exc}[/]")
        raise SystemExit(1) from None

    # ── (optional) re-index ────────────────────────────────────────
    if index:
        argv_index: list[str] = []
        if index_db is not None:
            # ``--db`` lives on Mimir's top-level parser, so it MUST be
            # passed before the subcommand for argparse to recognise it.
            argv_index += ["--db", str(index_db)]
        argv_index += [
            "index",
            "--root",
            str(root_path),
        ]
        if vector:
            argv_index += ["--backend", "vector"]
        rc_index = _run_mimir_main(mimir, argv_index)
        if rc_index not in (0, None):
            console.print(f"[error]✗ Mimir index failed (rc={rc_index})[/]")
            raise SystemExit(int(rc_index) or 1)

    # ── resolve the default index DB if not user-overridden ────────
    if index_db is None:
        index_db = mimir._index_paths(root_path, None)[0]

    if not index_db.exists():
        console.print(
            f"[error]✗ No Mimir index at {index_db}.[/]\n"
            f'[dim]Run `lilith ask --index "<warming question>"` first.[/]',
        )
        raise SystemExit(2)

    # ── query ──────────────────────────────────────────────────────
    argv_ask: list[str] = []
    if index_db is not None:
        argv_ask += ["--db", str(index_db)]
    argv_ask += [
        "ask",
        query,
        "--root",
        str(root_path),
        "-k",
        str(max(1, k)),
    ]
    if vector:
        argv_ask += ["--backend", "vector"]
    rc_ask = _run_mimir_main(mimir, argv_ask)
    # Mimir's cmd_ask already prints the formatted output (or the
    # "(no matches ...)" stderr line).  Just propagate its exit code.
    if rc_ask not in (0, None):
        raise SystemExit(int(rc_ask) or 1)


def _run_mimir_main(mimir: object, argv: list[str]) -> int | None:
    """Invoke Mimir's :func:`main` while keeping ``sys.argv`` clean.

    Mimir uses argparse with ``parse_args(argv)``, so we can pass an
    explicit argv list without polluting the real :data:`sys.argv`.
    """
    main_fn = getattr(mimir, "main", None)
    if not callable(main_fn):
        # Defensive fallback: Mimir < 1.0 layout?
        return None
    return int(main_fn(argv) or 0)


# ── memory subcommand group ─────────────────────────────────────────


def default_memory_db_path() -> Path:
    """Resolve the operator memory DB path from the lilith-cli config.

    Reads ``~/.yggdrasil/config.yaml`` through :func:`lilith_cli.config.load_config`
    and returns ``config.memory.db_path`` (``~`` already expanded by the
    config loader).  Falls back to the documented default if the config
    is missing the memory block entirely.
    """
    try:
        from lilith_cli.config import load_config

        cfg = load_config(None)
        raw = cfg.memory.db_path
        return (
            Path(raw).expanduser().resolve() if raw else (Path.home() / ".yggdrasil" / "memory.db")
        )
    except Exception:
        # Misconfigured YAML or first-run: stay safe with the documented default.
        return Path.home() / ".yggdrasil" / "memory.db"


memory_app = App(
    name="memory",
    help="Search or recall the operator memory store (lilith_memory).",
)


def _open_memory_store(db: Path | None) -> tuple[MemoryStore, Path]:
    """Open a :class:`MemoryStore` after validating the DB exists.

    The DB's parent directory is **not** created (so the test suite can
    guarantee a missing path is reported as missing).  ``requester``
    is hard-coded to ``"operator"`` so the read-guard policy treats
    these reads as operator-grade (per plan-29 A2 spec).
    """
    path = db if db is not None else default_memory_db_path()
    path = Path(path).expanduser().resolve()
    if not path.exists():
        from rich.console import Console

        Console().print(
            f"[error]✗ Memory DB not found at {path}.[/]\n"
            f"[dim]Start an interactive session first (`lilith chat`) to bootstrap it, "
            f"or pass `--db PATH` to point at an existing one.[/]",
        )
        raise SystemExit(1)
    return MemoryStore(path), path


@memory_app.command
def search(
    query: Annotated[
        str,
        Parameter(help="Substring / LIKE match against memory content"),
    ],
    limit: Annotated[
        int,
        Parameter(name=["--limit", "-n"], help="Maximum number of hits"),
    ] = 5,
    db: Annotated[
        Path | None,
        Parameter(name="--db", help="Override memory DB path"),
    ] = None,
) -> None:
    """Search the operator memory store (LIKE over content)."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    store, db_path = _open_memory_store(db)

    hits = store.search(query, limit=max(1, limit), requester="operator")
    if not hits:
        console.print(f"[dim]No memories matching '{query}' in {db_path}[/]")
        return

    table = Table(
        show_header=True,
        header_style="bold gold1",
        title=f"[bold realm]Memory search[/]  [dim]{db_path} · query='{query}'[/]",
        expand=False,
    )
    table.add_column("ID", justify="right", no_wrap=True)
    table.add_column("Session", style="dim")
    table.add_column("Role")
    table.add_column("Content", overflow="fold")
    table.add_column("When", style="dim")

    for h in hits:
        meta_str = json.dumps(h.get("metadata") or {}, ensure_ascii=False)
        content = h.get("content", "")
        if len(content) > 240:
            content = content[:240].rstrip() + "…"
        table.add_row(
            str(h.get("id", "?")),
            h.get("session_id", "—"),
            h.get("role", "—"),
            content,
            h.get("created_at", "—"),
        )
        if meta_str not in ("{}", ""):
            console.print(f"[dim]  meta:[/] {meta_str}")
    console.print(table)
    console.print(f"[dim]{len(hits)} hit(s)[/]")


@memory_app.command
def recent(
    limit: Annotated[
        int,
        Parameter(name=["--limit", "-n"], help="Maximum number of entries"),
    ] = 10,
    db: Annotated[
        Path | None,
        Parameter(name="--db", help="Override memory DB path"),
    ] = None,
) -> None:
    """Show the most recent entries across all sessions."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    store, db_path = _open_memory_store(db)

    entries = store.recent(limit=max(1, limit), requester="operator")
    if not entries:
        console.print(f"[dim]No memories in {db_path}[/]")
        return

    table = Table(
        show_header=True,
        header_style="bold gold1",
        title=f"[bold realm]Memory recent[/]  [dim]{db_path}[/]",
        expand=False,
    )
    table.add_column("ID", justify="right", no_wrap=True)
    table.add_column("Session", style="dim")
    table.add_column("Role")
    table.add_column("Content", overflow="fold")
    table.add_column("When", style="dim")

    for h in entries:
        content = h.get("content", "")
        if len(content) > 240:
            content = content[:240].rstrip() + "…"
        table.add_row(
            str(h.get("id", "?")),
            h.get("session_id", "—"),
            h.get("role", "—"),
            content,
            h.get("created_at", "—"),
        )
    console.print(table)
    console.print(f"[dim]{len(entries)} entry(ies)[/]")

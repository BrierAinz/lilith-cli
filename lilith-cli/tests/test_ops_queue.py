"""Tests for lilith_cli.ops_queue — operator console queue + work (plan-29 C).

Scope (intentionally minimal, deterministic — no LLM ever invoked):

- ``queue add`` publishes a ``queue.task`` message with the right
  payload schema (task / agent? / queued_by / queued_at).
- ``queue list`` tails ``queue.**`` and distinguishes free vs claimed.
- ``work --once`` on an **empty queue** returns exit 0 with a friendly
  message and never claims anything.
- ``work --once`` on a **pinned** ``queue.task`` calls the shared
  :func:`run_spawn` core, hits the fake channel successfully, and
  ``bus.ack``s the message.
- ``work --once`` on a **failing channel** releases the message and
  exits non-zero.
- ``work --once`` **auto-routes** (no pinned agent) using an injected
  lookup → ack on success.
- ``work --once`` with a **no-match** router → release + clear message
  + exit 1.
- ``_resolve_agent_from_payload`` covers the pinned / router / miss
  three-way decision.
- ``run_work_once`` correctly releases when the payload is missing the
  ``task`` field.

All temp trees live inside the test's ``tmp_path`` and are removed by
pytest automatically. **No real LLM is ever invoked.**

Same fixture strategy as :mod:`test_ops_spawn` — monkeypatch
``lilith_cli.main.__file__`` to a fake ``<tmp>/Yggdrasil/.../main.py``
so :func:`_resolve_yggdrasil_root` lands inside ``tmp_path``.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest


# Mirror conftest.py locally because tests in this file may be collected
# by other pytest entry points (e.g. a broader scope runner).
_PKG_DIR = str(Path(__file__).resolve().parent.parent)
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def fake_repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Relocate the resolved Yggdrasil root into ``tmp_path``.

    Builds the minimum layout :func:`_resolve_yggdrasil_root` looks for
    plus a ``Vanaheim/Agents/agent_cards.yaml`` with two cards (Hela +
    Mimir) so router-based tests have multiple candidates to rank. An
    empty ``.ygg/`` is also created so :class:`LilithBus` can land its
    DB there.
    """
    from lilith_cli import main as cli_main

    fake_root = tmp_path / "Yggdrasil"
    fake_main = fake_root / "Asgard" / "lilith-cli" / "lilith_cli" / "main.py"
    fake_main.parent.mkdir(parents=True)
    fake_main.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli_main, "__file__", str(fake_main))

    (fake_root / "Vanaheim" / "Agents").mkdir(parents=True)
    (fake_root / "Vanaheim" / "Agents" / "agent_cards.yaml").write_text(
        "---\n"
        "name: Hela\n"
        "role: Queen of Helheim\n"
        "level: 2\n"
        "model: glm-5.2\n"
        "tools:\n"
        "  - filesystem\n"
        "description: Oversees the dead and the archive.\n"
        "hooks: []\n"
        "---\n"
        "name: Mimir\n"
        "role: Keeper of the well of wisdom\n"
        "level: 2\n"
        "model: glm-5.2\n"
        "tools:\n"
        "  - filesystem\n"
        "description: Answers research questions from the well of memory.\n"
        "hooks: []\n",
        encoding="utf-8",
    )
    (fake_root / ".ygg").mkdir(parents=True, exist_ok=True)

    return fake_root


@pytest.fixture
def fake_repo_root_no_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Relocate the root with an EMPTY agent_cards.yaml — drives the
    'no cards available' branch (``AgentRouter`` registry ends up empty).
    """
    from lilith_cli import main as cli_main

    fake_root = tmp_path / "Yggdrasil"
    fake_main = fake_root / "Asgard" / "lilith-cli" / "lilith_cli" / "main.py"
    fake_main.parent.mkdir(parents=True)
    fake_main.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli_main, "__file__", str(fake_main))

    (fake_root / "Vanaheim" / "Agents").mkdir(parents=True)
    (fake_root / "Vanaheim" / "Agents" / "agent_cards.yaml").write_text(
        "# empty on purpose\n", encoding="utf-8"
    )
    (fake_root / ".ygg").mkdir(parents=True, exist_ok=True)

    return fake_root


@pytest.fixture
def fake_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``ops_spawn._CHANNELS`` with a Python ``-c`` shim that
    prints a deterministic marker (exit 0). Reused as the spawn fixture
    in A3 — same seam works here.
    """
    from lilith_cli import ops_spawn

    fake_argv = [sys.executable, "-c", "print('fake-agent-done')"]
    monkeypatch.setitem(ops_spawn._CHANNELS, "fake", fake_argv)


@pytest.fixture
def failing_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``ops_spawn._CHANNELS`` with a channel that always exits
    non-zero — drives the ``work --once`` release-on-failure branch.
    """
    from lilith_cli import ops_spawn

    failing = [
        sys.executable,
        "-c",
        "import sys; print('boom', file=sys.stderr); sys.exit(7)",
    ]
    monkeypatch.setitem(ops_spawn._CHANNELS, "fail7", failing)


def _bus_path(root: Path) -> Path:
    return root / ".ygg" / "lilith_bus.db"


def _read_bus_messages(bus_db: Path) -> list[sqlite3.Row]:
    """Read every bus row ordered by id (topics + roles + payloads)."""
    with sqlite3.connect(bus_db) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT id, topic, role, payload, claimed_by, delivered_at "
            "FROM messages ORDER BY id ASC"
        ).fetchall()


# ── Importability & version bump ────────────────────────────────────


def test_ops_queue_module_imports() -> None:
    """ops_queue exposes the documented public symbols."""
    from lilith_cli import ops_queue

    assert callable(ops_queue.run_queue_add)
    assert callable(ops_queue.run_queue_list)
    assert callable(ops_queue.run_work_once)
    assert callable(ops_queue.run_work_watch)
    assert callable(ops_queue.queue_add)
    assert callable(ops_queue.work)
    assert ops_queue.queue_app.name == "queue" or (
        isinstance(ops_queue.queue_app.name, tuple) and ops_queue.queue_app.name[0] == "queue"
    )
    assert ops_queue.work_app.name == "work" or (
        isinstance(ops_queue.work_app.name, tuple) and ops_queue.work_app.name[0] == "work"
    )


def test_default_bus_db_path_uses_resolved_root(fake_repo_root: Path) -> None:
    """Default bus DB path lands at <root>/.ygg/lilith_bus.db."""
    from lilith_cli import ops_queue

    assert ops_queue.default_bus_db_path() == _bus_path(fake_repo_root)


def test_version_bumped_to_4_4_0() -> None:
    """lilith-cli version must be 4.4.0 after the A4 pantheon passthrough slice."""
    from lilith_cli.main import __version__

    assert __version__ == "4.4.0"


def test_app_registers_queue_and_work() -> None:
    """main.app should expose ``queue`` and ``work`` alongside the A1-A3 commands."""
    from lilith_cli.main import app

    registered = set(app._registered_commands)
    assert "queue" in registered, f"queue missing from {registered}"
    assert "work" in registered, f"work missing from {registered}"
    # A1 + A2 + A3 commands must still be present (no regressions).
    assert "agents" in registered
    assert "bus" in registered
    assert "ask" in registered
    assert "memory" in registered
    assert "spawn" in registered


# ── queue.add ────────────────────────────────────────────────────────


def test_queue_add_publishes_with_default_role_and_payload(
    fake_repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``queue add`` publishes a ``queue.task`` message with the
    expected payload schema and the default ``worker`` role.
    """
    from lilith_cli import ops_queue

    bus_db = _bus_path(fake_repo_root)
    code = ops_queue.run_queue_add(
        "audit this",
        db=bus_db,
        repo_root=fake_repo_root,
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "queued" in out.lower()
    assert "role=worker" in out

    rows = _read_bus_messages(bus_db)
    assert len(rows) == 1
    assert rows[0]["topic"] == "queue.task"
    assert rows[0]["role"] == "worker"
    payload = json.loads(rows[0]["payload"])
    assert payload["task"] == "audit this"
    assert payload["agent"] is None
    assert payload["queued_by"] == "operator"
    assert isinstance(payload["queued_at"], float)


def test_queue_add_with_pinned_agent_stores_agent_in_payload(
    fake_repo_root: Path,
) -> None:
    """When ``--agent`` is supplied, the payload must carry it through."""
    from lilith_cli import ops_queue

    bus_db = _bus_path(fake_repo_root)
    code = ops_queue.run_queue_add(
        "draft a release note",
        agent="Hela",
        db=bus_db,
        repo_root=fake_repo_root,
    )
    assert code == 0

    rows = _read_bus_messages(bus_db)
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["agent"] == "Hela"
    assert payload["task"] == "draft a release note"


def test_queue_add_with_custom_role_and_queued_by(
    fake_repo_root: Path,
) -> None:
    """Custom --role + --queued-by override the defaults cleanly."""
    from lilith_cli import ops_queue

    bus_db = _bus_path(fake_repo_root)
    code = ops_queue.run_queue_add(
        "anycast this",
        role="researcher",
        queued_by="cotal",
        db=bus_db,
        repo_root=fake_repo_root,
    )
    assert code == 0

    rows = _read_bus_messages(bus_db)
    assert rows[0]["role"] == "researcher"
    payload = json.loads(rows[0]["payload"])
    assert payload["queued_by"] == "cotal"


def test_queue_add_rejects_empty_task(fake_repo_root: Path) -> None:
    """An empty task should exit 1 without ever touching the bus."""
    from lilith_cli import ops_queue

    bus_db = _bus_path(fake_repo_root)
    assert ops_queue.run_queue_add("", db=bus_db, repo_root=fake_repo_root) == 1
    assert ops_queue.run_queue_add("   ", db=bus_db, repo_root=fake_repo_root) == 1
    assert not bus_db.exists(), "no DB should be created for empty adds"


# ── queue.list ───────────────────────────────────────────────────────


def test_queue_list_on_empty_db_prints_friendly_message(
    fake_repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty bus renders a friendly message and exits 0."""
    from lilith_cli import ops_queue

    bus_db = _bus_path(fake_repo_root)
    code = ops_queue.run_queue_list(db=bus_db, repo_root=fake_repo_root, limit=20)
    assert code == 0
    out = capsys.readouterr().out
    assert "No pending" in out or "no pending" in out.lower()


def test_queue_list_shows_unclaimed_and_claimed_state(
    fake_repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two messages — one free, one claimed — appear in the table
    with the correct ``State`` column.
    """
    from lilith_cli import ops_queue
    from lilith_core.bus import LilithBus

    bus_db = _bus_path(fake_repo_root)
    bus = LilithBus(bus_db)
    try:
        bus.publish("queue.task", {"task": "free msg", "agent": None}, role="worker")
        bus.publish("queue.task", {"task": "claimed msg", "agent": "Hela"}, role="worker")
        bus.claim_any("worker", "skadi")
    finally:
        bus.close()

    code = ops_queue.run_queue_list(db=bus_db, repo_root=fake_repo_root, limit=20)
    assert code == 0
    out = capsys.readouterr().out
    # Both task strings should appear in the rendered table.
    assert "free msg" in out
    assert "claimed msg" in out
    # The free one is rendered with a "free" state; the claimed one
    # carries the claimer's name.
    assert "free" in out.lower()
    assert "skadi" in out


# ── work --once — empty queue ────────────────────────────────────────


def test_work_once_on_empty_queue_exits_zero(
    fake_repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Empty queue → friendly message, no claim, no spawn, exit 0."""
    from lilith_cli import ops_queue

    bus_db = _bus_path(fake_repo_root)
    code = ops_queue.run_work_once(
        claimer="skadi",
        db=bus_db,
        repo_root=fake_repo_root,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "vacía" in out.lower() or "vacia" in out.lower() or "empty" in out.lower()
    # Empty queue → no messages on the bus. (The bus DB file itself is
    # always created by LilithBus — that's expected — but it must end
    # up with zero rows.)
    if bus_db.exists():
        assert _read_bus_messages(bus_db) == []


def test_work_once_with_empty_claimer_exits_two(
    fake_repo_root: Path,
) -> None:
    """``--as`` (``claimer``) is required; missing it → exit 2."""
    from lilith_cli import ops_queue

    code = ops_queue.run_work_once(
        claimer="",
        db=_bus_path(fake_repo_root),
        repo_root=fake_repo_root,
    )
    assert code == 2


# ── work --once — pinned agent ack path ──────────────────────────────


def test_work_once_with_pinned_agent_acks_on_success(
    fake_repo_root: Path,
    fake_channel: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pinned ``Hela`` agent, fake channel (exit 0) → spawn runs,
    the bus message gets acked, exit 0.
    """
    from lilith_cli import ops_queue
    from lilith_core.bus import LilithBus

    bus_db = _bus_path(fake_repo_root)

    # Enqueue with --agent pinning.
    ops_queue.run_queue_add(
        "say hi",
        agent="hela",
        db=bus_db,
        repo_root=fake_repo_root,
    )

    code = ops_queue.run_work_once(
        claimer="skadi",
        channel="fake",
        timeout=30,
        db=bus_db,
        repo_root=fake_repo_root,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "toma" in out.lower() or "claim" in out.lower() or "→" in out
    assert "acked" in out.lower() or "ack" in out.lower()

    # The queue message must now carry claimed_by AND delivered_at.
    # (the spawn core also publishes 2 spawn.{agent} events; we ignore those.)
    rows = _read_bus_messages(bus_db)
    queue_rows = [r for r in rows if r["topic"] == "queue.task"]
    assert len(queue_rows) == 1
    assert queue_rows[0]["claimed_by"] == "skadi"
    assert queue_rows[0]["delivered_at"] is not None

    # The shared spawn core still published its start/done events.
    topics = [r["topic"] for r in rows]
    assert "spawn.hela" in topics
    assert "spawn.hela.done" in topics


# ── work --once — failing channel release path ───────────────────────


def test_work_once_releases_on_subprocess_failure(
    fake_repo_root: Path,
    failing_channel: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When the spawn subprocess exits non-zero, the queue message is
    released (back to the unclaimed pool) and ``work`` returns the
    subprocess's exit code.
    """
    from lilith_cli import ops_queue
    from lilith_core.bus import LilithBus

    bus_db = _bus_path(fake_repo_root)

    ops_queue.run_queue_add(
        "explode",
        agent="hela",
        db=bus_db,
        repo_root=fake_repo_root,
    )

    code = ops_queue.run_work_once(
        claimer="skadi",
        channel="fail7",
        timeout=30,
        db=bus_db,
        repo_root=fake_repo_root,
    )
    assert code == 7  # the fake channel exits 7

    out = capsys.readouterr().out
    assert "released" in out.lower() or "release" in out.lower()

    # The queue message must remain unclaimed (released) — no delivered_at.
    rows = _read_bus_messages(bus_db)
    queue_rows = [r for r in rows if r["topic"] == "queue.task"]
    assert len(queue_rows) == 1
    assert queue_rows[0]["claimed_by"] is None
    assert queue_rows[0]["delivered_at"] is None


# ── work --once — auto routing ───────────────────────────────────────


def test_work_once_auto_routes_via_injected_lookup_and_acks(
    fake_repo_root: Path,
    fake_channel: None,
) -> None:
    """No ``--agent`` pinning → injected ``route_lookup`` decides who
    runs the task; on spawn success the message is acked.
    """
    from lilith_cli import ops_queue

    bus_db = _bus_path(fake_repo_root)

    ops_queue.run_queue_add(
        "research memory",  # no agent pinned
        db=bus_db,
        repo_root=fake_repo_root,
    )

    # Deterministic router: anything → "mimir"
    def lookup(task: str) -> str | None:
        return "mimir"

    code = ops_queue.run_work_once(
        claimer="skadi",
        channel="fake",
        timeout=30,
        db=bus_db,
        repo_root=fake_repo_root,
        route_lookup=lookup,
    )
    assert code == 0

    rows = _read_bus_messages(bus_db)
    queue_row = next(r for r in rows if r["topic"] == "queue.task")
    assert queue_row["claimed_by"] == "skadi"
    assert queue_row["delivered_at"] is not None

    topics = [r["topic"] for r in rows]
    # The spawn core published a spawn.mimir + spawn.mimir.done pair.
    assert "spawn.mimir" in topics
    assert "spawn.mimir.done" in topics


def test_router_no_match_releases_and_reports(
    fake_repo_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Router miss → release the message + clear message + exit 1."""
    from lilith_cli import ops_queue

    bus_db = _bus_path(fake_repo_root)

    ops_queue.run_queue_add(
        "task without a clear home",
        db=bus_db,
        repo_root=fake_repo_root,
    )

    def always_miss(task: str) -> str | None:
        return None

    code = ops_queue.run_work_once(
        claimer="skadi",
        channel="minimax",
        timeout=30,
        db=bus_db,
        repo_root=fake_repo_root,
        route_lookup=always_miss,
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "sin agente" in out.lower() or "no agent" in out.lower()

    rows = _read_bus_messages(bus_db)
    queue_row = next(r for r in rows if r["topic"] == "queue.task")
    assert queue_row["claimed_by"] is None  # released
    assert queue_row["delivered_at"] is None


# ── work --once — payload edge case ──────────────────────────────────


def test_work_once_releases_when_payload_has_no_task(
    fake_repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A corrupt ``queue.task`` payload (no ``task`` field) is released
    defensively instead of being acked — we never lie about success.
    """
    from lilith_cli import ops_queue
    from lilith_core.bus import LilithBus

    bus_db = _bus_path(fake_repo_root)
    bus = LilithBus(bus_db)
    try:
        bus.publish("queue.task", {"agent": "hela"}, role="worker")  # no "task"
    finally:
        bus.close()

    code = ops_queue.run_work_once(
        claimer="skadi",
        channel="minimax",
        timeout=30,
        db=bus_db,
        repo_root=fake_repo_root,
        route_lookup=lambda _t: "hela",
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "no 'task' field" in out or "no 'task'" in out

    rows = _read_bus_messages(bus_db)
    assert rows[0]["claimed_by"] is None


# ── Pure helper tests (no subprocess, no bus) ────────────────────────


class TestResolveAgentFromPayload:
    """Cover the three-way decision in ``_resolve_agent_from_payload``.
    Pure function, no I/O.
    """

    def test_pinned_agent_takes_priority(self) -> None:
        from lilith_cli.ops_queue import _resolve_agent_from_payload

        agent, source = _resolve_agent_from_payload(
            {"task": "x", "agent": "hela"},
            route_lookup=lambda _t: "mimir",
        )
        assert agent == "hela"
        assert source == "pinned"

    def test_router_used_when_no_pin(self) -> None:
        from lilith_cli.ops_queue import _resolve_agent_from_payload

        agent, source = _resolve_agent_from_payload(
            {"task": "y", "agent": None},
            route_lookup=lambda _t: "mimir",
        )
        assert agent == "mimir"
        assert source == "router"

    def test_router_miss_reported_cleanly(self) -> None:
        from lilith_cli.ops_queue import _resolve_agent_from_payload

        agent, source = _resolve_agent_from_payload(
            {"task": "z", "agent": None},
            route_lookup=lambda _t: None,
        )
        assert agent is None
        assert source == "router-miss"

    def test_whitespace_only_pinned_falls_through_to_router(self) -> None:
        from lilith_cli.ops_queue import _resolve_agent_from_payload

        agent, source = _resolve_agent_from_payload(
            {"task": "z", "agent": "   "},
            route_lookup=lambda _t: "mimir",
        )
        assert agent == "mimir"
        assert source == "router"


class TestBuildLiveRouteLookup:
    """The live router lookup must pick the top-scoring definition
    deterministically against an explicit registry. Pattern mirrors
    the orchestrator's own ``test_explicit_registry_overrides_global``.
    """

    def test_picks_top_scored_definition(self) -> None:
        from lilith_cli.ops_queue import _build_live_route_lookup
        from lilith_orchestrator.subagents import SubAgentDefinition

        def _make(name: str, when: str, tags) -> SubAgentDefinition:
            return SubAgentDefinition(
                agent_type=name,
                when_to_use=when,
                system_prompt=f"prompt for {name}",
                allowed_tools=[],
                disallowed_tools=[],
                tags=list(tags),
            )

        registry = [
            _make("hela", when="oversees the dead archive", tags=["archive"]),
            _make("mimir", when="answers research questions memory", tags=["memory"]),
        ]
        lookup = _build_live_route_lookup(registry)
        # The "memory" keyword aligns mimir — top of the rank.
        assert lookup("memory lookup please") == "mimir"
        # The "archive" + "dead" leans toward hela.
        assert lookup("archive of the dead") == "hela"

    def test_returns_none_when_registry_empty(self) -> None:
        from lilith_cli.ops_queue import _build_live_route_lookup

        assert _build_live_route_lookup([])("anything") is None


class TestLoadCardDefinitions:
    """``_load_card_definitions`` should mirror what the spawn core does:
    AgentCard → SubAgentDefinition via ``card_to_subagent``.
    """

    def test_loads_two_cards_from_fake_repo(self, fake_repo_root: Path) -> None:
        from lilith_cli.ops_queue import _load_card_definitions
        from lilith_orchestrator.subagents import SubAgentDefinition

        defs = _load_card_definitions(fake_repo_root)
        assert len(defs) == 2
        types = {d.agent_type for d in defs}
        assert types == {"hela", "mimir"}
        # Each def is a real SubAgentDefinition.
        assert all(isinstance(d, SubAgentDefinition) for d in defs)

    def test_empty_yaml_yields_empty_list(self, fake_repo_root_no_yaml: Path) -> None:
        from lilith_cli.ops_queue import _load_card_definitions

        assert _load_card_definitions(fake_repo_root_no_yaml) == []


# ── Refactor sanity ─────────────────────────────────────────────────


def test_run_spawn_is_exported_and_usable() -> None:
    """The headless :func:`run_spawn` extracted from ``spawn`` must be
    importable from :mod:`ops_spawn` — ``work`` depends on it.
    """
    from lilith_cli import ops_spawn

    assert callable(ops_spawn.run_spawn)
    assert "run_spawn" in ops_spawn.__all__


def test_work_uses_run_spawn_not_subprocess_directly() -> None:
    """Whitebox guard: ``ops_queue.run_work_once`` must mention
    ``run_spawn`` by name (so a future refactor that bypasses the
    shared core breaks this test loudly).
    """
    from lilith_cli import ops_queue

    src = Path(ops_queue.__file__).read_text(encoding="utf-8")
    assert "run_spawn(" in src


# ── CLI surface validation ──────────────────────────────────────────


def test_work_app_requires_exactly_one_of_once_or_watch() -> None:
    """The CLI handler must reject ambiguous mode with a clear message."""
    from lilith_cli.main import app as cli_app
    from lilith_cli.ops_queue import work_app

    # Wiring assertion first: work_app's default callable is `work`.
    assert work_app.default_command is not None
    assert work_app.default_command.__name__ == "work"

    # Calling `work` with neither --once nor --watch → SystemExit(2).
    with pytest.raises(SystemExit) as excinfo:
        cli_app(
            ["work", "--as", "skadi"],
            exit_on_error=False,
            console=None,
        )
    assert excinfo.value.code == 2


def test_work_app_rejects_both_once_and_watch() -> None:
    """Passing both --once and --watch is also an error."""
    from lilith_cli.main import app as cli_app

    with pytest.raises(SystemExit) as excinfo:
        cli_app(
            ["work", "--as", "skadi", "--once", "--watch"],
            exit_on_error=False,
            console=None,
        )
    assert excinfo.value.code == 2

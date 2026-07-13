"""Tests for lilith_cli.ops_do — operator console `do` slice (plan-29 B).

Scope (intentionally minimal, deterministic — no LLM ever invoked):

- ``do "<task>"`` with a successful fake channel propagates the spawn's
  exit code (``0``) and prints the routing choice (agent + score +
  reason).
- ``do "<task>"`` with a **failing** fake channel propagates the
  subprocess exit code (non-zero), no swallowing.
- ``do "<task>"`` with a router that returns ``None`` exits ``3`` and
  prints the hint.
- ``do "<task>" --dry-run`` prints the routing decision and exits ``0``
  without invoking the spawn core (no bus, no subprocess, no log).
- ``run_do`` rejects an empty task with exit ``2`` and never touches
  the bus.
- ``run_do`` validates ``--channel`` against ``ops_spawn._CHANNELS``
  and exits ``1`` on unknown channel.
- The CLI surface registers ``do`` in ``main.app``.
- The version bumps to ``4.2.0`` everywhere.
- Whitebox guard: ``ops_do.run_do`` composes ``run_spawn`` (does not
  reimplement the spawn body).

All temp trees live inside the test's ``tmp_path`` and are removed by
pytest automatically. **No real LLM is ever invoked.**

Same fixture strategy as :mod:`test_ops_queue` and :mod:`test_ops_spawn`
— monkeypatch ``lilith_cli.main.__file__`` to a fake
``<tmp>/Yggdrasil/.../main.py`` so :func:`_resolve_yggdrasil_root`
lands inside ``tmp_path``.
"""

from __future__ import annotations

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
    Mimir) so ``_load_card_definitions`` returns a usable registry. An
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
def fake_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``ops_spawn._CHANNELS`` with a Python ``-c`` shim that
    prints a deterministic marker (exit 0). Same seam A3/C use.
    """
    from lilith_cli import ops_spawn

    fake_argv = [sys.executable, "-c", "print('fake-agent-done')"]
    monkeypatch.setitem(ops_spawn._CHANNELS, "fake", fake_argv)


@pytest.fixture
def failing_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``ops_spawn._CHANNELS`` with a channel that always exits
    non-zero — drives the ``do`` propagate-failure branch.
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


def _make_route(agent_type: str, score: float = 0.0) -> object:
    """Build a synthetic AgentRoute for the injectable lookup."""
    from lilith_orchestrator.agent_router import AgentRoute

    return AgentRoute(
        agent_type=agent_type,
        score=score,
        token_overlap=score,
        tag_hit=1.0 if score else 0.0,
        tool_fit=0.0,
        matched_tags=("memory",) if score else (),
        matched_tools=(),
    )


# ── Importability & version bump ────────────────────────────────────


def test_ops_do_module_imports() -> None:
    """ops_do exposes the documented public symbols."""
    from lilith_cli import ops_do

    assert callable(ops_do.run_do)
    assert callable(ops_do.do)
    assert ops_do.do_app.name == "do" or (
        isinstance(ops_do.do_app.name, tuple) and ops_do.do_app.name[0] == "do"
    )


def test_version_bumped_to_4_4_0() -> None:
    """lilith-cli version must be 4.4.0 after the A4 pantheon passthrough slice."""
    import lilith_cli
    from lilith_cli.main import __version__

    assert __version__ == "4.4.0"
    assert lilith_cli.__version__ == "4.4.0"


def test_app_registers_do() -> None:
    """main.app should expose ``do`` alongside the A1-C commands."""
    from lilith_cli.main import app

    registered = set(app._registered_commands)
    assert "do" in registered, f"do missing from {registered}"
    # A1 + A2 + A3 + C commands must still be present (no regressions).
    assert "agents" in registered
    assert "bus" in registered
    assert "ask" in registered
    assert "memory" in registered
    assert "spawn" in registered
    assert "queue" in registered
    assert "work" in registered


# ── run_do — happy path (success propagates) ───────────────────────


def test_run_do_propagates_spawn_exit_zero(
    fake_repo_root: Path,
    fake_channel: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Injected lookup returns Hela + the fake channel exits 0 →
    ``run_do`` returns 0 and the spawn side effects land on the bus.
    """
    from lilith_cli import ops_do

    bus_db = _bus_path(fake_repo_root)

    code = ops_do.run_do(
        "audit this",
        channel="fake",
        timeout=30,
        db=bus_db,
        repo_root=fake_repo_root,
        route_lookup=lambda _t: _make_route("hela", score=0.42),
    )
    assert code == 0
    out = capsys.readouterr().out
    # The decision line must mention the agent and the score.
    assert "hela" in out
    assert "0.4" in out
    # The reason line should reflect the matched tags we seeded.
    assert "tags=memory" in out or "memory" in out
    # The spawn core still published its start/done pair.
    topics = [r["topic"] for r in _read_bus_messages(bus_db)]
    assert "spawn.hela" in topics
    assert "spawn.hela.done" in topics


def test_run_do_propagates_spawn_exit_nonzero(
    fake_repo_root: Path,
    failing_channel: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Injected lookup returns Hela + the failing channel exits 7 →
    ``run_do`` returns 7 (no swallowing).
    """
    from lilith_cli import ops_do

    bus_db = _bus_path(fake_repo_root)

    code = ops_do.run_do(
        "explode",
        channel="fail7",
        timeout=30,
        db=bus_db,
        repo_root=fake_repo_root,
        route_lookup=lambda _t: _make_route("hela"),
    )
    assert code == 7
    out = capsys.readouterr().out
    assert "hela" in out


# ── run_do — router miss (exit 3) ─────────────────────────────────


def test_run_do_router_miss_exits_three(
    fake_repo_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A lookup that returns ``None`` → exit 3, hint printed, no spawn
    side effects on the bus.
    """
    from lilith_cli import ops_do

    bus_db = _bus_path(fake_repo_root)

    def always_miss(_task: str):
        return None

    code = ops_do.run_do(
        "task without a clear home",
        channel="minimax",
        timeout=30,
        db=bus_db,
        repo_root=fake_repo_root,
        route_lookup=always_miss,
    )
    assert code == ops_do.EXIT_NO_MATCH
    assert code == 3
    out = capsys.readouterr().out
    # Strip any whitespace so rich's terminal-width wrapping doesn't
    # split our hint string across a newline.
    flat = " ".join(out.split())
    assert "ningún agente matchea" in flat
    assert "lilith spawn" in flat
    assert "lilith queue add --agent" in flat

    # No spawn side effects: the bus DB must either be empty or hold
    # only pre-existing rows.
    if bus_db.exists():
        spawn_rows = [r for r in _read_bus_messages(bus_db) if r["topic"].startswith("spawn.")]
        assert spawn_rows == []


# ── run_do — --dry-run (no side effects) ───────────────────────────


def test_run_do_dry_run_prints_decision_without_spawning(
    fake_repo_root: Path,
    fake_channel: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--dry-run`` returns 0, prints the routing decision, and **does
    not** invoke the spawn core (no bus writes, no log file).
    """
    from lilith_cli import ops_do

    bus_db = _bus_path(fake_repo_root)
    log_dir = fake_repo_root / ".ygg" / "spawns"

    code = ops_do.run_do(
        "dry run me",
        channel="fake",
        timeout=30,
        dry_run=True,
        db=bus_db,
        repo_root=fake_repo_root,
        route_lookup=lambda _t: _make_route("hela", score=0.55),
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "hela" in out
    assert "0.5" in out
    assert "--dry-run" in out

    # No bus writes, no log file.
    if bus_db.exists():
        assert _read_bus_messages(bus_db) == []
    assert not log_dir.exists() or list(log_dir.iterdir()) == []


# ── run_do — input validation ────────────────────────────────────


def test_run_do_rejects_empty_task(fake_repo_root: Path) -> None:
    """Empty / whitespace task → exit 2, no bus created."""
    from lilith_cli import ops_do

    bus_db = _bus_path(fake_repo_root)
    assert ops_do.run_do("", db=bus_db, repo_root=fake_repo_root) == 2
    assert ops_do.run_do("   ", db=bus_db, repo_root=fake_repo_root) == 2
    assert not bus_db.exists(), "no DB should be created for empty tasks"


def test_run_do_rejects_unknown_channel(
    fake_repo_root: Path,
) -> None:
    """Unknown ``--channel`` → exit 1, hint listing the available ones."""
    from lilith_cli import ops_do

    code = ops_do.run_do(
        "anything",
        channel="not-a-channel",
        db=_bus_path(fake_repo_root),
        repo_root=fake_repo_root,
        route_lookup=lambda _t: _make_route("hela"),
    )
    assert code == 1


# ── Live router composition (with the fake card registry) ─────────


def test_run_do_uses_live_router_when_no_lookup_injected(
    fake_repo_root: Path,
    fake_channel: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without an injected lookup, ``run_do`` must build the live route
    from the 14 Vanaheim cards. With a 2-card fixture (Hela + Mimir)
    the router picks one of them based on the request — the test
    asserts the choice is *one of* the seeded cards (deterministic via
    the router, no LLM).
    """
    from lilith_cli import ops_do

    bus_db = _bus_path(fake_repo_root)

    code = ops_do.run_do(
        "research memory please",
        channel="fake",
        timeout=30,
        db=bus_db,
        repo_root=fake_repo_root,
    )
    assert code == 0
    out = capsys.readouterr().out
    # The router should pick one of the two seeded cards.
    assert ("hela" in out) or ("mimir" in out)
    # The decision line is present.
    assert "→" in out or "do" in out.lower()


# ── Pure helper tests (no subprocess, no bus) ─────────────────────


class TestFormatRouteReason:
    """``_format_route_reason`` should turn an AgentRoute's matched
    fields into a short, human-readable string — no LLM involvement.
    """

    def test_uses_matched_tags_and_tools(self) -> None:
        from lilith_cli.ops_do import _format_route_reason
        from lilith_orchestrator.agent_router import AgentRoute

        route = AgentRoute(
            agent_type="hela",
            score=0.5,
            token_overlap=0.2,
            tag_hit=1.0,
            tool_fit=0.0,
            matched_tags=("archive",),
            matched_tools=("read_file",),
        )
        reason = _format_route_reason(route)
        assert "tags=archive" in reason
        assert "tools=read_file" in reason

    def test_falls_back_to_jaccard_when_no_signals(self) -> None:
        from lilith_cli.ops_do import _format_route_reason
        from lilith_orchestrator.agent_router import AgentRoute

        route = AgentRoute(
            agent_type="hela",
            score=0.1,
            token_overlap=0.1,
            tag_hit=0.0,
            tool_fit=0.0,
        )
        reason = _format_route_reason(route)
        assert "jaccard=" in reason


class TestBuildLiveDoRouteLookup:
    """The live router lookup must return the top-scoring definition
    as a full :class:`AgentRoute` (so the score survives printing).
    """

    def test_returns_agent_route_not_string(self) -> None:
        from lilith_cli.ops_do import _build_live_do_route_lookup
        from lilith_orchestrator.agent_router import AgentRoute
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
        lookup = _build_live_do_route_lookup(registry)
        result = lookup("memory lookup please")
        assert isinstance(result, AgentRoute)
        assert result.agent_type == "mimir"
        assert result.score > 0.0

    def test_returns_none_for_empty_registry(self) -> None:
        from lilith_cli.ops_do import _build_live_do_route_lookup

        assert _build_live_do_route_lookup([])("anything") is None


# ── Refactor sanity (composition over reimplementation) ───────────


def test_run_do_composes_run_spawn_not_subprocess_directly() -> None:
    """Whitebox guard: ``ops_do.run_do`` must delegate the spawn body
    to ``run_spawn`` (B is a composition, not a reimplementation).
    """
    from lilith_cli import ops_do

    src = Path(ops_do.__file__).read_text(encoding="utf-8")
    assert "run_spawn(" in src
    # And it must NOT roll its own Popen — that would be a
    # reimplementation, exactly what plan-29 forbids.
    assert "subprocess.Popen" not in src
    assert "subprocess.run" not in src


def test_run_do_reuses_ops_queue_loader() -> None:
    """Whitebox guard: ``ops_do`` must import ``_load_card_definitions``
    from :mod:`ops_queue` (the seam C landed). If a future refactor
    duplicates the loader, this test breaks loudly.
    """
    from lilith_cli import ops_do

    src = Path(ops_do.__file__).read_text(encoding="utf-8")
    assert "_load_card_definitions" in src
    assert "from lilith_cli.ops_queue import" in src


# ── CLI surface validation ────────────────────────────────────────


def test_do_app_cli_smoke_routing_only(
    fake_repo_root: Path,
    fake_channel: None,
) -> None:
    """Drive the CLI surface end-to-end with an empty-pedido rejected
    by ``run_do`` (exit 2). Asserts the CLI registers the documented
    parameters.
    """
    from lilith_cli.main import app as cli_app

    with pytest.raises(SystemExit) as excinfo:
        cli_app(
            ["do", ""],
            exit_on_error=False,
            console=None,
        )
    assert excinfo.value.code == 2

"""Tests for lilith_cli.ops_spawn — operator console (plan-29 A3).

Scope:

- ``--dry-run`` is exercised for the full definition-and-command path
  and must produce **zero** side effects (no log file, no goal, no
  bus row, no subprocess).
- A ``python -c`` fake channel (monkeypatched onto ``_CHANNELS``)
  drives the end-to-end path: the subprocess runs, stdout/stderr are
  captured into ``<root>/.ygg/spawns/<ts>-<agent>.log``, a goal is
  created, ``LilithBus`` receives both ``spawn.<agent>`` (start) and
  ``spawn.<agent>.done`` (finish) messages, a handoff is written, and
  the audit log gets two entries (start + done).
- Timeout (subprocess that hangs forever) exits non-zero with the log
  marked ``timed_out=true``.
- Unknown agent → friendly error listing valid agents, exit 1.
- Unknown channel → friendly error listing valid channels, exit 2.

All temp trees live inside the test's ``tmp_path`` and are removed by
pytest automatically. **No real LLM is ever invoked.**

The pattern for relocating the workspace root is the same one
``test_ops_knowledge.py`` and ``test_ops.py`` already use: monkeypatch
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

    Builds the minimum layout :func:`_resolve_yggdrasil_root` looks for,
    plus an empty ``.ygg/`` and a fake ``Vanaheim/Agents/agent_cards.yaml``
    with one ``Hela`` card.  Returns the relocated root.
    """
    from lilith_cli import main as cli_main

    fake_root = tmp_path / "Yggdrasil"
    fake_main = fake_root / "Asgard" / "lilith-cli" / "lilith_cli" / "main.py"
    fake_main.parent.mkdir(parents=True)
    fake_main.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli_main, "__file__", str(fake_main))
    # _resolve_yggdrasil_root prefers YGGDRASIL_ROOT over __file__, so unset
    # it during tests so the relocated tmp_path root is authoritative.
    monkeypatch.delenv("YGGDRASIL_ROOT", raising=False)

    # Minimum hub layout (so AgentCardLoader.from_vanaheim finds the YAML).
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
        "hooks: []\n",
        encoding="utf-8",
    )

    # .ygg dir the spawn expects (CrossContext / LilithBus write here).
    (fake_root / ".ygg").mkdir(parents=True, exist_ok=True)

    return fake_root


@pytest.fixture
def fake_repo_root_no_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Relocate the root with an EMPTY agent_cards.yaml — used to drive
    the 'no agents available' branch.

    The YAML file exists (so :meth:`AgentCardLoader.from_vanaheim` does
    not raise ``FileNotFoundError``) but contains no documents, so
    ``list_agents()`` returns ``[]``.
    """
    from lilith_cli import main as cli_main

    fake_root = tmp_path / "Yggdrasil"
    fake_main = fake_root / "Asgard" / "lilith-cli" / "lilith_cli" / "main.py"
    fake_main.parent.mkdir(parents=True)
    fake_main.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli_main, "__file__", str(fake_main))
    # _resolve_yggdrasil_root prefers YGGDRASIL_ROOT over __file__, so unset
    # it during tests so the relocated tmp_path root is authoritative.
    monkeypatch.delenv("YGGDRASIL_ROOT", raising=False)

    (fake_root / "Vanaheim" / "Agents").mkdir(parents=True)
    # Empty multi-document YAML — loader sees zero cards.
    (fake_root / "Vanaheim" / "Agents" / "agent_cards.yaml").write_text(
        "# empty on purpose\n", encoding="utf-8"
    )
    (fake_root / ".ygg").mkdir(parents=True, exist_ok=True)

    return fake_root


@pytest.fixture
def fake_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``ops_spawn._CHANNELS`` with a fake entry whose command
    is a Python ``-c`` shim. The shim prints a deterministic marker so
    tests can grep the captured stdout without invoking any LLM.

    The value is a **list[str]** — :func:`_resolve_channel_command`
    treats list values as a full argv override that replaces the
    opencode call entirely.
    """
    from lilith_cli import ops_spawn

    fake_argv = [sys.executable, "-c", "print('fake-agent-done')"]
    monkeypatch.setitem(ops_spawn._CHANNELS, "fake", fake_argv)


@pytest.fixture
def hanging_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``ops_spawn._CHANNELS`` with an entry that hangs forever
    so the timeout branch fires deterministically.
    """
    from lilith_cli import ops_spawn

    hanging = [sys.executable, "-c", "import time; time.sleep(30)"]
    monkeypatch.setitem(ops_spawn._CHANNELS, "hang", hanging)


# ── Importability & version bump ────────────────────────────────────


def test_ops_spawn_module_imports():
    """ops_spawn should expose spawn, spawn_app, _CHANNELS, spawns_log_dir."""
    from lilith_cli import ops_spawn

    assert callable(ops_spawn.spawn)
    assert isinstance(ops_spawn._CHANNELS, dict)
    # _CHANNELS must contain the three documented channels.
    # The two sub-agent defaults (minimax, opencode-go) plus sakana,
    # which is the orchestrator's own model but is still exposed as
    # a channel so a sub-agent can opt into it.
    assert {"minimax", "opencode-go", "sakana"} <= set(ops_spawn._CHANNELS)
    # spawn_app is a cyclopts App whose name is the string 'spawn'.
    name = ops_spawn.spawn_app.name
    assert name == "spawn" or (isinstance(name, tuple) and name[0] == "spawn")


def test_default_bus_db_path_uses_resolved_root(fake_repo_root: Path):
    """default_bus_db_path should land at <root>/.ygg/lilith_bus.db."""
    from lilith_cli import ops_spawn

    path = ops_spawn.default_bus_db_path()
    assert path == fake_repo_root / ".ygg" / "lilith_bus.db"


def test_spawns_log_dir_uses_resolved_root(fake_repo_root: Path):
    """spawns_log_dir should land at <root>/.ygg/spawns."""
    from lilith_cli import ops_spawn

    assert ops_spawn.spawns_log_dir() == fake_repo_root / ".ygg" / "spawns"


# ── --dry-run ───────────────────────────────────────────────────────


def test_dry_run_prints_definition_and_command_no_side_effects(
    fake_repo_root: Path, fake_channel: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """--dry-run must NOT spawn a subprocess, write a log, create a goal,
    or publish to the bus — only print the resolved definition + command.
    """
    from lilith_cli import ops_spawn

    ops_spawn.spawn(
        agent="hela",
        task="draft a release note",
        channel="fake",
        timeout=60,
        dry_run=True,
        db=fake_repo_root / ".ygg" / "lilith_bus.db",
        repo_root=fake_repo_root,
    )
    out = capsys.readouterr().out

    # Resolved definition printed.
    assert "--dry-run" in out
    assert "Resolved SubAgentDefinition" in out
    assert "hela" in out
    assert "Queen of Helheim" in out
    assert "fake" in out

    # Side-effect-free.
    spawns_dir = fake_repo_root / ".ygg" / "spawns"
    assert not spawns_dir.exists() or list(spawns_dir.iterdir()) == []
    goals_dir = fake_repo_root / ".ygg" / "goals"
    assert not goals_dir.exists() or list(goals_dir.iterdir()) == []
    bus_db = fake_repo_root / ".ygg" / "lilith_bus.db"
    assert not bus_db.exists()
    audit_log = fake_repo_root / ".ygg" / "audit.jsonl"
    assert not audit_log.exists()


# ── Full flow with fake channel ─────────────────────────────────────


def test_full_spawn_flow_writes_goal_bus_handoff_audit_log(
    fake_repo_root: Path, fake_channel: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """A complete spawn against the fake channel must:

    - run the subprocess (its stdout contains 'fake-agent-done')
    - write a log file under .ygg/spawns/
    - create a goal in .ygg/goals/
    - write a handoff in .ygg/handoffs/
    - publish two bus messages: 'spawn.hela' + 'spawn.hela.done'
    - append two audit entries (start + done)
    """
    from lilith_cli import ops_spawn

    bus_db = fake_repo_root / ".ygg" / "lilith_bus.db"

    ops_spawn.spawn(
        agent="hela",
        task="say hi",
        channel="fake",
        timeout=30,
        dry_run=False,
        db=bus_db,
        repo_root=fake_repo_root,
    )
    out = capsys.readouterr().out
    assert "Spawning" in out
    assert "completed" in out.lower() or "✓" in out

    # ── log file ───────────────────────────────────────────────────
    spawns_dir = fake_repo_root / ".ygg" / "spawns"
    assert spawns_dir.is_dir()
    logs = list(spawns_dir.glob("*.log"))
    assert len(logs) == 1
    log_text = logs[0].read_text(encoding="utf-8")
    assert "agent: Hela" in log_text
    assert "fake-agent-done" in log_text
    assert "exit_code: 0" in log_text

    # ── goal ───────────────────────────────────────────────────────
    goals_dir = fake_repo_root / ".ygg" / "goals"
    assert goals_dir.is_dir()
    goal_files = list(goals_dir.glob("*.json"))
    assert len(goal_files) == 1
    goal_obj = json.loads(goal_files[0].read_text(encoding="utf-8"))
    assert goal_obj["name"].startswith("spawn:")
    assert "Hela" in goal_obj["name"] or "hela" in goal_obj["name"]
    assert len(goal_obj["turns"]) == 1
    assert goal_obj["turns"][0]["agent"] == "Hela"
    assert goal_obj["turns"][0]["action"] == "spawn-run"
    assert "exit=0" in goal_obj["turns"][0]["evidence"]

    # ── handoff ────────────────────────────────────────────────────
    handoffs_dir = fake_repo_root / ".ygg" / "handoffs"
    assert handoffs_dir.is_dir()
    handoff_files = list(handoffs_dir.glob("*.json"))
    assert len(handoff_files) == 1
    handoff_obj = json.loads(handoff_files[0].read_text(encoding="utf-8"))
    assert handoff_obj["goal_id"] == goal_obj["id"]
    assert handoff_obj["summary"]["total_turns"] == 1
    assert handoff_obj["summary"]["last_agent"] == "Hela"

    # ── bus messages ───────────────────────────────────────────────
    assert bus_db.is_file()
    with sqlite3.connect(bus_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT topic, role, payload FROM messages ORDER BY id ASC").fetchall()
    topics = [r["topic"] for r in rows]
    assert "spawn.hela" in topics
    assert "spawn.hela.done" in topics
    # Both messages carry the same role tag (from the card).
    for r in rows:
        assert r["role"] == "Queen of Helheim"

    start_payload = json.loads(next(r["payload"] for r in rows if r["topic"] == "spawn.hela"))
    done_payload = json.loads(next(r["payload"] for r in rows if r["topic"] == "spawn.hela.done"))
    assert start_payload["goal_id"] == goal_obj["id"]
    assert start_payload["channel"] == "fake"
    assert done_payload["exit_code"] == 0
    assert done_payload["timed_out"] is False

    # ── audit log ──────────────────────────────────────────────────
    audit_log = fake_repo_root / ".ygg" / "audit.jsonl"
    assert audit_log.is_file()
    audit_lines = [
        json.loads(line)
        for line in audit_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(audit_lines) == 2
    hooks = [ev["hook_type"] for ev in audit_lines]
    assert hooks == ["spawn.start", "spawn.done"]
    for ev in audit_lines:
        assert ev["policy"] == "spawn"
        assert ev["agent"] == "Hela"
        assert ev["action"] == "spawn"


# ── Timeout ─────────────────────────────────────────────────────────


def test_spawn_timeout_writes_log_marks_timed_out(
    fake_repo_root: Path, hanging_channel: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """A hanging subprocess must hit the timeout branch, exit 124,
    log ``timed_out=true``, and still record a goal + handoff + bus
    done-event.
    """
    from lilith_cli import ops_spawn

    bus_db = fake_repo_root / ".ygg" / "lilith_bus.db"

    with pytest.raises(SystemExit) as excinfo:
        ops_spawn.spawn(
            agent="hela",
            task="hang on me",
            channel="hang",
            timeout=1,  # 1-second cap → the sleeper blows past it fast
            dry_run=False,
            db=bus_db,
            repo_root=fake_repo_root,
        )
    assert excinfo.value.code == 124

    out = capsys.readouterr().out
    assert "timed out" in out.lower() or "timeout" in out.lower()

    spawns_dir = fake_repo_root / ".ygg" / "spawns"
    log_text = next(spawns_dir.glob("*.log")).read_text(encoding="utf-8")
    assert "timed_out: True" in log_text
    assert "exit_code: -1" in log_text

    # Bus done message should still carry timed_out=True.
    with sqlite3.connect(bus_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT payload FROM messages WHERE topic = ?",
            ("spawn.hela.done",),
        ).fetchone()
    assert row is not None
    payload = json.loads(row["payload"])
    assert payload["timed_out"] is True


# ── Error paths ─────────────────────────────────────────────────────


def test_unknown_agent_lists_valid_agents(
    fake_repo_root: Path, fake_channel: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unknown agent name should exit 1 and list the available cards."""
    from lilith_cli import ops_spawn

    with pytest.raises(SystemExit) as excinfo:
        ops_spawn.spawn(
            agent="Loki",
            task="anything",
            channel="fake",
            timeout=10,
            dry_run=False,
            db=fake_repo_root / ".ygg" / "lilith_bus.db",
            repo_root=fake_repo_root,
        )
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "Loki" in out
    assert "Hela" in out
    assert "Available agents" in out


def test_unknown_channel_lists_valid_channels(
    fake_repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unknown channel should exit 2 BEFORE touching the agent loader."""
    from lilith_cli import ops_spawn

    with pytest.raises(SystemExit) as excinfo:
        ops_spawn.spawn(
            agent="hela",
            task="anything",
            channel="deepseek",
            timeout=10,
            dry_run=True,  # even dry-run should reject early
            db=fake_repo_root / ".ygg" / "lilith_bus.db",
            repo_root=fake_repo_root,
        )
    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "deepseek" in out
    assert "minimax" in out
    assert "sakana" in out
    assert "opencode-go" in out


def test_empty_agent_list_reports_no_cards(
    fake_repo_root_no_yaml: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When the YAML is missing, the spawn should fail with a friendly
    'Available agents: (none)' message and exit 1.
    """
    from lilith_cli import ops_spawn

    with pytest.raises(SystemExit) as excinfo:
        ops_spawn.spawn(
            agent="hela",
            task="anything",
            channel="minimax",
            timeout=10,
            dry_run=True,
            db=fake_repo_root_no_yaml / ".ygg" / "lilith_bus.db",
            repo_root=fake_repo_root_no_yaml,
        )
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "(none)" in out


# ── Cyclopts registration ───────────────────────────────────────────


def test_app_registers_spawn():
    """main.app should expose ``spawn`` alongside agents/bus/ask/memory."""
    from lilith_cli.main import app

    registered = set(app._registered_commands)
    assert "spawn" in registered, f"spawn missing from {registered}"
    # A1 + A2 commands must still be present.
    assert "agents" in registered
    assert "bus" in registered
    assert "ask" in registered
    assert "memory" in registered


def test_spawn_app_default_command_is_spawn():
    """The spawn_app's default callable should be the ``spawn`` function."""
    from lilith_cli import ops_spawn

    # Cyclopts stores the @default-decorated function on ``default_command``.
    assert ops_spawn.spawn_app.default_command is ops_spawn.spawn

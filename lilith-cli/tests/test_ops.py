"""Tests for lilith_cli.ops — operator console (plan-29 A1).

Scope:
  - ``bus tail/publish/claim/ack`` exercised against a **real**
    :class:`lilith_core.bus.LilithBus` on a tmp_path SQLite file (no
    mocks — the bus is small and deterministic, and this is the
    integration boundary we care about).
  - ``agents`` exercised against a minimal in-tree YAML fixture so the
    test never touches Vanaheim/ (which lives outside the package).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


# Ensure the package source wins over any stray single-file module at the
# repo root (mirrors conftest.py but we duplicate locally because tests in
# this file may be collected by other pytest entry points).
_PKG_DIR = str(Path(__file__).resolve().parent.parent)
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def fixture_repo_root(tmp_path: Path) -> Path:
    """Create a minimal Vanaheim/Agents/agent_cards.yaml under tmp_path.

    Two cards: ``Odin`` (level 2, 3 tools, 2 hooks) and ``Mimir`` (level
    1, 1 tool, no hooks). Tests pass this directory as ``--repo-root``.
    """
    cards_dir = tmp_path / "Vanaheim" / "Agents"
    cards_dir.mkdir(parents=True, exist_ok=True)
    cards_yaml = cards_dir / "agent_cards.yaml"
    cards_yaml.write_text(
        "---\n"
        "name: Odin\n"
        "role: All-father orchestrator\n"
        "level: 2\n"
        "model: glm-5.2\n"
        "tools:\n"
        "  - terminal\n"
        "  - filesystem\n"
        "  - web_search\n"
        "description: Oversees the nine realms and dispatches agents.\n"
        "hooks:\n"
        "  - pre_tool_use\n"
        "  - post_tool_use\n"
        "---\n"
        "name: Mimir\n"
        "role: Knowledge keeper\n"
        "level: 1\n"
        "model: glm-5.2\n"
        "tools:\n"
        "  - rag_search\n"
        "description: Answers questions from the long-term memory store.\n"
        "hooks: []\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def bus_db(tmp_path: Path) -> Path:
    """Path to a fresh, empty bus DB file inside tmp_path."""
    db_dir = tmp_path / "bus"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "lilith_bus.db"


# ── Importability & version bump ────────────────────────────────────


def test_ops_module_imports():
    """ops module should expose agents, bus_app, default_bus_db_path."""
    from lilith_cli import ops

    assert callable(ops.agents)
    assert callable(ops.default_bus_db_path)
    # bus_app is a cyclopts App whose ``name`` is a tuple ('bus',).
    name = ops.bus_app.name if isinstance(ops.bus_app.name, str) else ops.bus_app.name[0]
    assert name == "bus"


def test_default_bus_db_path_uses_resolved_root(monkeypatch, tmp_path: Path):
    """default_bus_db_path should point at <root>/.ygg/lilith_bus.db."""
    from lilith_cli import main as cli_main
    from lilith_cli import ops

    # Mirror the existing test_resolve_yggdrasil_root layout: parents[3]
    # of the fake main.py resolves back to fake_root.
    fake_root = tmp_path / "Yggdrasil"
    fake_module = fake_root / "Asgard" / "lilith-cli" / "lilith_cli" / "main.py"
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli_main, "__file__", str(fake_module))

    path = ops.default_bus_db_path()
    assert path == fake_root / ".ygg" / "lilith_bus.db"
    assert isinstance(path, Path)


def test_version_bumped_to_4_4_0():
    """lilith_cli version should be 4.4.0 after slice A4 (was 4.2.0)."""

    import lilith_cli
    from lilith_cli.main import __version__

    assert lilith_cli.__version__ == "4.4.0"
    assert __version__ == "4.4.0"


# ── agents ──────────────────────────────────────────────────────────


def test_agents_table_against_fixture(fixture_repo_root: Path, capsys):
    """agents (no --show) should print a table covering both fixture cards."""
    from lilith_cli.ops import agents

    agents(show=None, repo_root=fixture_repo_root)
    out = capsys.readouterr().out

    assert "Odin" in out
    assert "Mimir" in out
    assert "All-father orchestrator" in out
    assert "Knowledge keeper" in out
    assert "glm-5.2" in out
    # Tool counts: Odin has 3, Mimir has 1.
    assert " 3 " in out or "│ 3 " in out
    # Hook counts: Odin has 2, Mimir has 0.
    assert " 2 " in out or "│ 2 " in out
    # Footer points at the fixture path. Rich may soft-wrap the long path
    # so check for the suffix of the path instead of an exact substring.
    expected_suffix = str(fixture_repo_root / "Vanaheim" / "Agents" / "agent_cards.yaml")
    assert expected_suffix.split("test_", maxsplit=1)[0] in out or expected_suffix in out
    assert "agent_cards.yaml" in out


def test_agents_show_detail(fixture_repo_root: Path, capsys):
    """agents --show <name> should print full detail (tools + hooks + description)."""
    from lilith_cli.ops import agents

    agents(show="Odin", repo_root=fixture_repo_root)
    out = capsys.readouterr().out

    assert "Odin" in out
    assert "All-father orchestrator" in out
    assert "Oversees the nine realms" in out
    # Tools list
    for t in ("terminal", "filesystem", "web_search"):
        assert t in out
    # Hooks list
    for h in ("pre_tool_use", "post_tool_use"):
        assert h in out


def test_agents_show_case_insensitive(fixture_repo_root: Path, capsys):
    """--show should match case-insensitively."""
    from lilith_cli.ops import agents

    agents(show="mImIr", repo_root=fixture_repo_root)
    out = capsys.readouterr().out
    assert "Mimir" in out
    assert "rag_search" in out


def test_agents_show_unknown_exits(fixture_repo_root: Path, capsys):
    """--show with an unknown name should exit non-zero and list available cards."""
    from lilith_cli.ops import agents

    with pytest.raises(SystemExit) as excinfo:
        agents(show="Loki", repo_root=fixture_repo_root)

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "Loki" in out
    assert "Odin" in out
    assert "Mimir" in out


# ── bus: publish → tail → claim → ack ───────────────────────────────


def test_bus_publish_returns_success_message(bus_db: Path, capsys):
    """publish should write to the bus and print the assigned id."""
    from lilith_cli.ops import publish

    publish(
        topic="test.greet",
        payload_json=json.dumps({"hello": "world"}),
        role="r1",
        db=bus_db,
    )
    out = capsys.readouterr().out
    assert "Published" in out
    assert "id=1" in out
    assert "topic='test.greet'" in out
    assert "role=r1" in out


def test_bus_publish_rejects_non_object_payload(bus_db: Path, capsys):
    """publish should refuse non-dict JSON payloads with exit code 2."""
    from lilith_cli.ops import publish

    with pytest.raises(SystemExit) as excinfo:
        publish(topic="t", payload_json=json.dumps([1, 2, 3]), db=bus_db)

    assert excinfo.value.code == 2
    assert "must be a JSON object" in capsys.readouterr().out


def test_bus_publish_rejects_invalid_json(bus_db: Path, capsys):
    """publish should refuse malformed JSON with exit code 2."""
    from lilith_cli.ops import publish

    with pytest.raises(SystemExit) as excinfo:
        publish(topic="t", payload_json="{not json", db=bus_db)

    assert excinfo.value.code == 2
    assert "not valid JSON" in capsys.readouterr().out


def test_bus_tail_against_published_messages(bus_db: Path, capsys):
    """tail should show previously published messages and respect the topic pattern."""
    from lilith_cli.ops import publish, tail
    from lilith_core.bus import LilithBus

    bus = LilithBus(bus_db)
    bus.publish("alpha.one", {"n": 1}, role="r1")
    bus.publish("alpha.two", {"n": 2})
    bus.publish("beta.one", {"n": 3}, role="r2")
    bus.close()

    tail(topic="alpha.**", limit=10, db=bus_db)
    out_alpha = capsys.readouterr().out
    assert "alpha.one" in out_alpha
    assert "alpha.two" in out_alpha
    assert "beta.one" not in out_alpha

    tail(topic="**", limit=10, db=bus_db)
    out_all = capsys.readouterr().out
    assert "alpha.one" in out_all
    assert "beta.one" in out_all
    assert "3 message(s)" in out_all


def test_bus_tail_empty(bus_db: Path, capsys):
    """tail on an empty bus should print a friendly no-match message."""
    from lilith_cli.ops import tail

    tail(topic="**", limit=10, db=bus_db)
    out = capsys.readouterr().out
    assert "No messages matching" in out


def test_bus_claim_returns_message_and_marks_claimer(bus_db: Path, capsys):
    """claim should yield the next unclaimed message for the role."""
    from lilith_cli.ops import claim
    from lilith_core.bus import LilithBus

    bus = LilithBus(bus_db)
    bus.publish("a", {"x": 1}, role="r1")
    bus.publish("b", {"x": 2}, role="r1")
    bus.close()

    m = claim(role="r1", claimer="skadi", db=bus_db)
    assert m is not None
    assert m.topic == "a"
    assert m.claimed_by == "skadi"

    # Second claim from a different claimer should yield the next message.
    m2 = claim(role="r1", claimer="sakana", db=bus_db)
    assert m2 is not None
    assert m2.topic == "b"
    assert m2.claimed_by == "sakana"

    out = capsys.readouterr().out
    assert "Claimed id=1" in out
    assert "Claimed id=2" in out


def test_bus_claim_empty(bus_db: Path, capsys):
    """claim on an empty queue should print 'vacío' and return None."""
    from lilith_cli.ops import claim

    result = claim(role="r1", claimer="skadi", db=bus_db)
    assert result is None
    assert "vacío" in capsys.readouterr().out


def test_bus_ack_marks_delivered(bus_db: Path, capsys):
    """ack should mark the message delivered for the matching claimer."""
    from lilith_cli.ops import ack, claim
    from lilith_core.bus import LilithBus

    bus = LilithBus(bus_db)
    bus.publish("a", {"x": 1}, role="r1")
    bus.close()

    claim(role="r1", claimer="skadi", db=bus_db)
    ack(msg_id=1, claimer="skadi", db=bus_db)

    # Confirm via a direct DB read: delivered_at must be set, claimed_by
    # must still match.
    bus = LilithBus(bus_db)
    row = bus._conn.execute("SELECT claimed_by, delivered_at FROM messages WHERE id = 1").fetchone()
    bus.close()
    assert row["claimed_by"] == "skadi"
    assert row["delivered_at"] is not None

    assert "Acked id=1" in capsys.readouterr().out


def test_bus_ack_wrong_claimer_exits(bus_db: Path, capsys):
    """ack from the wrong claimer should exit 1 and not deliver."""
    from lilith_cli.ops import ack, claim
    from lilith_core.bus import LilithBus

    bus = LilithBus(bus_db)
    bus.publish("a", {"x": 1}, role="r1")
    bus.close()

    claim(role="r1", claimer="skadi", db=bus_db)

    with pytest.raises(SystemExit) as excinfo:
        ack(msg_id=1, claimer="sakana", db=bus_db)
    assert excinfo.value.code == 1
    assert "Could not ack" in capsys.readouterr().out


def test_bus_ack_already_delivered_exits(bus_db: Path, capsys):
    """ack-ing an already-delivered message should exit 1."""
    from lilith_cli.ops import ack, claim
    from lilith_core.bus import LilithBus

    bus = LilithBus(bus_db)
    bus.publish("a", {"x": 1}, role="r1")
    bus.close()

    claim(role="r1", claimer="skadi", db=bus_db)
    ack(msg_id=1, claimer="skadi", db=bus_db)

    with pytest.raises(SystemExit) as excinfo:
        ack(msg_id=1, claimer="skadi", db=bus_db)
    assert excinfo.value.code == 1


# ── Cyclopts registration ───────────────────────────────────────────


def test_app_registers_agents_and_bus():
    """main.app should expose both ``agents`` and the ``bus`` sub-app."""
    from lilith_cli.main import app

    # Cyclopts tracks registered subcommands in ``_registered_commands``
    # (a dict keyed by command name). Both ``agents`` and ``bus`` should
    # be present at the top level after A1 wiring.
    registered = set(app._registered_commands)
    assert "agents" in registered, f"agents missing from {registered}"
    assert "bus" in registered, f"bus missing from {registered}"


def test_bus_app_subcommands():
    """bus_app should expose tail, publish, claim, ack."""
    from lilith_cli.ops import bus_app

    registered = set(bus_app._registered_commands)
    assert {"tail", "publish", "claim", "ack"} <= registered

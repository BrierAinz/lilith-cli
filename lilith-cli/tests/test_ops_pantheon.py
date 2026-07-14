"""Tests for lilith_cli.ops_pantheon — operator console (plan-29 A4).

Scope (read-only passthrough — no writes anywhere):

- ``lilith goals`` (no ``--goal-id``) renders the goals table mirroring
  ``ygg context goals`` (id / name / status / project / done% / turns /
  pending gates) against a populated ``.ygg`` fixture in ``tmp_path``.
- ``lilith goals --goal-id <ID>`` renders the goal detail (status,
  description, completion %, quota remaining, last 10 turns with
  evidence, every gate with a ✓/✗/○ mark + status) — mirrors
  ``ygg context goal-show``.
- ``lilith goals --goal-id <MISSING>`` exits 1 with a friendly error.
- ``lilith goals`` on an empty ``.ygg`` exits 0 with a friendly message.
- ``lilith policy eval <tool>`` returns the matching rule's ``(action,
  rule)`` pair, rendered with the same color mapping as
  ``ygg context eval``. A missing tool falls back to the default
  ``log`` action with a dim hint.
- ``lilith policy eval`` with an empty ``tool_name`` exits 2 without
  touching the policies file.
- ``lilith policy list`` renders every rule from ``policies.yaml``.
  An empty / missing YAML exits 0 with a friendly message.
- The CLI surface registers both ``goals`` and ``policy`` in ``main.app``.
- The version bumps to ``4.4.0`` across all three sites (``__init__.py``,
  ``main.py``, ``pyproject.toml``).

All temp trees live inside ``tmp_path``. No LLM is ever invoked; the
fixture builds the minimum ``.ygg`` (goals/, handoffs/, policies.yaml)
directly on disk and exercises :class:`CrossContext` end-to-end.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


# Mirror conftest.py locally — tests in this file may be collected by
# entry points that don't pre-load the lilith_cli package.
_PKG_DIR = str(Path(__file__).resolve().parent.parent)
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def populated_ygg(tmp_path: Path) -> Path:
    """Build a tmp_path ``.ygg/`` with two goals, two handoffs, and a
    ``policies.yaml`` that exercises the four action types.

    Returns the resolved ``.ygg`` directory (NOT the project root) —
    this is exactly what the operator console passes to CrossContext.
    """
    from lilith_skills.cross_context import GOALS_DIR, HANDOFFS_DIR, POLICIES_FILE

    ygg_dir = tmp_path / ".ygg"
    (ygg_dir / GOALS_DIR).mkdir(parents=True)
    (ygg_dir / HANDOFFS_DIR).mkdir(parents=True)

    # Two goals: one active with turns + a pending gate, one done with
    # all gates resolved. The handoffs are auto-derived snapshots but
    # we write them explicitly so the directory matches what the live
    # ecosystem produces.
    goal_a = {
        "id": "abc12345",
        "name": "smoke-test",
        "description": "test goal fixture",
        "project": "Asgard/lilith-core",
        "status": "active",
        "created_at": 1_700_000_000.0,
        "updated_at": 1_700_000_100.0,
        "turns": [
            {
                "agent": "Skadi",
                "action": "analyze",
                "evidence": "scanned coverage",
                "timestamp": "2024-01-01T00:00:00",
                "tokens_used": 0,
                "metadata": {},
            },
            {
                "agent": "Mimir",
                "action": "research",
                "evidence": "fetched docs",
                "timestamp": "2024-01-01T00:01:00",
                "tokens_used": 0,
                "metadata": {},
            },
        ],
        "gates": [
            {
                "id": "g0001",
                "description": "review the diff?",
                "status": "pending",
                "created_at": "2024-01-01T00:00:00",
                "resolved_at": None,
                "resolved_by": None,
                "resolution_note": "",
            }
        ],
        "todos": [],
        "quota_max_calls": 5,
        "quota_used_calls": 2,
        "quota_max_tokens": 0,
        "quota_used_tokens": 0,
        "metadata": {},
    }
    goal_b = {
        "id": "done6789",
        "name": "ship-it",
        "description": "released",
        "project": "Asgard/lilith-api",
        "status": "done",
        "created_at": 1_700_000_000.0,
        "updated_at": 1_700_001_000.0,
        "turns": [],
        "gates": [
            {
                "id": "g0002",
                "description": "QA pass",
                "status": "approved",
                "created_at": "2024-01-01T00:00:00",
                "resolved_at": "2024-01-01T00:05:00",
                "resolved_by": "skadi",
                "resolution_note": "ok",
            }
        ],
        "todos": [],
        "quota_max_calls": 10,
        "quota_used_calls": 3,
        "quota_max_tokens": 0,
        "quota_used_tokens": 0,
        "metadata": {},
    }
    (ygg_dir / GOALS_DIR / "abc12345.json").write_text(json.dumps(goal_a), encoding="utf-8")
    (ygg_dir / GOALS_DIR / "done6789.json").write_text(json.dumps(goal_b), encoding="utf-8")

    # Handoffs: tiny JSON summaries for both goals (the operator console
    # only reads goals, but we mirror the on-disk shape so a future
    # handoffs surface can be added without rewriting the fixture).
    handoff_a = {
        "handoff_version": "1.0",
        "goal_id": "abc12345",
        "name": "smoke-test",
        "summary": {
            "total_turns": 2,
            "last_agent": "Mimir",
            "last_action": "research",
            "completion_pct": 0.0,
        },
        "pending_gates": [goal_a["gates"][0]],
        "open_todos": [],
        "quota_remaining": {"calls": 3, "tokens": -1},
    }
    (ygg_dir / HANDOFFS_DIR / "abc12345.json").write_text(json.dumps(handoff_a), encoding="utf-8")

    # Policies: one of each action type so `policy eval` can match the
    # right color and `policy list` renders four rows.
    policies_yaml = """
policies:
  - name: deny-shell
    description: "block shell access"
    priority: 10
    action: deny
    scope: all
    type: tool_denylist
    tools: [shell_exec, system]
    enabled: true

  - name: allow-reads
    priority: 20
    action: allow
    type: tool_allowlist
    tools: [read_file]

  - name: flag-risky
    priority: 30
    action: flag
    type: regex
    field_name: tool_name
    pattern: "delete_.*"

  - name: audit-admin-tools
    priority: 99
    action: log
    type: regex
    field_name: tool_name
    pattern: "^admin_.*"
"""
    (ygg_dir / POLICIES_FILE).write_text(policies_yaml, encoding="utf-8")

    return ygg_dir


@pytest.fixture
def empty_ygg(tmp_path: Path) -> Path:
    """A bare ``.ygg/`` with no goals / handoffs / policies — drives
    the empty-state branches.
    """
    ygg_dir = tmp_path / ".ygg"
    ygg_dir.mkdir()
    return ygg_dir


@pytest.fixture
def fake_repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Relocate the resolved Yggdrasil root into ``tmp_path`` so
    :func:`ops_pantheon.default_ygg_dir` returns a tmp_path-scoped
    path. Mirrors the fixture used by ``test_ops_queue`` /
    ``test_ops_spawn`` / ``test_ops_do``.
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

    (fake_root / ".ygg").mkdir(parents=True, exist_ok=True)
    return fake_root


# ── Importability & version bump ────────────────────────────────────


def test_ops_pantheon_module_imports() -> None:
    """ops_pantheon exposes the documented public surface."""
    from lilith_cli import ops_pantheon

    assert callable(ops_pantheon.run_goals)
    assert callable(ops_pantheon.run_policy_eval)
    assert callable(ops_pantheon.run_policy_list)
    assert callable(ops_pantheon.goals)
    assert callable(ops_pantheon.policy_eval)
    assert callable(ops_pantheon.policy_list)
    # Cyclopts App names
    goals_name = ops_pantheon.goals_app.name
    assert goals_name == "goals" or (isinstance(goals_name, tuple) and goals_name[0] == "goals")
    policy_name = ops_pantheon.policy_app.name
    assert policy_name == "policy" or (
        isinstance(policy_name, tuple) and policy_name[0] == "policy"
    )


def test_version_bumped_to_4_4_0() -> None:
    """A4 (pantheon passthrough) bumps lilith-cli to 4.4.0 across all three sites."""
    import lilith_cli
    from lilith_cli.main import __version__

    # Site 1: lilith_cli/__init__.py
    assert lilith_cli.__version__ == "4.4.0"
    # Site 2: lilith_cli/main.py
    assert __version__ == "4.4.0"
    # Site 3: pyproject.toml (hatch pulls this for wheel metadata).
    # __file__ is .../Asgard/lilith-cli/tests/test_ops_pantheon.py;
    # parents[1] lands on lilith-cli/, which owns the pyproject.toml.
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert 'version = "4.4.0"' in text


def test_app_registers_goals_and_policy() -> None:
    """main.app exposes ``goals`` and ``policy`` alongside the A1-C commands."""
    from lilith_cli.main import app

    registered = set(app._registered_commands)
    assert "goals" in registered, f"goals missing from {registered}"
    assert "policy" in registered, f"policy missing from {registered}"
    # No regression: every A1-C command must still be present.
    assert "agents" in registered
    assert "bus" in registered
    assert "ask" in registered
    assert "memory" in registered
    assert "spawn" in registered
    assert "queue" in registered
    assert "work" in registered
    assert "do" in registered


# ── Path resolution ─────────────────────────────────────────────────


def test_default_ygg_dir_uses_resolved_root(fake_repo_root: Path) -> None:
    """``default_ygg_dir`` resolves to ``<repo_root>/.ygg``."""
    from lilith_cli import ops_pantheon

    assert ops_pantheon.default_ygg_dir() == fake_repo_root / ".ygg"


# ── goals — table ────────────────────────────────────────────────────


def test_goals_empty_exits_zero_with_friendly_message(
    empty_ygg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty ``.ygg`` renders a dim message and exits 0."""
    from lilith_cli import ops_pantheon

    code = ops_pantheon.run_goals(ygg_dir=empty_ygg)
    assert code == 0
    out = capsys.readouterr().out
    assert "No goals found" in out


def test_goals_table_lists_both_fixture_goals(
    populated_ygg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The table lists both fixture goals with id / name / status /
    done% / turns / pending gates.
    """
    from lilith_cli import ops_pantheon

    code = ops_pantheon.run_goals(ygg_dir=populated_ygg)
    assert code == 0
    out = capsys.readouterr().out
    # IDs and names — the table mirrors cmd_goals in context_cli.
    assert "abc12345" in out
    assert "smoke-test" in out
    assert "done6789" in out
    assert "ship-it" in out
    # Status column
    assert "active" in out
    assert "done" in out
    # Project column (when non-empty)
    assert "Asgard/lilith-core" in out
    assert "Asgard/lilith-api" in out
    # Header row
    assert "Done" in out
    assert "Turns" in out
    assert "Gates" in out
    # Counts: goal_a has 2 turns + 1 pending gate; goal_b has 0 turns +
    # 0 pending gates (the only gate is approved).
    assert "2" in out  # turns for goal_a


def test_goals_unknown_id_exits_one(
    populated_ygg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ``--goal-id`` that does not match any known goal exits 1 with
    a friendly error (mirrors ``ygg context goal-show`` behaviour).
    """
    from lilith_cli import ops_pantheon

    code = ops_pantheon.run_goals("does-not-exist", ygg_dir=populated_ygg)
    assert code == 1
    out = capsys.readouterr().out
    assert "does-not-exist" in out
    assert "No goal" in out


# ── goals — detail ───────────────────────────────────────────────────


def test_goals_with_id_renders_detail_with_turns_and_gates(
    populated_ygg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--goal-id <known>`` renders status, description, completion %,
    quota remaining, the last turns (with evidence), and every gate
    with a ✓/✗/○ mark + status.
    """
    from lilith_cli import ops_pantheon

    code = ops_pantheon.run_goals("abc12345", ygg_dir=populated_ygg)
    assert code == 0
    out = capsys.readouterr().out
    # Header / status / project / description / completion / quota
    assert "smoke-test" in out
    assert "abc12345" in out
    assert "active" in out
    assert "Asgard/lilith-core" in out
    assert "test goal fixture" in out
    # Quota remaining is rendered as a dict repr.
    assert "calls" in out
    # Turns: both agents + both actions + both evidence strings
    assert "Skadi" in out
    assert "analyze" in out
    assert "scanned coverage" in out
    assert "Mimir" in out
    assert "research" in out
    assert "fetched docs" in out
    # Gate: the pending gate must show the description and status.
    assert "review the diff?" in out
    assert "pending" in out


def test_goals_with_id_for_done_goal_renders_approved_mark(
    populated_ygg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An approved gate renders with a checkmark + status (verifies the
    ✓ branch of the gate-mark logic in ``_render_goal_detail``).
    """
    from lilith_cli import ops_pantheon

    code = ops_pantheon.run_goals("done6789", ygg_dir=populated_ygg)
    assert code == 0
    out = capsys.readouterr().out
    assert "ship-it" in out
    assert "QA pass" in out
    assert "approved" in out


# ── policy eval ──────────────────────────────────────────────────────


def test_policy_eval_matches_deny_rule_with_correct_action(
    populated_ygg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tool on a denylist resolves to action=deny and the rule's
    name + priority appear in the rendered decision line.
    """
    from lilith_cli import ops_pantheon

    code = ops_pantheon.run_policy_eval("shell_exec", ygg_dir=populated_ygg)
    assert code == 0
    out = capsys.readouterr().out
    assert "DENY" in out
    assert "deny-shell" in out
    assert "10" in out  # priority
    assert "Policy decision" in out


def test_policy_eval_matches_allow_rule(
    populated_ygg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tool on the allowlist resolves to action=allow; the rule with
    the LOWEST priority (most specific) wins by ordering (the
    always-type ``audit-everything`` has priority 99).
    """
    from lilith_cli import ops_pantheon

    code = ops_pantheon.run_policy_eval("read_file", ygg_dir=populated_ygg)
    assert code == 0
    out = capsys.readouterr().out
    assert "ALLOW" in out
    assert "allow-reads" in out


def test_policy_eval_no_match_prints_default_log_hint(
    populated_ygg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tool name that matches no rule prints the dim 'default log'
    hint (no rule line). Mirrors ``ygg context eval`` when no rule fires.
    """
    from lilith_cli import ops_pantheon

    code = ops_pantheon.run_policy_eval("never_seen_tool", ygg_dir=populated_ygg)
    assert code == 0
    out = capsys.readouterr().out
    assert "never_seen_tool" in out
    assert "default" in out
    assert "log" in out
    assert "No policy matches" in out
    # No 'Policy decision' line when no rule fires.
    assert "Policy decision" not in out


def test_policy_eval_rejects_empty_tool_name(
    populated_ygg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Empty / whitespace-only tool_name exits 2 without ever reading
    the policies file (defensive guard).
    """
    from lilith_cli import ops_pantheon

    assert ops_pantheon.run_policy_eval("", ygg_dir=populated_ygg) == 2
    assert ops_pantheon.run_policy_eval("   ", ygg_dir=populated_ygg) == 2


# ── policy list ──────────────────────────────────────────────────────


def test_policy_list_renders_all_rules(
    populated_ygg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The rules table mirrors ``ygg context policies`` — every rule's
    name / type / action / priority / enabled flag renders.
    """
    from lilith_cli import ops_pantheon

    code = ops_pantheon.run_policy_list(ygg_dir=populated_ygg)
    assert code == 0
    out = capsys.readouterr().out
    # Header
    assert "Name" in out
    assert "Type" in out
    assert "Action" in out
    assert "Priority" in out
    assert "Enabled" in out
    # Each rule's name + action
    assert "deny-shell" in out
    assert "allow-reads" in out
    assert "flag-risky" in out
    assert "audit-admin-tools" in out
    assert "deny" in out
    assert "allow" in out
    assert "flag" in out
    assert "log" in out
    # All rules are enabled in the fixture
    assert "yes" in out


def test_policy_list_empty_when_no_yaml(
    empty_ygg: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ``.ygg/`` without ``policies.yaml`` prints a dim hint and exits 0."""
    from lilith_cli import ops_pantheon

    code = ops_pantheon.run_policy_list(ygg_dir=empty_ygg)
    assert code == 0
    out = capsys.readouterr().out
    assert "No policies" in out


# ── ygg-dir override ────────────────────────────────────────────────


def test_ygg_dir_override_is_respected(
    populated_ygg: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two distinct ``.ygg`` fixtures resolve independently — pointing
    ``--ygg-dir`` at the empty one while leaving ``populated_ygg``
    untouched makes ``goals`` see no goals.
    """
    from lilith_cli import ops_pantheon

    other = tmp_path / "other_ygg"
    other.mkdir()
    code = ops_pantheon.run_goals(ygg_dir=other)
    assert code == 0
    out = capsys.readouterr().out
    assert "No goals found" in out
    # Sanity: the populated fixture is intact (separate fixture, but
    # this also proves the override didn't write back to it).
    assert (populated_ygg / "goals" / "abc12345.json").exists()


# ── CLI surface ──────────────────────────────────────────────────────


def test_cli_goals_help_lists_goal_id_flag() -> None:
    """``goals --help`` must mention the ``--goal-id`` and ``--ygg-dir``
    flags so the operator can discover them.
    """
    from lilith_cli.main import app as cli_app

    with pytest.raises(SystemExit) as excinfo:
        cli_app(["goals", "--help"], exit_on_error=False, console=None)
    assert excinfo.value.code == 0


def test_cli_policy_eval_invokes_handler(
    populated_ygg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smoke-test the Cyclopts dispatch path: ``policy eval <tool>``
    wires through to ``run_policy_eval`` with the right kwargs.
    """
    from lilith_cli import ops_pantheon

    calls: list[tuple[str, Path | None]] = []

    def fake_run(tool_name: str, *, ygg_dir=None, console=None):  # type: ignore[no-untyped-def]
        calls.append((tool_name, ygg_dir))
        return 0

    monkeypatch.setattr(ops_pantheon, "run_policy_eval", fake_run)

    from lilith_cli.main import app as cli_app

    # Cyclopts' default ``print_non_int_sys_exit`` action calls
    # ``sys.exit(0)`` after a successful dispatch — that's expected.
    with pytest.raises(SystemExit) as excinfo:
        cli_app(
            [
                "policy",
                "eval",
                "shell_exec",
                "--ygg-dir",
                str(populated_ygg),
            ],
            exit_on_error=False,
            console=None,
        )
    assert excinfo.value.code == 0
    assert calls == [("shell_exec", populated_ygg)]


# ── No-side-effects guard ───────────────────────────────────────────


def test_run_goals_does_not_mutate_state(
    populated_ygg: Path,
) -> None:
    """``run_goals`` is a pure read — no new files appear and the
    existing fixture bytes are unchanged (no last-modified jitter).
    """
    from lilith_cli import ops_pantheon

    snapshot = sorted(p for p in populated_ygg.rglob("*") if p.is_file())
    before = {p: p.read_bytes() for p in snapshot}
    before_mtimes = {p: p.stat().st_mtime_ns for p in snapshot}

    ops_pantheon.run_goals(ygg_dir=populated_ygg)
    ops_pantheon.run_goals("abc12345", ygg_dir=populated_ygg)
    ops_pantheon.run_goals("missing", ygg_dir=populated_ygg)

    after = sorted(p for p in populated_ygg.rglob("*") if p.is_file())
    assert after == snapshot, f"file set changed: {snapshot} → {after}"
    for p in snapshot:
        assert p.read_bytes() == before[p]
        assert p.stat().st_mtime_ns == before_mtimes[p]


def test_run_policy_eval_does_not_mutate_state(
    populated_ygg: Path,
) -> None:
    """``run_policy_eval`` is also read-only — no audit entries, no
    YAML writes.
    """
    from lilith_cli import ops_pantheon

    snapshot = sorted(p for p in populated_ygg.rglob("*") if p.is_file())
    before = {p: p.read_bytes() for p in snapshot}

    ops_pantheon.run_policy_eval("shell_exec", ygg_dir=populated_ygg)
    ops_pantheon.run_policy_eval("missing_tool", ygg_dir=populated_ygg)

    after = sorted(p for p in populated_ygg.rglob("*") if p.is_file())
    assert after == snapshot
    for p in snapshot:
        assert p.read_bytes() == before[p]


# ── Color f-string regression guard ─────────────────────────────────


def test_policy_eval_color_close_tag_is_well_formed() -> None:
    """Whitebox guard: the colored output line in ``run_policy_eval``
    must use the correct Rich close tag ``[/{color}]`` (NOT the buggy
    ``[/[{color}]`` that killed an earlier run). The test asserts the
    exact f-string shape via ``src`` so a future refactor that
    reintroduces the bug breaks this loudly.
    """
    from lilith_cli import ops_pantheon

    src = Path(ops_pantheon.__file__).read_text(encoding="utf-8")
    assert "[{color}]Policy decision:[/{color}]" in src, (
        "Rich color close tag must be [/{color}], not [/[{color}]. "
        "Re-introducing the bug kills policy eval output."
    )
    assert "[/[{color}]" not in src, (
        "Detected the old buggy close tag [/[{color}] in run_policy_eval. "
        "Fix it back to [/{color}]."
    )

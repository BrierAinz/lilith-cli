from io import StringIO
from types import SimpleNamespace

from lilith_cli.config import YggdrasilConfig
from lilith_cli.mission.inbox import MissionInbox
from lilith_tools.orchestration_state import OrchestrationStateStore
from rich.console import Console


def _seed(path):
    store = OrchestrationStateStore(path)
    routing = {
        "mission_spec": {
            "project_root": r"D:\\fixture",
            "metadata": {},
        },
        "team": [{"agent_id": "cocytus", "display_name": "Cocytus"}],
        "compute": {"cocytus": {"resource_id": "opencode_go"}},
    }
    parent = store.add_task(
        "Finish the remaster",
        task_id="mission-parent",
        status="completada",
        preset="lilith-mission",
        routing=routing,
        correlation_id="campaign-root",
        verification={"verified": True},
    )
    store.update_task(
        parent["id"],
        result="Implemented and verified.",
        usage={"total_tokens": 321, "cost_usd": 0.0},
    )
    store.append_post_mortem({
        "task_id": parent["id"],
        "correlation_id": "campaign-root",
        "preset": "lilith-mission",
        "success": True,
        "lessons": ["Use deterministic verification before close."],
    })
    child_routing = {
        "mission_spec": {
            "project_root": r"D:\\fixture",
            "metadata": {
                "parent_task_id": parent["id"],
                "reason": "Follow-up hardening",
            },
        }
    }
    store.add_task(
        "Harden the runtime",
        task_id="mission-child",
        status="pendiente",
        preset="lilith-mission",
        routing=child_routing,
        correlation_id="child-correlation",
    )
    return store


def _console():
    stream = StringIO()
    return Console(file=stream, force_terminal=False, color_system=None, width=140), stream


def _session(tmp_path):
    cfg = YggdrasilConfig(memory={"enabled": False}, provider="local", model="fixture")
    return SimpleNamespace(
        config=cfg,
        agent_mode="default",
        _project_root=str(tmp_path),
        _execution_profile="agent",
        get_tool_descriptions=lambda: [{"name": "mission_status"}],
    )
def test_inbox_derives_debrief_and_ack_preserves_result(tmp_path):
    path = tmp_path / "state.sqlite3"
    _seed(path)
    inbox = MissionInbox(path)
    reports = inbox.list(unread_only=True)
    assert len(reports) == 1
    report = reports[0]
    assert report.task_id == "mission-parent"
    assert report.debrief["result"] == "Implemented and verified."
    assert report.debrief["verification"]["verified"] is True
    assert report.debrief["lessons"] == [
        "Use deterministic verification before close."
    ]
    assert report.debrief["children"][0]["objective"] == "Harden the runtime"

    acknowledged = inbox.acknowledge("mission-parent")
    assert acknowledged.acknowledged is True
    assert inbox.unread_count() == 0
    task = next(row for row in _seedless(path)["tasks"] if row["id"] == "mission-parent")
    assert task["result"] == "Implemented and verified."
    assert task["verification"]["verified"] is True


def _seedless(path):
    return OrchestrationStateStore(path).get()
def test_mission_inbox_report_and_ack_commands(tmp_path, monkeypatch):
    from lilith_cli import agent_console

    path = tmp_path / "state.sqlite3"
    _seed(path)
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(path))
    session = _session(tmp_path)

    console, stream = _console()
    assert agent_console.console_command(session, "mission", "inbox", console)
    output = stream.getvalue()
    assert "MISSION INBOX" in output
    assert "mission-parent" in output

    console, stream = _console()
    assert agent_console.console_command(
        session, "mission", "report mission-parent", console
    )
    output = stream.getvalue()
    assert "MISSION DEBRIEF" in output
    assert "Implemented and verified." in output
    assert "Harden the runtime" in output

    console, stream = _console()
    assert agent_console.console_command(
        session, "mission", "ack mission-parent", console
    )
    assert "Acknowledged mission-parent" in stream.getvalue()
    assert MissionInbox(path).unread_count() == 0
def test_header_surfaces_unread_reports(tmp_path, monkeypatch):
    from lilith_cli import agent_console

    path = tmp_path / "state.sqlite3"
    _seed(path)
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(path))
    monkeypatch.setattr(agent_console, "windows_admin_status", lambda: True)
    console, stream = _console()
    agent_console.render_header(_session(tmp_path), console)
    output = stream.getvalue()
    assert "MISSION INBOX" in output
    assert "1 report(s) waiting" in output


def test_ide_mission_control_surfaces_unread_inbox(tmp_path, monkeypatch):
    from lilith_cli.ide import mission_panel

    path = tmp_path / "state.sqlite3"
    _seed(path)
    monkeypatch.setenv("YGGDRASIL_ORCHESTRATION_STATE", str(path))
    monkeypatch.setattr(
        mission_panel,
        "runtime_status",
        lambda: {
            "scheduler": {"installed": True, "state": "ready"},
            "queue": {"pending": 0, "blocked": 0},
        },
    )
    text = mission_panel.dashboard_text(_session(tmp_path))
    assert "MISSION INBOX · 1 unread" in text
    assert "mission-parent" in text
    assert "PASS" in text

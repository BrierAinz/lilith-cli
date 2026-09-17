from io import StringIO
from types import SimpleNamespace

from lilith_cli import agent_console
from lilith_cli.config import YggdrasilConfig
from lilith_cli.mission import presentation
from rich.console import Console


def _session(tmp_path):
    cfg = YggdrasilConfig(memory={"enabled": False}, provider="local", model="fixture")
    return SimpleNamespace(
        config=cfg,
        agent_mode="default",
        _project_root=str(tmp_path),
        _execution_profile="agent",
        get_tool_descriptions=lambda: [
            {"name": "mission_status"},
            {"name": "file_read"},
        ],
    )


def _console() -> tuple[Console, StringIO]:
    stream = StringIO()
    return Console(file=stream, force_terminal=False, color_system=None, width=120), stream


def _snapshot(active=True):
    mission = {
        "id": "mission-abc",
        "correlation_id": "abc123",
        "status": "delegada",
        "title": "Remaster Lilith",
        "routing": {
            "mission_spec": {"project_root": r"D:\workspace\lilith-cli"},
            "team": [
                {"display_name": "Demiurge"},
                {"display_name": "Cocytus"},
            ],
            "compute": {
                "demiurge": {"resource_id": "experimental_labs"},
                "cocytus": {"resource_id": "opencode_go"},
            },
        },
        "verification": {},
    }
    return {
        "active": mission if active else None,
        "selected": mission,
        "missions": [mission],
        "related": [{"status": "completada"}, {"status": "delegada"}],
        "progress": {"done": 1, "failed": 0, "total": 2},
    }


def test_header_is_queen_orchestrator_and_shows_active_mission(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path)
    console, stream = _console()
    monkeypatch.setattr(agent_console, "windows_admin_status", lambda: True)
    monkeypatch.setattr(presentation, "mission_snapshot", lambda limit=5: _snapshot())

    agent_console.render_header(session, console)
    rendered = stream.getvalue()
    assert "LILITH · QUEEN ORCHESTRATOR" in rendered
    assert "Ainz / Overlord" in rendered
    assert "MISSION mission-abc · RUNNING · 1/2 subtasks" in rendered
    assert "Demiurge, Cocytus" in rendered


def test_prompt_identity_is_clean_unicode() -> None:
    rendered = str(agent_console.prompt_fragments())
    assert "› Tú" in rendered
    assert "Ã" not in rendered


def test_mission_status_renders_card_without_model(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path)
    console, stream = _console()
    monkeypatch.setattr(presentation, "mission_snapshot", lambda limit=10: _snapshot())
    assert agent_console.console_command(session, "mission", "status", console)
    rendered = stream.getvalue()
    assert "LILITH · MISSION" in rendered
    assert "Remaster Lilith" in rendered
    assert "experimental_labs" in rendered


def test_mission_court_and_compute_are_human_readable(tmp_path) -> None:
    session = _session(tmp_path)
    console, stream = _console()
    assert agent_console.console_command(session, "mission", "court", console)
    rendered = stream.getvalue()
    assert "COURT" in rendered
    assert "Lilith" in rendered and "Demiurge" in rendered and "Sebas" in rendered

    console, stream = _console()
    assert agent_console.console_command(session, "mission", "compute", console)
    rendered = stream.getvalue()
    assert "COMPUTE" in rendered
    assert "Experimental Labs Free" in rendered
    assert "Claude Pro / Claude Code" in rendered


def test_repl_discovers_mission_command() -> None:
    from lilith_cli.repl import _SLASH_COMMANDS

    assert "/mission" in _SLASH_COMMANDS


def test_mission_activity_uses_court_identity_not_internal_tool_name() -> None:
    label = presentation.mission_tool_label(
        "mission_delegate", {"agent": "cocytus"}
    )
    assert label == "Cocytus · execute"
    line = presentation.mission_activity_line(
        "mission_delegate",
        {"agent": "cocytus"},
        {"compute_resource": "opencode_go"},
    )
    assert line is not None
    assert "Cocytus · execute" in line.plain
    assert "DONE" in line.plain
    assert "opencode_go" in line.plain
    assert "mission_delegate" not in line.plain


def test_sebas_desktop_activity_is_human_readable() -> None:
    assert presentation.mission_tool_label(
        "mission_admin_exec", {"action": "service_change"}
    ) == "Sebas · admin"


def test_court_is_canonical_local_surface(tmp_path) -> None:
    session = _session(tmp_path)
    console, stream = _console()
    assert agent_console.console_command(session, "court", "list", console)
    rendered = stream.getvalue()
    assert "COURT" in rendered
    assert "Demiurge" in rendered and "Cocytus" in rendered and "Sebas" in rendered

    console, stream = _console()
    assert agent_console.console_command(session, "court", "health", console)
    rendered = stream.getvalue()
    assert "COMPUTE" in rendered
    assert "Health" in rendered


def test_repl_discovers_court_command() -> None:
    from lilith_cli.repl import _SLASH_COMMANDS

    assert "/court" in _SLASH_COMMANDS

from __future__ import annotations

from typing import Any, ClassVar

from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from ..mission.compute import ComputeBroker
from ..mission.court import CourtRegistry
from ..mission.inbox import MissionInbox
from ..mission.learning import LearningEngine
from ..mission.presentation import mission_snapshot
from ..mission.runtime_status import runtime_status


def _line(title: str, value: object) -> str:
    return f"{title:<12} {value}"


def dashboard_text(session: Any) -> str:
    snap = mission_snapshot(limit=10)
    mission = snap.get("selected")
    rows = ["LILITH · MISSION CONTROL", ""]
    if isinstance(mission, dict):
        routing = mission.get("routing") or {}
        spec = routing.get("mission_spec") or {}
        rows.extend([
            _line("Status", str(mission.get("status") or "unknown").upper()),
            _line("Mission", mission.get("title") or "-"),
            _line("ID", mission.get("correlation_id") or "-"),
            _line("Root", spec.get("project_root") or "-"),
        ])
        progress = snap.get("progress") or {}
        if progress.get("total"):
            rows.append(_line(
                "Progress",
                f"{progress.get('done', 0)}/{progress.get('total', 0)} done · "
                f"{progress.get('failed', 0)} failed",
            ))
        longrun = routing.get("longrun") or {}
        if longrun:
            rows.append(_line("Longrun", longrun.get("run_id") or "registered"))
    else:
        rows.append("No mission registered. Describe an objective in chat.")

    inbox = MissionInbox()
    unread = inbox.list(unread_only=True, limit=3)
    rows.extend(["", f"MISSION INBOX · {inbox.unread_count()} unread"])
    if unread:
        for report in unread:
            verified = (report.debrief.get("verification") or {}).get("verified")
            marker = "PASS" if verified is True else "FAIL" if verified is False else "?"
            rows.append(
                f"  {report.task_id:<18} {report.status.upper():<11} {marker:<4} {report.objective}"
            )
    else:
        rows.append("  no unread mission reports")

    rows.extend(["", "COURT"])
    for agent in CourtRegistry.list_agents(include_queen=True):
        flags = []
        if agent.can_execute:
            flags.append("exec")
        if agent.can_review:
            flags.append("review")
        if agent.can_admin:
            flags.append("admin")
        rows.append(f"  {agent.display_name:<18} {agent.role} {'/'.join(flags)}")

    runtime = runtime_status()
    scheduler = runtime.get("scheduler") or {}
    queue = runtime.get("queue") or {}
    rows.extend([
        "",
        "RUNTIME",
        _line("Scheduled", "yes" if scheduler.get("installed") else "no"),
        _line("State", scheduler.get("state") or scheduler.get("reason") or "unknown"),
        _line("Queue", f"{queue.get('pending', 0)} pending · {queue.get('blocked', 0)} blocked"),
        _line("Next run", scheduler.get("next_run_time") or "-"),
        "",
        "COMPUTE",
    ])
    for resource in ComputeBroker().enabled():
        rows.append(
            f"  {resource.label:<28} {resource.transport}/{resource.adapter} · {resource.cost_class}"
        )

    tool_names = sorted(
        item.get("name", "")
        for item in session.get_tool_descriptions()
        if isinstance(item, dict)
    )
    mcp = [name for name in tool_names if name.startswith("mcp_")]
    mission_tools = [name for name in tool_names if name.startswith("mission_")]
    rows.extend([
        "",
        "CAPABILITIES",
        _line("Mission tools", len(mission_tools)),
        _line("MCP mounted", len(mcp)),
    ])
    if mcp:
        rows.extend(f"  {name}" for name in mcp[:12])
        if len(mcp) > 12:
            rows.append(f"  +{len(mcp) - 12} more")

    try:
        from lilith_skills.delegation_skills import DelegationSkillRegistry

        from ..robust_kit import catalog

        guide_count = len(catalog())
        recipe_count = len(DelegationSkillRegistry().list())
    except (OSError, ValueError, TypeError):
        guide_count = recipe_count = 0
    learning = LearningEngine().public_summary()
    rows.extend([
        _line("Skill guides", guide_count),
        _line("Skill recipes", recipe_count),
        _line("Route memory", len(learning.get("routes") or [])),
        _line("Skill candidates", len(learning.get("skill_candidates") or [])),
        "",
        "Esc close · F5 refresh · read-only dashboard",
    ])
    return "\n".join(str(row) for row in rows)


class MissionPanelScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss_panel", "Close"),
        Binding("f5", "refresh", "Refresh"),
    ]
    DEFAULT_CSS = """
    MissionPanelScreen { align: center middle; }
    #mission-dialog {
        width: 92%;
        max-width: 118;
        height: 88%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    #mission-dashboard { height: auto; }
    """

    def __init__(self, session: Any) -> None:
        super().__init__()
        self.session = session

    def compose(self):
        with Vertical(id="mission-dialog"), VerticalScroll():
            yield Static(dashboard_text(self.session), id="mission-dashboard")

    def action_dismiss_panel(self) -> None:
        self.dismiss(None)

    def action_refresh(self) -> None:
        self.query_one("#mission-dashboard", Static).update(
            dashboard_text(self.session)
        )

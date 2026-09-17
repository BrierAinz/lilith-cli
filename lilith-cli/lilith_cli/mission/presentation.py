from __future__ import annotations

from typing import Any

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .compute import ComputeBroker
from .compute_health import ComputeHealthResolver
from .court import CourtRegistry
from .kernel import MissionKernel
from .learning import LearningEngine

_TERMINAL = {"completada", "fallida", "cancelada"}
_STATUS = {
    "pendiente": ("PENDING", "yellow"),
    "delegada": ("RUNNING", "cyan"),
    "bloqueada": ("BLOCKED", "magenta"),
    "completada": ("DONE", "green"),
    "fallida": ("FAILED", "red"),
    "cancelada": ("CANCELLED", "dim"),
}


def _status(value: object) -> Text:
    raw = str(value or "unknown")
    label, style = _STATUS.get(raw, (raw.upper(), "white"))
    return Text(label, style=style)


def mission_snapshot(limit: int = 20) -> dict[str, Any]:
    kernel = MissionKernel()
    missions = kernel.list_missions(limit=limit)
    state = kernel._store().get()
    active = next((row for row in missions if row.get("status") not in _TERMINAL), None)
    selected = active or (missions[0] if missions else None)
    related: list[dict[str, Any]] = []
    if selected:
        correlation = selected.get("correlation_id")
        selected_id = selected.get("id")
        related = [
            row for row in state.get("tasks", [])
            if row.get("id") != selected_id and row.get("correlation_id") == correlation
        ]
    done = sum(row.get("status") == "completada" for row in related)
    failed = sum(row.get("status") == "fallida" for row in related)
    return {
        "active": active,
        "selected": selected,
        "missions": missions,
        "related": related,
        "progress": {"done": done, "failed": failed, "total": len(related)},
    }


def compact_mission_line() -> Text:
    snap = mission_snapshot(limit=5)
    active = snap["active"]
    if not active:
        return Text("Mission Kernel ready · no active mission", style="dim")
    routing = active.get("routing") or {}
    team = routing.get("team") or []
    names = [
        str(row.get("display_name") or row.get("agent_id"))
        for row in team if isinstance(row, dict)
    ]
    progress = snap["progress"]
    text = Text()
    text.append("MISSION ", style="bold")
    text.append(str(active.get("id") or ""), style="cyan")
    text.append(" · ")
    text.append_text(_status(active.get("status")))
    if progress["total"]:
        text.append(f" · {progress['done']}/{progress['total']} subtasks")
    if names:
        text.append(" · ")
        text.append(", ".join(names), style="dim")
    return text


def mission_panel() -> Panel:
    snap = mission_snapshot(limit=10)
    mission = snap["selected"]
    if not mission:
        return Panel(
            Text("No missions registered. Describe an objective to Lilith."),
            title="MISSION",
            border_style="cyan",
        )
    routing = mission.get("routing") or {}
    spec = routing.get("mission_spec") or {}
    table = Table.grid(padding=(0, 1))
    table.add_column(style="dim", width=12)
    table.add_column()
    table.add_row("Status", _status(mission.get("status")))
    table.add_row("Objective", Text(str(mission.get("title") or ""), style="bold"))
    table.add_row("Mission ID", str(mission.get("correlation_id") or ""))
    table.add_row("Root", str(spec.get("project_root") or ""))
    progress = snap["progress"]
    if progress["total"]:
        table.add_row(
            "Progress",
            f"{progress['done']}/{progress['total']} done · {progress['failed']} failed",
        )
    team = routing.get("team") or []
    names = [
        str(row.get("display_name") or row.get("agent_id"))
        for row in team if isinstance(row, dict)
    ]
    if names:
        table.add_row("Court", " · ".join(names))
    compute = routing.get("compute") or {}
    resources = [
        f"{agent}: {route.get('resource_id')}"
        for agent, route in compute.items() if isinstance(route, dict)
    ]
    if resources:
        table.add_row("Compute", " · ".join(resources))
    verification = mission.get("verification") or {}
    deterministic = verification.get("deterministic") or {}
    if deterministic:
        verified = bool(deterministic.get("verified"))
        detail = f"{'PASS' if verified else 'FAIL'} · exit {deterministic.get('exit_code')}"
        table.add_row("Verify", Text(detail, style="green" if verified else "red"))
    return Panel(table, title="LILITH · MISSION", border_style="cyan")


def court_table() -> Table:
    table = Table(title="COURT", header_style="bold cyan")
    table.add_column("Agent")
    table.add_column("Role")
    table.add_column("Exec", justify="center")
    table.add_column("Review", justify="center")
    table.add_column("Admin", justify="center")
    for agent in CourtRegistry.list_agents(include_queen=True):
        table.add_row(
            agent.display_name,
            agent.role,
            "yes" if agent.can_execute else "-",
            "yes" if agent.can_review else "-",
            "yes" if agent.can_admin else "-",
        )
    return table


def compute_table() -> Table:
    broker = ComputeBroker()
    resolver = ComputeHealthResolver(broker=broker)
    health = {
        row.resource_id: row
        for row in resolver.snapshot()
    }
    fabric_accounts = resolver.observed_fabric_accounts()
    table = Table(title="COMPUTE", header_style="bold cyan")
    table.add_column("Resource")
    table.add_column("Health")
    table.add_column("Transport")
    table.add_column("Cost")
    table.add_column("Reason")
    for resource in broker.enabled():
        row = health[resource.resource_id]
        state = Text(
            row.state.upper(),
            style="green" if row.available else "red",
        )
        table.add_row(
            resource.label,
            state,
            f"{resource.transport}/{resource.adapter}",
            resource.cost_class,
            row.reason,
        )
    for account in fabric_accounts:
        state_name = str(account.get("state") or "unknown").upper()
        available = state_name not in {"OPEN", "UNAVAILABLE"}
        table.add_row(
            f"↳ {account.get('provider')}/{account.get('account_id')}",
            Text(state_name, style="green" if available else "red"),
            "fabric/account",
            "observed",
            f"success={account.get('successes', 0)} · failures={account.get('failures', 0)}",
        )
    return table


def learning_table() -> Table:
    summary = LearningEngine().public_summary()
    table = Table(title="LEARNING", header_style="bold cyan")
    table.add_column("Role")
    table.add_column("Resource")
    table.add_column("Runs", justify="right")
    table.add_column("Success", justify="right")
    table.add_column("Avoid")
    rows = summary.get("routes") or summary.get("route_health") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        table.add_row(
            str(row.get("court_agent") or row.get("agent") or "-"),
            str(row.get("resource_id") or "-"),
            str(row.get("attempts") or 0),
            str(row.get("successes") or 0),
            "yes" if row.get("avoid") else "-",
        )
    return table


_MISSION_ACTIONS = {
    "mission_prepare": ("Lilith", "prepare"),
    "mission_complete": ("Lilith", "verify/close"),
    "mission_status": ("Lilith", "status"),
    "mission_court": ("Lilith", "court"),
    "mission_compute": ("Pandora's Actor", "route compute"),
    "mission_learning": ("Pandora's Actor", "learning"),
    "mission_authority": ("Lilith", "authority"),
    "mission_resume_expired": ("Lilith", "resume"),
    "mission_conclave": ("Court", "conclave"),
    "mission_desktop_observe": ("Sebas", "observe desktop"),
    "mission_desktop_act": ("Sebas", "computer use"),
    "mission_admin_exec": ("Sebas", "admin"),
    "mission_skill_run": ("Court", "skill"),
}


def mission_tool_label(name: str, args: dict[str, Any] | None = None) -> str:
    args = args or {}
    if name == "mission_delegate":
        agent = CourtRegistry.get(str(args.get("agent") or ""))
        return f"{agent.display_name if agent else 'Court'} · execute"
    if name in _MISSION_ACTIONS:
        actor, action = _MISSION_ACTIONS[name]
        return f"{actor} · {action}"
    legacy = CourtRegistry.get(name.removesuffix("_delegate"))
    if name.endswith("_delegate") and legacy is not None:
        return f"{legacy.display_name} · legacy adapter"
    return name


def mission_activity_line(
    name: str,
    args: dict[str, Any] | None,
    data: Any,
    *,
    error: bool = False,
    raw: str = "",
) -> Text | None:
    if not (name.startswith("mission_") or name.endswith("_delegate")):
        return None
    label = mission_tool_label(name, args)
    text = Text()
    text.append("✗ " if error else "✓ ", style="red" if error else "green")
    text.append(f"{label:<28}", style="bold")
    text.append("FAILED" if error else "DONE", style="red" if error else "green")
    detail = ""
    if isinstance(data, dict):
        if name == "mission_delegate":
            detail = str(data.get("compute_resource") or data.get("adapter") or "")
        elif name == "mission_prepare":
            reg = data.get("registration") or {}
            detail = str(reg.get("status") or data.get("recommended_route") or "")
        elif name == "mission_complete":
            task = data.get("task") or {}
            detail = str(task.get("status") or "")
        elif name == "mission_admin_exec":
            detail = str(data.get("effective_action") or data.get("error") or "")
        elif name == "mission_desktop_act":
            detail = str((args or {}).get("operation") or "")
        elif name == "mission_skill_run":
            detail = str((args or {}).get("name") or "")
    if error and not detail:
        detail = " ".join(str(raw).split())[:140]
    if detail:
        text.append(f" · {detail}", style="dim")
    return text


def runtime_table() -> Table:
    from .runtime_status import runtime_status

    data = runtime_status()
    scheduler = data.get("scheduler") or {}
    queue = data.get("queue") or {}
    budgets = data.get("budgets") or {}
    table = Table(title="RUNTIME", header_style="bold cyan")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Scheduled", "yes" if scheduler.get("installed") else "no")
    table.add_row("State", str(scheduler.get("state") or scheduler.get("reason") or "unknown"))
    table.add_row("Enabled", str(bool(scheduler.get("enabled"))))
    table.add_row("Last result", str(scheduler.get("last_result") if scheduler.get("last_result") is not None else "-"))
    table.add_row("Next run", str(scheduler.get("next_run_time") or "-"))
    table.add_row(
        "Queue",
        f"{queue.get('pending', 0)} pending · {queue.get('running', 0)} running · "
        f"{queue.get('blocked', 0)} blocked",
    )
    table.add_row("Next mission", str(queue.get("next_task_id") or "-"))
    usage = (budgets.get("usage") or {}).get("daily") or {}
    preflight = budgets.get("preflight") or {}
    table.add_row(
        "Daily budget",
        f"{usage.get('calls', 0)} calls · {usage.get('tokens', 0)} tokens · "
        f"${float(usage.get('cost_usd', 0.0) or 0.0):.4f}",
    )
    table.add_row(
        "Budget gate",
        "open" if preflight.get("allowed", True) else str(preflight.get("reason") or "blocked"),
    )
    table.add_row("Log", str(data.get("log_path") or "-"))
    return table


def inbox_table(*, unread_only: bool = True, limit: int = 20) -> Table:
    from .inbox import MissionInbox

    reports = MissionInbox().list(unread_only=unread_only, limit=limit)
    title = "MISSION INBOX · UNREAD" if unread_only else "MISSION INBOX"
    table = Table(title=title, header_style="bold cyan")
    table.add_column("Task")
    table.add_column("Status")
    table.add_column("Objective")
    table.add_column("Verify")
    table.add_column("Next", justify="right")
    for report in reports:
        verification = report.debrief.get("verification") or {}
        verified = verification.get("verified")
        verify = "PASS" if verified is True else "FAIL" if verified is False else "-"
        children = report.debrief.get("children") or []
        table.add_row(
            report.task_id,
            str(report.status).upper(),
            report.objective,
            verify,
            str(len(children)),
        )
    if not reports:
        table.add_row("-", "-", "No mission reports waiting.", "-", "0")
    return table


def debrief_panel(task_id: str) -> Panel:
    from .inbox import MissionInbox

    report = next(
        (row for row in MissionInbox().list(unread_only=False, limit=100)
         if row.task_id == str(task_id).strip()),
        None,
    )
    if report is None:
        return Panel(Text(f"Mission report not found: {task_id}"), title="DEBRIEF")
    data = report.debrief
    table = Table.grid(padding=(0, 1))
    table.add_column(style="dim", width=14)
    table.add_column()
    table.add_row("Status", _status(report.status))
    table.add_row("Objective", Text(report.objective, style="bold"))
    table.add_row("Result", str(data.get("result") or "-"))
    verification = data.get("verification") or {}
    verified = verification.get("verified")
    table.add_row(
        "Verification",
        Text(
            "PASS" if verified is True else "FAIL" if verified is False else "UNKNOWN",
            style="green" if verified is True else "red" if verified is False else "yellow",
        ),
    )
    court = data.get("court") or []
    if court:
        table.add_row("Court", " · ".join(str(item) for item in court))
    lessons = data.get("lessons") or []
    if lessons:
        table.add_row("Learned", "\n".join(f"- {item}" for item in lessons))
    children = data.get("children") or []
    if children:
        table.add_row(
            "Next missions",
            "\n".join(
                f"- {row.get('objective')} [{str(row.get('status') or '').upper()}]"
                for row in children if isinstance(row, dict)
            ),
        )
    compaction = data.get("journal_compaction") or {}
    if compaction:
        if compaction.get("compacted"):
            table.add_row(
                "Journal",
                f"archived · {compaction.get('original_entries', 0)} -> "
                f"{compaction.get('retained_entries', 0)} entries",
            )
        elif compaction.get("reason") == "corrupt_lines_present":
            table.add_row("Journal", Text("not compacted · corruption detected", style="red"))
    usage = data.get("usage") or {}
    if usage:
        tokens = int(usage.get("total_tokens", 0) or 0)
        cost = float(usage.get("cost_usd", 0.0) or 0.0)
        table.add_row("Usage", f"{tokens} tokens · ${cost:.6f}")
    table.add_row("Acknowledged", "yes" if report.acknowledged else "no")
    return Panel(table, title="LILITH · MISSION DEBRIEF", border_style="cyan")


def compact_inbox_line() -> Text | None:
    from .inbox import MissionInbox

    reports = MissionInbox().list(unread_only=True, limit=1)
    if not reports:
        return None
    report = reports[0]
    verification = report.debrief.get("verification") or {}
    verified = verification.get("verified")
    verify = "PASS" if verified is True else "FAIL" if verified is False else "UNVERIFIED"
    children = report.debrief.get("children") or []
    text = Text("LATEST REPORT ", style="bold yellow")
    text.append(str(report.status).upper(), style="green" if report.status == "completada" else "red")
    text.append(f" · {verify} · {report.objective}")
    if children:
        text.append(f" · {len(children)} next mission(s)", style="dim")
    return text

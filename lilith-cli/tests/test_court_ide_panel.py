from lilith_cli.ide.screens.modals import CourtPanelScreen, YggdrasilPanelScreen
from lilith_cli.ide.views.yggdrasil_panel import CourtPanelMixin, YggdrasilPanelMixin


def test_legacy_panel_symbols_are_compatibility_aliases():
    assert CourtPanelMixin is YggdrasilPanelMixin
    assert CourtPanelScreen is YggdrasilPanelScreen


def test_court_panel_has_no_legacy_bus_or_mutating_panel_actions():
    assert not hasattr(CourtPanelMixin, "_yggdrasil_bus_db_path")
    assert not hasattr(CourtPanelMixin, "_delegate_to_preset")
    assert not hasattr(CourtPanelMixin, "_yggdrasil_cancel_queue_message")
    assert not hasattr(CourtPanelMixin, "_yggdrasil_kill_spawn")
    actions = {binding.action for binding in CourtPanelScreen.BINDINGS}
    assert actions == {"dismiss_panel", "refresh"}


def test_court_agents_come_from_canonical_registry():
    host = CourtPanelMixin()
    rows = host._court_agents()
    ids = {row["agent_id"] for row in rows}
    assert "lilith" in ids
    assert {"demiurge", "pandora", "sebas", "cocytus", "shalltear", "aura", "mare"} <= ids
    assert not {"vor", "huginn", "muninn"} & ids


def test_court_mission_observation_uses_kernel_reader(monkeypatch):
    from lilith_cli import agent_console

    calls = []
    expected = {"available": True, "active": None, "recent": []}

    def observe(limit=5):
        calls.append(limit)
        return expected

    monkeypatch.setattr(agent_console, "mission_observation", observe)
    host = CourtPanelMixin()
    assert host._court_mission_observation(8) is expected
    assert calls == [8]

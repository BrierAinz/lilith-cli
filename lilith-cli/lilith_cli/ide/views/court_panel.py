"""Canonical IDE actions for Lilith Mission Control / Court dashboard."""

from __future__ import annotations


class CourtPanelMixin:
    """Open the single canonical Mission Control dashboard."""

    @staticmethod
    def _court_agents() -> list[dict[str, object]]:
        from ...mission.court import CourtRegistry

        return [agent.as_dict() for agent in CourtRegistry.list_agents(include_queen=True)]

    @staticmethod
    def _court_mission_observation(limit: int = 5) -> dict[str, object]:
        from ...agent_console import mission_observation

        return mission_observation(limit)

    def action_open_court_panel(self) -> None:
        self.action_open_mission_panel()  # type: ignore[attr-defined]

    def action_open_yggdrasil_panel(self) -> None:
        """Compatibility action retained for old keymaps/plugins."""
        self.action_open_mission_panel()  # type: ignore[attr-defined]

from lilith_cli.mission.court import CourtRegistry


def test_legacy_names_resolve_to_new_court_identity() -> None:
    assert CourtRegistry.get("vor").agent_id == "cocytus"
    assert CourtRegistry.get("muninn").agent_id == "sebas"
    assert CourtRegistry.get("batch-deepseek").agent_id == "mare"


def test_court_selection_uses_capabilities_not_provider_names() -> None:
    team = CourtRegistry.choose(
        {"architecture", "code", "security"},
        count=4,
        require_executor=True,
        require_reviewer=True,
    )
    ids = {agent.agent_id for agent in team}
    assert "demiurge" in ids
    assert "cocytus" in ids
    assert "shalltear" in ids
    assert all("provider" not in agent.role.lower() for agent in team)


def test_lilith_is_above_subagent_registry() -> None:
    assert CourtRegistry.QUEEN.agent_id == "lilith"
    assert CourtRegistry.QUEEN.can_admin
    assert "lilith" not in CourtRegistry.AGENTS

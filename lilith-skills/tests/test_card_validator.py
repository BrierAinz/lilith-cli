"""Tests for lilith_skills.card_validator.

End-to-end exercise against the real Vanaheim/Agents/agent_cards.yaml
(14 cards): every one must pass with ``allow_capabilities=True`` and
the dataclass must carry the expected per-card tool partition.

Also covers negative cases:
- A card with an invented tool name raises CardValidationError.
- A capability-only name is rejected when ``allow_capabilities=False``.
- The bind_loader integration: ``validate_tools`` / ``strict_tools``
  kwargs behave as documented in sandbox_binder.

The fixture ``real_loader`` depends on the shared ``vanaheim_yaml_path``
guard (see ``conftest.py``) so that a standalone Asgard checkout on CI
without the Yggdrasil hub parent yields SKIPs rather than
``FileNotFoundError`` failures.
"""

from __future__ import annotations

import logging

import pytest

from lilith_skills.agent_cards import AgentCard, AgentCardLoader
from lilith_skills.card_validator import (
    CardToolValidation,
    CardValidationError,
    assert_loader_tools_valid,
    validate_card_tools,
    validate_loader_tools,
)
from lilith_skills.sandbox_binder import bind_loader


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_loader(vanaheim_yaml_path) -> AgentCardLoader:
    """The real Vanaheim/Agents/agent_cards.yaml (14 cards).

    Skipped on standalone Asgard checkouts where the Vanaheim YAML
    isn't reachable from the test package — see conftest.py.
    """
    return AgentCardLoader.from_vanaheim(
        str(vanaheim_yaml_path.parents[2])
    )


def _card(
    *,
    name: str = "Tester",
    role: str = "Test",
    level: int = 2,
    tools: list[str] | None = None,
) -> AgentCard:
    return AgentCard(
        name=name,
        role=role,
        level=level,
        model="glm-5.2",
        tools=list(tools or []),
        description="test card",
    )


# ── Real loader: every Vanaheim card passes ────────────────────────────────


class TestRealVanaheimCardsPass:
    """All 14 shipped cards must pass validation with allow_capabilities=True."""

    def test_real_loader_loads_14_cards(self, real_loader: AgentCardLoader) -> None:
        assert len(real_loader.list_agents()) == 14

    def test_every_card_passes_with_default(self, real_loader: AgentCardLoader) -> None:
        results = validate_loader_tools(real_loader)
        assert len(results) == 14
        for r in results:
            assert r.ok, (
                f"card {r.agent_name!r} unexpectedly failed validation: "
                f"unknown={sorted(r.unknown_tools)}"
            )

    def test_assert_loader_tools_valid_does_not_raise(
        self, real_loader: AgentCardLoader
    ) -> None:
        # Should run without exception -- the convenient one-liner.
        assert_loader_tools_valid(real_loader, allow_capabilities=True)

    def test_card_names_match_expected_set(
        self, real_loader: AgentCardLoader
    ) -> None:
        names = {c.name for c in real_loader.list_agents()}
        assert names == {
            "Odin",
            "Mimir",
            "Adan",
            "Eva",
            "Shalltear",
            "Heimdall",
            "Freyja",
            "Loki",
            "Thor",
            "Skadi",
            "Hela",
            "Tyr",
            "Fenrir",
            "Jörmungandr",
        }

    @pytest.mark.parametrize(
        "agent_name, expected_concrete, expected_capability_only",
        [
            # Odin: web_search+read_file(alias→file_read)+search_files+sess_search
            # read_file is alias; search_files & session_search are capability-only.
            (
                "Odin",
                frozenset({"web_search", "file_read"}),
                frozenset({"search_files", "session_search"}),
            ),
            # Adan: terminal+read_file+write_file+patch+search_files
            # only read_file aliases to a real tool.
            (
                "Adan",
                frozenset({"file_read"}),
                frozenset({"terminal", "write_file", "patch", "search_files"}),
            ),
            # Mimir: web_search+read_file+search_files+write_file+session_search
            (
                "Mimir",
                frozenset({"web_search", "file_read"}),
                frozenset({"search_files", "write_file", "session_search"}),
            ),
        ],
    )
    def test_partition_for_named_card(
        self,
        real_loader: AgentCardLoader,
        agent_name: str,
        expected_concrete: frozenset[str],
        expected_capability_only: frozenset[str],
    ) -> None:
        card = real_loader.get_agent(agent_name)
        result = validate_card_tools(card)
        assert result.concrete_tools == expected_concrete, agent_name
        assert result.capability_only_tools == expected_capability_only, agent_name
        assert not result.unknown_tools
        assert result.ok


# ── Negative: invented / unknown names ─────────────────────────────────────


class TestNegativeCases:
    def test_invented_tool_raises(self) -> None:
        card = _card(name="Bad", tools=["read_file", "magic_pixie_dust"])
        with pytest.raises(CardValidationError) as ei:
            validate_card_tools(card, allow_capabilities=True)
        assert "magic_pixie_dust" in str(ei.value)

    def test_invented_tool_rejected_even_strict(self) -> None:
        card = _card(name="Bad", tools=["terminal", "magic_pixie_dust"])
        with pytest.raises(CardValidationError):
            validate_card_tools(card, allow_capabilities=False)

    def test_capability_only_rejected_when_allow_capabilities_false(self) -> None:
        # terminal is a real capability name but isn't a concrete tool.
        # When strict, it's moved from capability_only to unknown.
        card = _card(name="Stricter", tools=["terminal"])
        with pytest.raises(CardValidationError):
            validate_card_tools(card, allow_capabilities=False)

    def test_validate_card_tools_propagates_validation_on_exception(
        self,
    ) -> None:
        """The raised exception should mention the agent name."""
        card = _card(name="NamedBad", tools=["telepathy"])
        with pytest.raises(CardValidationError, match="NamedBad"):
            validate_card_tools(card)

    def test_empty_tools_passes(self) -> None:
        card = _card(name="Empty", tools=[])
        result = validate_card_tools(card)
        assert result.ok
        assert result.total == 0
        assert not result.concrete_tools
        assert not result.capability_only_tools

    def test_duplicate_tools_handled(self) -> None:
        card = _card(name="Dup", tools=["terminal", "terminal", "read_file"])
        result = validate_card_tools(card)
        assert result.ok
        assert result.capability_only_tools == frozenset({"terminal"})
        assert result.concrete_tools == frozenset({"file_read"})


# ── CardToolValidation dataclass ───────────────────────────────────────────


class TestCardToolValidationDataclass:
    def test_ok_property_true_when_no_unknown(self) -> None:
        cv = CardToolValidation(agent_name="X", unknown_tools=frozenset())
        assert cv.ok

    def test_ok_property_false_when_unknown(self) -> None:
        cv = CardToolValidation(agent_name="X", unknown_tools=frozenset({"oops"}))
        assert not cv.ok

    def test_total_property_counts_all(self) -> None:
        cv = CardToolValidation(
            agent_name="X",
            unknown_tools=frozenset({"u1"}),
            concrete_tools=frozenset({"c1", "c2"}),
            capability_only_tools=frozenset({"k1"}),
        )
        assert cv.total == 4


# ── validate_loader_tools ──────────────────────────────────────────────────


class TestValidateLoaderTools:
    def test_returns_one_per_card(self, real_loader: AgentCardLoader) -> None:
        results = validate_loader_tools(real_loader)
        assert len(results) == len(real_loader.list_agents())

    def test_loader_mix_strict_raises(self) -> None:
        """A loader mixing a clean card with a bad card raises under strict=True."""
        cards = [
            _card(name="Good", tools=["terminal", "read_file"]),
            _card(name="Bad", tools=["flying_carpet"]),
        ]

        class _L:
            def list_agents(self) -> list[AgentCard]:
                return cards

        with pytest.raises(CardValidationError, match="Bad"):
            validate_loader_tools(_L(), strict=True)  # type: ignore[arg-type]

    def test_loader_mix_non_strict_collects(self) -> None:
        """Non-strict mode collects all per-card results, even failing ones."""
        cards = [
            _card(name="Good", tools=["terminal", "read_file"]),
            _card(name="Bad", tools=["flying_carpet"]),
        ]

        class _L:
            def list_agents(self) -> list[AgentCard]:
                return cards

        results = validate_loader_tools(_L())  # type: ignore[arg-type]
        assert len(results) == 2
        assert results[0].ok
        assert not results[1].ok
        assert "flying_carpet" in results[1].unknown_tools


# ── assert_loader_tools_valid ───────────────────────────────────────────────


class TestAssertLoaderToolsValid:
    def test_clean_loader_silent(self) -> None:
        cards = [_card(name="G", tools=["read_file"])]

        class _L:
            def list_agents(self) -> list[AgentCard]:
                return cards

        assert_loader_tools_valid(_L())  # type: ignore[arg-type]  # no raise

    def test_bad_loader_raises(self) -> None:
        cards = [_card(name="G", tools=["read_file"]),
                 _card(name="B", tools=["flying_carpet"])]

        class _L:
            def list_agents(self) -> list[AgentCard]:
                return cards

        with pytest.raises(CardValidationError, match="B"):
            assert_loader_tools_valid(_L())  # type: ignore[arg-type]


# ── sandbox_binder integration ─────────────────────────────────────────────


class TestBindLoaderIntegration:
    """The new validate_tools / strict_tools kwargs behave per the spec."""

    def test_default_behavior_unchanged(self, real_loader: AgentCardLoader) -> None:
        # Same call signature as before this spec -- no validate kwarg.
        result = bind_loader(real_loader)
        assert len(result) == 14

    def test_validate_tools_soft_warns_but_passes(
        self, real_loader: AgentCardLoader, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The real loader is clean: no warnings should appear.
        with caplog.at_level(logging.WARNING, logger="lilith.skills.sandbox_binder"):
            result = bind_loader(real_loader, validate_tools=True)
        assert len(result) == 14
        assert not any("failed tool validation" in r.message for r in caplog.records)

    def test_strict_tools_passes_for_clean(
        self, real_loader: AgentCardLoader
    ) -> None:
        # Real loader is clean, strict should not raise.
        result = bind_loader(
            real_loader, validate_tools=True, strict_tools=True
        )
        assert len(result) == 14

    def test_strict_tools_raises_for_dirty(self) -> None:
        cards = [
            _card(name="Clean", tools=["terminal", "read_file"]),
            _card(name="Bad", tools=["flying_carpet"]),
        ]

        class _L:
            def list_agents(self) -> list[AgentCard]:
                return cards

        with pytest.raises(CardValidationError, match="Bad"):
            bind_loader(_L(), validate_tools=True, strict_tools=True)  # type: ignore[arg-type]

    def test_soft_continues_binding_despite_bad_cards(self) -> None:
        cards = [
            _card(name="Clean", tools=["terminal", "read_file"]),
            _card(name="Bad", tools=["flying_carpet"]),
        ]

        class _L:
            def list_agents(self) -> list[AgentCard]:
                return cards

        # Even with a dirty card, soft mode returns the bound entries
        # (with a warning emitted via logger).
        result = bind_loader(_L(), validate_tools=True)  # type: ignore[arg-type]
        assert len(result) == 2

    def test_allow_capabilities_false_in_bind_loader(self) -> None:
        # When allow_capabilities=False, capability-only names become
        # unknown and (under strict) trigger the error.
        cards = [_card(name="CapOnly", tools=["terminal"])]

        class _L:
            def list_agents(self) -> list[AgentCard]:
                return cards

        with pytest.raises(CardValidationError):
            bind_loader(  # type: ignore[arg-type]
                _L(),
                validate_tools=True,
                strict_tools=True,
                allow_capabilities=False,
            )

"""Tests for the v7 orchestration system prompt.

The v7 program teaches Lilith about its new orchestration arsenal
(`delegate_subagent` with agentic/structured/max_tokens,
`orchestration_state`, `skill_run` / `/skills`, `post_mortems`,
`/costs`, `/state`, `/subagents test`, `/mcp`, `file_append`, etc.).
These tests pin the default prompt that ships in ``config.py`` so the
clause list cannot silently regress.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from lilith_cli.agent import DECISION_SUPPORT_INSTRUCTIONS, AgentSession
from lilith_cli.config import _DEFAULT_CONFIG_YAML, YggdrasilConfig

# Tools / commands whose presence in the default prompt is required.
# The filename stays for compatibility, but these assertions include the
# additive v8 verified-orchestration contract.
_REQUIRED_KEYWORDS: tuple[str, ...] = (
    "mission_prepare", "success_criteria", "mission_court", "mission_compute",
    "mission_delegate", "mission_conclave", "mission_skill_run", "MCP",
    "mission_authority", "longrun", "leases", "heartbeat", "checkpoint",
    "post_mortems", "memory_evidence", "circuit breakers", "compute health",
    "aggregate budgets", "rollback", "file_write", "file_edit", "file_append",
    "mission_complete", "verify", "Campaign Mode", "Demiurge", "Sebas", "Cocytus",
)


class TestYggdrasilConfigDefaultSystemPrompt:
    """The Pydantic default system_prompt must teach the v7 arsenal."""

    def test_default_prompt_is_non_empty(self) -> None:
        assert YggdrasilConfig().system_prompt.strip()

    def test_default_prompt_preserves_orchestrator_identity(self) -> None:
        prompt = YggdrasilConfig().system_prompt
        # Anchor identity lines must survive.
        assert "Lilith, Queen / Prime Orchestrator" in prompt
        assert "Ainz / Overlord" in prompt
        assert "orchestrator of the Yggdrasil ecosystem" not in prompt

    @pytest.mark.parametrize("keyword", _REQUIRED_KEYWORDS)
    def test_default_prompt_mentions_v7_keyword(self, keyword: str) -> None:
        prompt = YggdrasilConfig().system_prompt
        assert keyword.lower() in prompt.lower(), (
            f"v7 default system prompt must mention '{keyword}' so the model "
            f"knows the arsenal exists. Got prompt:\n{prompt}"
        )

    def test_default_prompt_mentions_nine_clauses(self) -> None:
        """The verified arsenal is a numbered list 1..9 — pin the count."""
        prompt = YggdrasilConfig().system_prompt
        clause_markers = re.findall(r"(?:^|\n)\s*\d+\.\s", prompt)
        assert len(clause_markers) >= 11, (
            f"Queen/Mission contract should have at least 11 numbered clauses, "
            f"found {len(clause_markers)} in:\n{prompt}"
        )

    def test_default_prompt_mentions_safeguard(self) -> None:
        """Safeguard rule: 2 failures -> change strategy."""
        prompt = YggdrasilConfig().system_prompt.lower()
        assert "diagnose the cause" in prompt
        assert "change strategy" in prompt
        assert "never duplicate an effect whose outcome is unknown" in prompt


class TestDefaultConfigYamlSystemPrompt:
    """The bundled ``_DEFAULT_CONFIG_YAML`` template must also include the arsenal."""

    def test_default_yaml_parses_with_system_prompt(self) -> None:
        # The default YAML is a multi-document string starting with comments;
        # yaml.safe_load handles it as a single mapping thanks to the leading
        # '#' comment being ignored.
        import yaml

        parsed = yaml.safe_load(_DEFAULT_CONFIG_YAML)
        assert isinstance(parsed, dict)
        assert "system_prompt" in parsed
        prompt = parsed["system_prompt"]
        # system_prompt uses '>' folded style, so it can be a string.
        assert isinstance(prompt, str)
        assert "Lilith, Queen / Prime Orchestrator" in prompt
        assert parsed["provider"] == "fabric"
        assert parsed["model"] == "router"

    @pytest.mark.parametrize("keyword", _REQUIRED_KEYWORDS)
    def test_default_yaml_prompt_mentions_v7_keyword(self, keyword: str) -> None:
        import yaml

        prompt = yaml.safe_load(_DEFAULT_CONFIG_YAML)["system_prompt"]
        assert keyword.lower() in prompt.lower(), (
            f"v7 default YAML system_prompt must mention '{keyword}' "
            f"so a fresh `lilith` install picks up the arsenal."
        )


class TestUserConfigYamlSystemPrompt:
    """Custom personalities are user data, independent of bundled defaults."""

    def test_user_prompt_preserves_identity(self, tmp_path, monkeypatch) -> None:
        import yaml
        from lilith_cli.config import load_config

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        path = tmp_path / "config.yaml"
        personality = "Eres Lilith. Habla español y respeta mi estilo nórdico."
        path.write_text(yaml.safe_dump({"system_prompt": personality}), encoding="utf-8")
        assert load_config(path).system_prompt == personality


class TestDecisionSupportContract:
    """Decision guidance is runtime policy, independent of user personality."""

    def test_contract_defines_bounded_options_and_recommendation(self) -> None:
        prompt = DECISION_SUPPORT_INSTRUCTIONS.lower()
        assert "2 to 4" in prompt
        assert '"recommended"' in prompt
        assert "number or by option name" in prompt
        assert "otherwise make a\n  reasonable assumption" in prompt
        assert "not a substitute" in prompt

    def test_custom_personality_receives_decision_support_at_runtime(self) -> None:
        session = AgentSession.__new__(AgentSession)
        session.system_prompt = "Mi personalidad privada"
        session.history = []
        session.config = SimpleNamespace(
            history=SimpleNamespace(max_turns=5),
            memory=SimpleNamespace(enabled=False),
        )
        session._tools_enabled = False
        session._progress_enabled = False
        session.get_tool_descriptions = list
        session._load_project_instructions = lambda: ""

        messages = session._build_messages()

        assert messages[0]["content"].startswith("Mi personalidad privada")
        assert DECISION_SUPPORT_INSTRUCTIONS in messages[0]["content"]

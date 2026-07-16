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

import pytest

from lilith_cli.config import YggdrasilConfig, _DEFAULT_CONFIG_YAML


# Tools / commands whose presence in the default prompt is required.
# Keep this list aligned with the v7 arsenal list in
# ``Docs/LILITH_V7_ROADMAP.md`` (tandas 1-6).
_REQUIRED_KEYWORDS: tuple[str, ...] = (
    "orchestration_state",
    "add_task",
    "update_task",
    "delegate_subagent",
    "agentic=true",
    "structured=true",
    "max_tokens",
    "skill_run",
    "/skills",
    "post_mortems",
    "file_write",
    "file_append",
    "verify",  # "Verify every deliverable on disk"
)


class TestYggdrasilConfigDefaultSystemPrompt:
    """The Pydantic default system_prompt must teach the v7 arsenal."""

    def test_default_prompt_is_non_empty(self) -> None:
        assert YggdrasilConfig().system_prompt.strip()

    def test_default_prompt_preserves_orchestrator_identity(self) -> None:
        prompt = YggdrasilConfig().system_prompt
        # Anchor identity lines must survive.
        assert "You are Lilith, the orchestrator of the Yggdrasil ecosystem" in prompt
        assert "Where Ancient Meets Digital" in prompt

    @pytest.mark.parametrize("keyword", _REQUIRED_KEYWORDS)
    def test_default_prompt_mentions_v7_keyword(self, keyword: str) -> None:
        prompt = YggdrasilConfig().system_prompt
        assert keyword.lower() in prompt.lower(), (
            f"v7 default system prompt must mention '{keyword}' so the model "
            f"knows the arsenal exists. Got prompt:\n{prompt}"
        )

    def test_default_prompt_mentions_eight_clauses(self) -> None:
        """The arsenal is a numbered list 1..8 — pin the count."""
        prompt = YggdrasilConfig().system_prompt
        clause_markers = re.findall(r"(?:^|\n)\s*\d+\.\s", prompt)
        assert len(clause_markers) >= 8, (
            f"v7 arsenal list should have at least 8 numbered clauses, "
            f"found {len(clause_markers)} in:\n{prompt}"
        )

    def test_default_prompt_mentions_safeguard(self) -> None:
        """Safeguard rule: 2 failures -> change strategy."""
        prompt = YggdrasilConfig().system_prompt.lower()
        assert "fails twice" in prompt or "fail twice" in prompt
        assert "change" in prompt


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
        assert "Where Ancient Meets Digital" in prompt

    @pytest.mark.parametrize("keyword", _REQUIRED_KEYWORDS)
    def test_default_yaml_prompt_mentions_v7_keyword(self, keyword: str) -> None:
        import yaml

        prompt = yaml.safe_load(_DEFAULT_CONFIG_YAML)["system_prompt"]
        assert keyword.lower() in prompt.lower(), (
            f"v7 default YAML system_prompt must mention '{keyword}' "
            f"so a fresh `lilith` install picks up the arsenal."
        )


class TestUserConfigYamlSystemPrompt:
    """Sanity-check the live user config at ~/.yggdrasil/config.yaml.

    Skipped automatically if the file is absent — the test is here so
    operators see a clear message when their personal prompt is stale.
    """

    USER_CONFIG_PATH = Path.home() / ".yggdrasil" / "config.yaml"

    def test_user_prompt_preserves_identity(self) -> None:
        if not self.USER_CONFIG_PATH.exists():
            pytest.skip("~/.yggdrasil/config.yaml not present")
        import yaml

        parsed = yaml.safe_load(self.USER_CONFIG_PATH.read_text(encoding="utf-8"))
        prompt = parsed.get("system_prompt", "")
        assert "You are Lilith" in prompt
        assert "Where Ancient Meets Digital" in prompt
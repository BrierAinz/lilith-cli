"""Tests for the Lilith IDE Runestone / artifact system."""

from __future__ import annotations

from pathlib import Path

import pytest

from lilith_cli.ide.runestones import Runestone, RunestoneForge, extract_runestones


class TestExtractRunestones:
    """Unit tests for extracting code fences as Runestones."""

    def test_single_python_block(self):
        text = "Aquí tenés el código:\n\n```python\ndef hello():\n    pass\n```\n\nSaludos."
        stones = extract_runestones(text)
        assert len(stones) == 1
        assert stones[0].language == "python"
        assert "def hello():" in stones[0].content

    def test_multiple_blocks(self):
        text = "```json\n{\"a\": 1}\n```\n\n```python\nx = 1\n```"
        stones = extract_runestones(text)
        assert len(stones) == 2
        assert stones[0].language == "json"
        assert stones[1].language == "python"

    def test_no_blocks(self):
        assert extract_runestones("solo texto") == []

    def test_block_without_language(self):
        text = "```\nplain text\n```"
        stones = extract_runestones(text)
        assert len(stones) == 1
        assert stones[0].language == "text"


class TestRunestoneForge:
    """Unit tests for the RunestoneForge session tracker."""

    def test_forge_stores_stones(self):
        forge = RunestoneForge()
        text = "```python\ndef foo(): pass\n```"
        stones = forge.forge(text)
        assert len(stones) == 1
        assert len(forge.list()) == 1
        assert forge.get(stones[0].id) is not None

    def test_forge_returns_empty_for_plain_text(self):
        forge = RunestoneForge()
        assert forge.forge("sin bloques") == []
        assert forge.list() == []

    def test_clear(self):
        forge = RunestoneForge()
        forge.forge("```python\nx=1\n```")
        forge.clear()
        assert forge.list() == []

    def test_apply_writes_file(self, tmp_path):
        forge = RunestoneForge()
        stone = Runestone.from_code_block("python", "print(1)", title="test.py")
        forge._runestones = {stone.id: stone}
        target = tmp_path / "out.py"
        forge.apply(stone.id, target)
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "print(1)"

    def test_apply_missing_raises(self):
        forge = RunestoneForge()
        with pytest.raises(FileNotFoundError):
            forge.apply("noexiste", Path("x.py"))

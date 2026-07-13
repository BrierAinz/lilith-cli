"""Pydantic models for skill data."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

from pydantic import BaseModel, Field


class Skill(BaseModel):
    """A single skill entry parsed from SKILL.md frontmatter."""

    name: str = Field(..., description="Skill name (directory name)")
    category: str = Field(..., description="Category (parent directory)")
    description: str = Field(default="", description="Short description")
    trigger: str = Field(default="", description="When to load this skill")
    tags: list[str] = Field(default_factory=list, description="Searchable tags")
    version: str = Field(default="1.0.0", description="Skill version")
    path: Path = Field(..., description="Absolute path to skill directory")
    content: str = Field(default="", description="Full SKILL.md content")
    file_count: int = Field(default=1, description="Number of supporting files")
    linked_files: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Linked files: references, scripts, templates",
    )

    model_config = {"arbitrary_types_allowed": True}

    @property
    def skill_md_path(self) -> Path:
        """Path to the main SKILL.md file."""
        return self.path / "SKILL.md"

    @property
    def qualified_name(self) -> str:
        """Category/skill-name format."""
        return f"{self.category}/{self.name}"

    def to_prompt(self, max_chars: int = 8000) -> str:
        """Format skill as an LLM prompt injection.

        Args:
            max_chars: Maximum characters to include from content.
                       0 = frontmatter only, -1 = everything.

        Returns:
            Formatted string suitable for LLM context injection.

        """
        parts = [f"# Skill: {self.qualified_name}"]
        if self.description:
            parts.append(f"Description: {self.description}")
        if self.trigger:
            parts.append(f"Trigger: {self.trigger}")
        if self.tags:
            parts.append(f"Tags: {', '.join(self.tags)}")
        parts.append("")

        if self.content:
            if max_chars == -1:
                parts.append(self.content)
            elif max_chars > 0:
                # Skip frontmatter, get body content
                body = self._extract_body()
                if len(body) > max_chars:
                    body = body[: max_chars - 3] + "..."
                parts.append(body)

        return "\n".join(parts)

    def _extract_body(self) -> str:
        """Extract markdown body (after frontmatter)."""
        content = self.content
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                return content[end + 3 :].strip()
        return content


class SkillManifest(BaseModel):
    """Machine-readable manifest of all available skills."""

    schema_version: str = Field(default="yggdrasil-skills-v1", alias="schema")
    source: str = Field(default="hermes-agent", description="Export source")
    categories: dict[str, list[Skill]] = Field(
        default_factory=dict,
        description="Skills organized by category",
    )
    total_skills: int = Field(default=0)
    total_files: int = Field(default=0)

    model_config = {"populate_by_name": True}

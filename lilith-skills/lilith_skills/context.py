"""CLI integration for loading skills into the Lilith agent context.

Provides the `SkillContext` class that formats relevant skills for injection
into LLM prompts, enabling the Lilith CLI to dynamically load skill knowledge
from the Yggdrasil knowledge base.
"""

from __future__ import annotations

from pathlib import Path

from lilith_skills.registry import SkillRegistry


# Default Yggdrasil monorepo location
_YGGDRASIL_ROOT = Path("/mnt/d/Proyectos/Yggdrasil")
_SKILLS_REL = Path("Svartalfheim/Docs/skills")


class SkillContext:
    """Format skills for LLM prompt injection.

    Given a user query or task description, selects the most relevant
    skills and formats them for inclusion in a system prompt or context window.

    Usage::

        ctx = SkillContext()

        # Auto-select relevant skills based on a query
        prompt_text = ctx.build_context("Train a LoRA for my character on PixAI")

        # Or load specific skills
        prompt_text = ctx.load_skills(["mlops/lora-training-pipeline", "creative/comfyui"])

        # Or get a specific skill
        skill = ctx.get_skill("software-development/yggdrasil-ecosystem")
    """

    def __init__(
        self,
        repo_root: Path | str | None = None,
        max_skills: int = 5,
        max_chars_per_skill: int = 4000,
    ) -> None:
        """Initialize the skill context.

        Args:
            repo_root: Path to Yggdrasil monorepo root. Defaults to
                       /mnt/d/Proyectos/Yggdrasil.
            max_skills: Maximum number of skills to include in context.
            max_chars_per_skill: Maximum characters per skill in context.

        """
        root = Path(repo_root) if repo_root else _YGGDRASIL_ROOT
        skills_dir = root / _SKILLS_REL

        if not skills_dir.is_dir():
            raise FileNotFoundError(
                f"Skills directory not found at {skills_dir}. "
                f"Set repo_root to the Yggdrasil monorepo root.",
            )

        self.registry = SkillRegistry(skills_dir)
        self.max_skills = max_skills
        self.max_chars = max_chars_per_skill

    def build_context(self, query: str, max_total_chars: int = 16000) -> str:
        """Build a context block with relevant skills for a query.

        Args:
            query: User query or task description.
            max_total_chars: Maximum total characters for all skills combined.

        Returns:
            Formatted string with skill descriptions and content.

        """
        results = self.registry.search(query, limit=self.max_skills)
        if not results:
            return ""

        parts = [
            "# Relevant Skills\n",
            "The following skills from the Yggdrasil knowledge base may help:\n",
        ]

        total_chars = 0
        for skill in results:
            skill_prompt = skill.to_prompt(max_chars=self.max_chars)
            if total_chars + len(skill_prompt) > max_total_chars:
                # Truncate if over budget
                remaining = max_total_chars - total_chars
                if remaining > 200:
                    parts.append(skill_prompt[:remaining] + "\n\n[...truncated]")
                break
            parts.append(skill_prompt)
            parts.append("")  # blank line separator
            total_chars += len(skill_prompt)

        return "\n".join(parts)

    def load_skills(self, skill_names: list[str]) -> str:
        """Load specific skills by name into a context block.

        Args:
            skill_names: List of qualified names (e.g., ["mlops/lora-training-pipeline"])

        Returns:
            Formatted string with full skill content.

        """
        parts = ["# Loaded Skills\n"]

        for name in skill_names:
            skill = self.registry.get(name)
            if skill:
                parts.append(skill.to_prompt(max_chars=self.max_chars))
                parts.append("")
            else:
                parts.append(f"## Skill not found: {name}\n")

        return "\n".join(parts)

    def get_skill(self, qualified_name: str) -> object | None:
        """Get a single skill by name.

        Args:
            qualified_name: e.g., "mlops/lora-training-pipeline"

        Returns:
            Skill object or None.

        """
        return self.registry.get(qualified_name)

    def list_available(self) -> str:
        """Return a formatted list of all available skills.

        Returns:
            Human-readable list of all skills grouped by category.

        """
        stats = self.registry.stats()
        lines = [
            f"# Yggdrasil Knowledge Base ({stats['total_skills']} skills)\n",
        ]

        for category in sorted(stats["categories"].keys()):
            skills = self.registry.by_category(category)
            lines.append(f"\n## {category} ({len(skills)})")
            for skill in skills:
                desc = skill.description[:60] if skill.description else "No description"
                lines.append(f"  - {skill.name}: {desc}")

        return "\n".join(lines)

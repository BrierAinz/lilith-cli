"""Skill Registry — search, filter, and retrieve skills from the knowledge base.

Provides the main interface for looking up skills by name, category, tags,
or content search. Integrates with the Yggdrasil monorepo structure.
"""

from __future__ import annotations

import json
from pathlib import Path

from lilith_skills.loader import SkillLoader
from lilith_skills.models import Skill, SkillManifest


# Default path within Yggdrasil monorepo
_DEFAULT_SKILLS_REL = "Svartalfheim/Docs/skills"


class SkillRegistry:
    """Registry for searching and retrieving Yggdrasil skills.

    Usage::

        # From repo root (auto-discovers skills directory)
        registry = SkillRegistry.from_repo("/mnt/d/Proyectos/Yggdrasil")

        # Or directly from skills directory
        registry = SkillRegistry("/mnt/d/Proyectos/Yggdrasil/Svartalfheim/Docs/skills")

        # List all skills
        for skill in registry.list_skills():
            print(skill.qualified_name)

        # Search by keyword
        results = registry.search("comfyui")

        # Get by qualified name
        skill = registry.get("mlops/lora-training-pipeline")
        print(skill.to_prompt())

        # Filter by category
        creative = registry.by_category("creative")

        # Filter by tag
        tagged = registry.by_tag("blender")
    """

    def __init__(self, skills_root: Path | str) -> None:
        """Initialize the registry by scanning the skills directory.

        Args:
            skills_root: Path to the skills directory
                (e.g., .../Svartalfheim/Docs/skills/)

        """
        self.root = Path(skills_root).resolve()
        self.loader = SkillLoader(self.root)
        self._skills: list[Skill] = []
        self._by_name: dict[str, Skill] = {}
        self._by_qualified: dict[str, Skill] = {}
        self._by_category: dict[str, list[Skill]] = {}

        self._load()

    @classmethod
    def from_repo(cls, repo_root: Path | str) -> SkillRegistry:
        """Create a registry from a Yggdrasil monorepo root.

        Automatically locates the skills directory at
        <repo_root>/Svartalfheim/Docs/skills/

        Args:
            repo_root: Path to the Yggdrasil monorepo root.

        Returns:
            Initialized SkillRegistry.

        Raises:
            FileNotFoundError: If skills directory doesn't exist.

        """
        repo = Path(repo_root).resolve()
        skills_dir = repo / _DEFAULT_SKILLS_REL

        if not skills_dir.is_dir():
            raise FileNotFoundError(
                f"Skills directory not found at {skills_dir}. "
                f"Ensure the Yggdrasil repo is at {repo}.",
            )

        return cls(skills_dir)

    def _load(self) -> None:
        """Scan and index all skills."""
        self._skills = self.loader.scan()
        self._by_name = {}
        self._by_qualified = {}
        self._by_category = {}

        for skill in self._skills:
            # Index by name
            self._by_name[skill.name] = skill
            # Index by qualified name (category/name)
            self._by_qualified[skill.qualified_name] = skill
            # Index by category
            if skill.category not in self._by_category:
                self._by_category[skill.category] = []
            self._by_category[skill.category].append(skill)

    def list_skills(self) -> list[Skill]:
        """Return all loaded skills."""
        return list(self._skills)

    def get(self, qualified_name: str) -> Skill | None:
        """Get a skill by its qualified name (category/skill-name).

        Also accepts just the skill name if unique.

        Args:
            qualified_name: e.g., "mlops/lora-training-pipeline"
                           or just "lora-training-pipeline"

        Returns:
            The Skill if found, None otherwise.

        """
        # Try qualified name first
        if qualified_name in self._by_qualified:
            return self._by_qualified[qualified_name]

        # Try plain name
        if qualified_name in self._by_name:
            return self._by_name[qualified_name]

        return None

    def by_category(self, category: str) -> list[Skill]:
        """Get all skills in a category.

        Args:
            category: Category name (e.g., "creative", "mlops")

        Returns:
            List of skills in that category.

        """
        return self._by_category.get(category, [])

    def by_tag(self, tag: str) -> list[Skill]:
        """Get all skills that have a specific tag.

        Args:
            tag: Tag to search for (case-insensitive)

        Returns:
            List of skills matching the tag.

        """
        tag_lower = tag.lower()
        return [s for s in self._skills if tag_lower in [t.lower() for t in s.tags]]

    def search(self, query: str, limit: int = 10) -> list[Skill]:
        """Search skills by name, description, tags, or trigger.

        Args:
            query: Search string (case-insensitive substring match).
            limit: Maximum results to return.

        Returns:
            Ranked list of matching skills.

        """
        query_lower = query.lower()
        scored: list[tuple[int, Skill]] = []

        for skill in self._skills:
            score = 0

            # Name match (highest priority)
            if query_lower in skill.name.lower():
                score += 10
            # Exact name match
            if skill.name.lower() == query_lower:
                score += 20

            # Tag match
            for tag in skill.tags:
                if query_lower in tag.lower():
                    score += 5

            # Description match
            if query_lower in skill.description.lower():
                score += 3

            # Trigger match
            if query_lower in skill.trigger.lower():
                score += 2

            if score > 0:
                scored.append((score, skill))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        return [skill for _, skill in scored[:limit]]

    def categories(self) -> list[str]:
        """Return sorted list of all categories."""
        return sorted(self._by_category.keys())

    def category_counts(self) -> dict[str, int]:
        """Return number of skills per category."""
        return {cat: len(skills) for cat, skills in self._by_category.items()}

    def stats(self) -> dict:
        """Return registry statistics."""
        return {
            "total_skills": len(self._skills),
            "total_categories": len(self._by_category),
            "categories": self.category_counts(),
        }

    def to_manifest(self) -> SkillManifest:
        """Export all skills as a SkillManifest."""
        return SkillManifest(
            source="yggdrasil-knowledge-base",
            categories=self._by_category,
            total_skills=len(self._skills),
            total_files=sum(s.file_count for s in self._skills),
        )

    def to_json(self, indent: int = 2) -> str:
        """Export manifest as JSON string."""
        manifest = self.to_manifest()
        return json.dumps(
            manifest.model_dump(mode="json"),
            indent=indent,
            ensure_ascii=False,
        )

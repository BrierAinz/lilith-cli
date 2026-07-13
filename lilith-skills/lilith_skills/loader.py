"""Skill loader — parses SKILL.md files from the filesystem.

Reads the Yggdrasil skills knowledge base (Svartalfheim/Docs/skills/)
and parses YAML frontmatter from each SKILL.md into structured Skill objects.
"""

from __future__ import annotations

from pathlib import Path

from lilith_skills.models import Skill


class SkillLoader:
    """Load and parse skills from the filesystem.

    The skills directory follows this structure::

        skills/
        ├── creative/
        │   ├── comfyui/
        │   │   ├── SKILL.md
        │   │   └── references/
        │   └── blender-mcp/
        │       ├── SKILL.md
        │       └── scripts/
        └── mlops/
            └── lora-training-pipeline/
                ├── SKILL.md
                ├── references/
                ├── scripts/
                └── templates/

    Each SKILL.md has YAML frontmatter::

        ---
        name: skill-name
        description: Short description
        trigger: When to load this skill
        tags: [tag1, tag2]
        version: 1.0.0
        ---

        # Skill Content
        ...
    """

    def __init__(self, skills_root: Path | str) -> None:
        """Initialize with the root directory containing skill categories.

        Args:
            skills_root: Path to the skills directory
                (e.g., /mnt/d/Proyectos/Yggdrasil/Svartalfheim/Docs/skills/)

        """
        self.root = Path(skills_root).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"Skills directory not found: {self.root}")

    def scan(self) -> list[Skill]:
        """Scan all categories and parse all SKILL.md files.

        Returns:
            List of Skill objects, one per valid SKILL.md found.

        """
        skills: list[Skill] = []

        # Find subdirectories that look like category dirs
        # They contain skill subdirectories OR are flat skill dirs
        if not self.root.is_dir():
            return skills

        for category_dir in sorted(self.root.iterdir()):
            if not category_dir.is_dir():
                continue
            if category_dir.name.startswith((".", "_")):
                continue
            if category_dir.name in ("MANIFEST.json",):
                continue

            category_name = category_dir.name

            # Check if this is a flat skill (has SKILL.md directly) or a category
            direct_skill_md = category_dir / "SKILL.md"
            if direct_skill_md.exists():
                # This is a single skill, not a category
                skill = self._parse_skill_md(direct_skill_md, category_name)
                if skill:
                    # Override: parent of this dir is the category
                    # Actually, this IS the category for uncategorized skills
                    # For now, treat category_name as both category and name
                    skill = Skill(
                        name=category_name,
                        category=category_name,
                        description=skill.description,
                        trigger=skill.trigger,
                        tags=skill.tags,
                        version=skill.version,
                        path=skill.path,
                        content=skill.content,
                        file_count=self._count_files(category_dir),
                        linked_files=self._list_linked_files(category_dir),
                    )
                    skills.append(skill)
                continue

            # Category directory with multiple skills
            for skill_dir in sorted(category_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                if skill_dir.name.startswith((".", "_")):
                    continue

                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue

                skill = self._parse_skill_md(skill_md, category_name)
                if skill:
                    full_skill = Skill(
                        name=skill_dir.name,
                        category=category_name,
                        description=skill.description,
                        trigger=skill.trigger,
                        tags=skill.tags,
                        version=skill.version,
                        path=skill_dir,
                        content=skill.content,
                        file_count=self._count_files(skill_dir),
                        linked_files=self._list_linked_files(skill_dir),
                    )
                    skills.append(full_skill)

        return skills

    def _parse_skill_md(self, path: Path, category: str) -> Skill | None:
        """Parse a SKILL.md file with YAML frontmatter.

        Args:
            path: Path to SKILL.md
            category: Category name (parent directory)

        Returns:
            Parsed Skill object, or None if parsing fails.

        """
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        frontmatter = self._parse_frontmatter(content)
        if not frontmatter:
            # No frontmatter, create basic skill from content
            return Skill(
                name=path.parent.name,
                category=category,
                path=path.parent,
                content=content,
            )

        # Extract tags - handle both list and comma-separated string
        tags = frontmatter.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        elif isinstance(tags, list):
            tags = [str(t) for t in tags]

        return Skill(
            name=str(frontmatter.get("name", path.parent.name)),
            category=category,
            description=str(frontmatter.get("description", "")),
            trigger=str(frontmatter.get("trigger", "")),
            tags=tags,
            version=str(frontmatter.get("version", "1.0.0")),
            path=path.parent,
            content=content,
        )

    @staticmethod
    def _parse_frontmatter(content: str) -> dict[str, str | list[str]] | None:
        """Parse YAML frontmatter from a skill markdown file.

        Args:
            content: Full markdown content with optional --- frontmatter.

        Returns:
            Dict of frontmatter fields, or None if no frontmatter found.

        """
        if not content.startswith("---"):
            return None

        end = content.find("---", 3)
        if end == -1:
            return None

        yaml_text = content[3:end].strip()
        result: dict[str, str | list[str]] = {}

        for raw_line in yaml_text.split("\n"):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            # Handle key: value
            if ":" in line:
                key, _, raw_value = line.partition(":")
                key = key.strip()
                value: str | list[str] = raw_value.strip()

                # Remove quotes
                if isinstance(value, str) and (
                    (value.startswith('"') and value.endswith('"'))
                    or (value.startswith("'") and value.endswith("'"))
                ):
                    value = value[1:-1]

                # Handle > folded block (just take first line)
                if isinstance(value, str) and value.startswith(">"):
                    value = value[1:].strip()

                # Handle list values [tag1, tag2]
                if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
                    value = [v.strip().strip("'\"") for v in value[1:-1].split(",")]
                # Handle comma-separated values (YAML bare list)
                elif isinstance(value, str) and "," in value and key in ("tags", "categories"):
                    value = [v.strip().strip("'\"") for v in value.split(",")]

                result[key] = value

        return result or None

    @staticmethod
    def _count_files(directory: Path) -> int:
        """Count all files in a directory recursively."""
        return sum(1 for _ in directory.rglob("*") if _.is_file())

    @staticmethod
    def _list_linked_files(directory: Path) -> dict[str, list[str]]:
        """List supported file types organized by subdirectory type."""
        linked: dict[str, list[str]] = {}
        for subdir_type in ("references", "scripts", "templates", "assets"):
            subdir = directory / subdir_type
            if subdir.is_dir():
                files = [str(f.relative_to(directory)) for f in subdir.rglob("*") if f.is_file()]
                if files:
                    linked[subdir_type] = files
        return linked

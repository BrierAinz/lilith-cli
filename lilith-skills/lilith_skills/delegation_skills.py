"""Reusable delegation templates persisted as one YAML document per skill.

These executable templates are intentionally separate from knowledge-base
``SKILL.md`` files: the existing registry models declarative prompt knowledge,
while this registry has a strict runtime schema and user-level lifecycle under
``~/.yggdrasil/skills``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class DelegationSkill:
    name: str
    description: str
    preset: str
    prompt_template: str
    agentic: bool = False
    structured: bool = False
    max_tokens: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DelegationSkill":
        skill = cls(
            name=str(data.get("name", "")).strip(),
            description=str(data.get("description", "")).strip(),
            preset=str(data.get("preset", "")).strip(),
            prompt_template=str(data.get("prompt_template", "")).strip(),
            agentic=bool(data.get("agentic", False)),
            structured=bool(data.get("structured", False)),
            max_tokens=(int(data["max_tokens"]) if data.get("max_tokens") is not None else None),
        )
        skill.validate()
        return skill

    def validate(self) -> None:
        if not _NAME_RE.fullmatch(self.name):
            raise ValueError("nombre de skill inválido")
        if not self.preset:
            raise ValueError("preset es requerido")
        if not self.prompt_template:
            raise ValueError("prompt_template es requerido")
        for placeholder in ("{TASK}", "{PROJECT}", "{CONTEXT}"):
            if placeholder not in self.prompt_template:
                raise ValueError(f"prompt_template debe incluir {placeholder}")
        if self.max_tokens is not None and self.max_tokens < 1:
            raise ValueError("max_tokens debe ser >= 1")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    def render(self, task: str, project: str = "", context: str = "") -> str:
        return (
            self.prompt_template.replace("{TASK}", str(task))
            .replace("{PROJECT}", str(project))
            .replace("{CONTEXT}", str(context))
        )


DEFAULT_SKILLS = (
    DelegationSkill(
        name="recon-repo",
        description="Reconocimiento estructurado de un repositorio",
        preset="investigador-minimax",
        prompt_template="Analiza {TASK}\nProyecto: {PROJECT}\nContexto: {CONTEXT}",
        structured=True,
    ),
    DelegationSkill(
        name="batch-docs",
        description="Procesamiento por lotes de documentación",
        preset="batch-deepseek",
        prompt_template="Procesa en lote: {TASK}\nProyecto: {PROJECT}\nContexto: {CONTEXT}",
    ),
    DelegationSkill(
        name="implementar-feature",
        description="Implementación agéntica de una feature",
        preset="ejecutor-kimi",
        prompt_template="Implementa {TASK}\nProyecto: {PROJECT}\nContexto: {CONTEXT}",
        agentic=True,
    ),
)


def default_skills_path() -> Path:
    override = os.environ.get("YGGDRASIL_DELEGATION_SKILLS")
    return Path(override).expanduser() if override else Path.home() / ".yggdrasil" / "skills"


class DelegationSkillRegistry:
    def __init__(self, root: str | Path | None = None, *, seed_defaults: bool = True) -> None:
        self.root = Path(root).expanduser() if root else default_skills_path()
        self.root.mkdir(parents=True, exist_ok=True)
        if seed_defaults:
            for skill in DEFAULT_SKILLS:
                if not self._path(skill.name).exists():
                    self.save(skill)

    def _path(self, name: str) -> Path:
        if not _NAME_RE.fullmatch(str(name)):
            raise ValueError("nombre de skill inválido")
        return self.root / f"{name}.yaml"

    @staticmethod
    def _load_text(text: str) -> dict[str, Any]:
        data = yaml.safe_load(text) if yaml is not None else json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("skill YAML inválida")
        return data

    @staticmethod
    def _dump(data: dict[str, Any]) -> str:
        if yaml is not None:
            return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        return json.dumps(data, ensure_ascii=False, indent=2)

    def list(self) -> list[DelegationSkill]:
        skills = []
        for path in sorted(self.root.glob("*.yaml")):
            try:
                skills.append(DelegationSkill.from_dict(self._load_text(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return sorted(skills, key=lambda item: item.name)

    def names(self) -> list[str]:
        return [skill.name for skill in self.list()]

    def get(self, name: str) -> DelegationSkill | None:
        path = self._path(name)
        if not path.exists():
            return None
        return DelegationSkill.from_dict(self._load_text(path.read_text(encoding="utf-8")))

    def save(self, skill: DelegationSkill) -> Path:
        skill.validate()
        path = self._path(skill.name)
        temp = path.with_suffix(".yaml.tmp")
        temp.write_text(self._dump(skill.to_dict()), encoding="utf-8")
        os.replace(temp, path)
        return path

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if not path.exists():
            return False
        path.unlink()
        return True

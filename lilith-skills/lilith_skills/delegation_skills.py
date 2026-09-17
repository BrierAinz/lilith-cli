"""Reusable delegation templates persisted as one YAML document per skill.

These executable templates are intentionally separate from knowledge-base
``SKILL.md`` files: the existing registry models declarative prompt knowledge,
while this registry has a strict runtime schema and user-level lifecycle under
``~/.yggdrasil/skills``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
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
    def from_dict(cls, data: dict[str, Any]) -> DelegationSkill:
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


@dataclass(frozen=True)
class SkillVersionRecord:
    name: str
    version_id: str
    created_at: str
    source: str
    sha256: str
    path: str
    active: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_SKILLS = (
    DelegationSkill(
        name="recon-repo",
        description="Reconocimiento estructurado de un repositorio",
        # Era "investigador-minimax", un preset que NO existe en
        # ~/.yggdrasil/hlidskjalf_subagents.yaml: la skill fallaba al delegar por
        # preset inexistente. grok-research es el que encaja (contexto grande para
        # leer un repo entero) y es el que exige test_registry_seeds_three_real_preset_skills.
        preset="grok-research",
        prompt_template="Analiza {TASK}\nProyecto: {PROJECT}\nContexto: {CONTEXT}",
        structured=True,
    ),
    DelegationSkill(
        name="batch-docs",
        description="Procesamiento por lotes de documentación",
        # Mismo caso: apuntaba a un preset inexistente. El de lote es este.
        preset="batch-deepseek",
        prompt_template="Procesa en lote: {TASK}\nProyecto: {PROJECT}\nContexto: {CONTEXT}",
    ),
    DelegationSkill(
        name="implementar-feature",
        description="Implementación agéntica de una feature",
        preset="batch-deepseek",
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
            raise TypeError("skill YAML inválida")
        return data

    @staticmethod
    def _dump(data: dict[str, Any]) -> str:
        if yaml is not None:
            return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _versions_dir(self, name: str, *, create: bool = True) -> Path:
        self._path(name)
        path = self.root / ".versions" / name
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _version_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _active_hash(self, name: str) -> str | None:
        path = self._path(name)
        if not path.exists():
            return None
        return self._version_hash(path.read_text(encoding="utf-8"))

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

    def save_versioned(self, skill: DelegationSkill, *, source: str = "manual") -> SkillVersionRecord:
        skill.validate()
        text = self._dump(skill.to_dict())
        digest = self._version_hash(text)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        version_id = f"{stamp}-{digest[:12]}"
        versions = self._versions_dir(skill.name)
        version_path = versions / f"{version_id}.yaml"
        meta_path = versions / f"{version_id}.json"
        version_path.write_text(text, encoding="utf-8")
        record = {
            "name": skill.name, "version_id": version_id,
            "created_at": datetime.now(UTC).isoformat(), "source": str(source),
            "sha256": digest, "path": str(version_path),
        }
        meta_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        self.save(skill)
        return SkillVersionRecord(**record, active=True)

    def versions(self, name: str) -> list[SkillVersionRecord]:
        active_hash = self._active_hash(name)
        root = self._versions_dir(name, create=False)
        if not root.is_dir():
            return []
        rows: list[SkillVersionRecord] = []
        active_assigned = False
        for meta in sorted(root.glob("*.json"), reverse=True):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                is_active = bool(
                    active_hash and not active_assigned and data.get("sha256") == active_hash
                )
                active_assigned = active_assigned or is_active
                rows.append(SkillVersionRecord(**data, active=is_active))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return rows

    def get_version(self, name: str, version_id: str) -> DelegationSkill:
        version_path = self.root / ".versions" / name / f"{version_id}.yaml"
        if not version_path.is_file():
            raise ValueError(f"versión de skill no encontrada: {version_id}")
        skill = DelegationSkill.from_dict(
            self._load_text(version_path.read_text(encoding="utf-8"))
        )
        if skill.name != name:
            raise ValueError("la versión no pertenece a la skill solicitada")
        return skill

    def rollback(
        self, name: str, version_id: str, *, source: str | None = None
    ) -> SkillVersionRecord:
        skill = self.get_version(name, version_id)
        return self.save_versioned(
            skill, source=source or f"rollback:{version_id}"
        )

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if not path.exists():
            return False
        path.unlink()
        return True

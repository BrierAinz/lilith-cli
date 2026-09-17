"""Read-only, catalog-bound access to bundled guidance; never executes skills."""
from __future__ import annotations

import hashlib
import re
from typing import Any, ClassVar

from lilith_tools.base import BaseTool, ToolResult
from lilith_tools.registry import ToolRegistry


def _metadata(skill: Any) -> dict[str, Any]:
    return {
        'name': skill.name,
        'description': skill.description,
        'version': skill.version,
        'sha256': hashlib.sha256(skill.content.encode('utf-8')).hexdigest(),
    }


@ToolRegistry.register
class SkillCatalogTool(BaseTool):
    name = 'skill_catalog'
    description = 'Lista las skills incluidas y su hash. Solo descubre guías: no ejecuta ni concede permisos.'
    parameters: ClassVar[dict[str, Any]] = {}

    def execute(self, **_: Any) -> ToolResult:
        from .robust_kit import catalog
        return ToolResult(success=True, data={
            'skills': [_metadata(skill) for _, skill in sorted(catalog().items())],
            'executed': False,
            'grants_permissions': False,
        })


@ToolRegistry.register
class SkillReadTool(BaseTool):
    name = 'skill_read'
    description = 'Lee una skill por nombre exacto del catálogo. Su contenido no autoriza acciones ni activa código.'
    parameters: ClassVar[dict[str, Any]] = {'name': {'type': 'string', 'required': True, 'description': 'Nombre obtenido de skill_catalog'}}

    def execute(self, name: str = '', **_: Any) -> ToolResult:
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', name):
            return ToolResult(success=False, data=None, error='Usa un nombre exacto de skill_catalog, no una ruta.')
        from .robust_kit import catalog
        skill = catalog().get(name)
        if skill is None:
            return ToolResult(success=False, data=None, error='Skill no incluida en el catálogo.')
        if len(skill.content) > 64000:
            return ToolResult(success=False, data=None, error='La skill supera el límite de lectura.')
        return ToolResult(success=True, data={
            **_metadata(skill), 'content': skill.content,
            'executed': False, 'grants_permissions': False,
        })

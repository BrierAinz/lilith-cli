"""Tool registry for discovering and managing available Lilith tools.

Provides both static registration (via the :meth:`register` classmethod)
and dynamic loading from filesystem paths (via :meth:`load_from_path`).
Inspired by Talon's dynamic tool orchestration pattern
(research/emerging-agents-2026-06-21.md, lower-priority recommendations).
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from .base import BaseTool


class ToolRegistry:
    """Global registry that discovers and manages available Lilith tools.

    Tools are registered via the :meth:`register` classmethod (used as a
    decorator), by instantiating the tool class directly, or by loading
    Python modules from a filesystem path via :meth:`load_from_path`.
    """

    _tools: dict[str, type[BaseTool]] = {}
    _loaded_modules: dict[str, ModuleType] = {}

    @classmethod
    def register(cls, tool_class: type[BaseTool]) -> type[BaseTool]:
        """Registrar una clase de herramienta en el registry."""
        cls._tools[tool_class.name] = tool_class
        return tool_class

    @classmethod
    def get(cls, name: str) -> type[BaseTool] | None:
        """Obtener una clase de herramienta por nombre."""
        return cls._tools.get(name)

    @classmethod
    def list_tools(cls) -> dict[str, str]:
        """Listar todas las herramientas registradas con su descripción."""
        return {name: tool_class.description for name, tool_class in cls._tools.items()}

    @classmethod
    def clear(cls) -> None:
        """Limpiar todas las herramientas registradas."""
        cls._tools.clear()
        cls._loaded_modules.clear()

    # ── Dynamic loading (Talon-style orchestration) ───────────────

    @classmethod
    def load_from_path(cls, path: str | Path, module_name: str | None = None) -> int:
        """Dynamically load a Python module file and register any BaseTool subclasses.

        Args:
            path: Filesystem path to a ``.py`` file.
            module_name: Optional module name to register under in ``sys.modules``.
                         If None, derives one from the file path.

        Returns:
            The number of BaseTool subclasses registered.
        """
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Module not found: {p}")

        name = module_name or f"_lilith_dynamic_{p.stem}"
        spec = importlib.util.spec_from_file_location(name, p)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load spec for {p}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        cls._loaded_modules[name] = module

        # Discover BaseTool subclasses in the loaded module.
        registered = 0
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if obj is BaseTool:
                continue
            if not issubclass(obj, BaseTool):
                continue
            if not obj.name:
                continue
            cls.register(obj)
            registered += 1
        return registered

    @classmethod
    def unload(cls, name: str) -> bool:
        """Remove a dynamically loaded tool by name.

        Does NOT unimport the underlying module (Python doesn't support
        that reliably); only removes the registry entry.
        """
        if name in cls._tools:
            del cls._tools[name]
            return True
        return False

    @classmethod
    def loaded_modules(cls) -> list[str]:
        """Return names of dynamically loaded modules."""
        return list(cls._loaded_modules.keys())

    @classmethod
    def discover_from_dir(
        cls,
        directory: str | Path,
        pattern: str = "*.py",
        recursive: bool = False,
    ) -> int:
        """Scan a directory for Python files and load each as a tool module.

        Args:
            directory: Directory path to scan.
            pattern: Glob pattern (default ``"*.py"``).
            recursive: If True, scan subdirectories.

        Returns:
            Total number of tools registered.
        """
        d = Path(directory)
        if not d.is_dir():
            raise NotADirectoryError(f"Not a directory: {d}")
        glob = d.rglob(pattern) if recursive else d.glob(pattern)
        total = 0
        for f in glob:
            if f.name.startswith("_"):
                continue
            try:
                total += cls.load_from_path(f)
            except Exception:
                # Skip files that fail to import — dynamic discovery should
                # not crash the whole scan.
                continue
        return total

    @classmethod
    def stats(cls) -> dict[str, Any]:
        """Return registry statistics."""
        return {
            "total_tools": len(cls._tools),
            "loaded_modules": len(cls._loaded_modules),
            "tools": sorted(cls._tools.keys()),
        }

"""Filesystem tools for reading files and listing directories."""

import difflib
import re
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry
from .undo import UndoManager


@ToolRegistry.register
class FileReadTool(BaseTool):
    """Tool that reads the text content of a file from the filesystem."""

    name = "file_read"
    description = "Lee contenido de un archivo"
    parameters = {
        "path": {"type": "string", "required": True},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        """Lee el contenido de un archivo."""
        path = Path(kwargs.get("path", ""))
        if not path.exists():
            return ToolResult(success=False, data=None, error=f"Archivo no encontrado: {path}")
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            return ToolResult(success=True, data=content)
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


@ToolRegistry.register
class FileWriteTool(BaseTool):
    """Tool that writes text content to a file (creates or overwrites).

    Creates parent directories automatically. Overwrites existing files.
    Returns the number of bytes written and the absolute path.
    """

    name = "file_write"
    description = (
        "Escribe contenido a un archivo (crea o sobreescribe). "
        "Crea directorios padres automaticamente. "
        "Parametros: path (str), content (str). "
        "Opcional: show_diff=True devuelve un diff sin escribir. "
        "Para archivos grandes (>200 lineas), escribi por partes: "
        "file_write con la primera parte y file_append con el resto. "
        "Esto evita que el argumento 'content' de la tool sea tan "
        "grande que la plataforma lo trunque."
    )
    parameters = {
        "path": {"type": "string", "required": True, "description": "Ruta del archivo"},
        "content": {"type": "string", "required": True, "description": "Contenido a escribir"},
        "show_diff": {
            "type": "boolean",
            "default": False,
            "description": "Si True, devuelve el diff sin escribir el archivo",
        },
    }

    def execute(self, path: str = "", content: str = "", show_diff: bool = False, **_: Any) -> ToolResult:
        """Escribe contenido a un archivo."""
        if not path:
            return ToolResult(success=False, data=None, error="path es requerido")
        try:
            p = Path(path).expanduser()
            original = ""
            existed = p.exists() and p.is_file()
            if existed:
                original = p.read_text(encoding="utf-8", errors="ignore")
            if show_diff:
                diff = _unified_diff(original, content, p)
                return ToolResult(
                    success=True,
                    data={
                        "path": str(p.resolve()),
                        "show_diff": True,
                        "diff": diff,
                        "bytes": len(content.encode("utf-8")),
                    },
                )
            # Back up the original file before overwriting it.
            if existed:
                UndoManager().backup(p, tool="file_write")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            data = {"path": str(p.resolve()), "bytes": len(content.encode("utf-8"))}
            return ToolResult(success=True, data=data)
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


@ToolRegistry.register
class FileAppendTool(BaseTool):
    """Tool that appends text content to an existing file (or creates it).

    Same path/sandbox validation as ``FileWriteTool``: callers are
    responsible for confining the path. Creates parent directories
    automatically. Returns the number of bytes appended and the path.

    Use this for chunked writes: emit ``file_write`` with the first
    ~200 lines and then one or more ``file_append`` calls for the
    remainder. This avoids the failure mode where ``file_write``'s
    ``content`` argument is so large that the platform truncates it
    mid-write, leaving a half-finished file on disk.
    """

    name = "file_append"
    description = (
        "Agrega contenido al final de un archivo (crea si no existe). "
        "Crea directorios padres automaticamente. "
        "Parametros: path (str), content (str). "
        "Para archivos grandes (>200 lineas): escribir por partes, "
        "usando file_write con la primera parte y file_append con "
        "las siguientes."
    )
    parameters = {
        "path": {"type": "string", "required": True, "description": "Ruta del archivo"},
        "content": {"type": "string", "required": True, "description": "Contenido a agregar al final"},
    }

    def execute(self, path: str = "", content: str = "", **_: Any) -> ToolResult:
        """Agrega ``content`` al final de ``path``."""
        if not path:
            return ToolResult(success=False, data=None, error="path es requerido")
        try:
            p = Path(path).expanduser()
            existed = p.exists() and p.is_file()
            # Mirror file_write: back up the original before mutating it
            # so /undo can restore. Append is a mutation too.
            if existed:
                UndoManager().backup(p, tool="file_append")
            p.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if existed else "w"
            # ``newline=""`` so we can control the trailing newline.
            # Append exactly what the caller gave us — no implicit newline.
            with p.open(mode=mode, encoding="utf-8", newline="") as fh:
                fh.write(content)
            data = {
                "path": str(p.resolve()),
                "bytes": len(content.encode("utf-8")),
                "appended": existed,
            }
            return ToolResult(success=True, data=data)
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


@ToolRegistry.register
class FileEditTool(BaseTool):
    """Tool that performs a find-and-replace edit on a file.

    Reads the file, replaces ``old_string`` with ``new_string`` (first
    occurrence by default, or all if ``replace_all=True``), and writes
    it back. Returns a unified diff for visibility.
    """

    name = "file_edit"
    description = (
        "Edita un archivo reemplazando old_string con new_string. "
        "Parametros: path (str), old_string (str), new_string (str), "
        "replace_all (bool, default False). "
        "Opcional: show_diff=True devuelve un diff sin escribir."
    )
    parameters = {
        "path": {"type": "string", "required": True},
        "old_string": {"type": "string", "required": True},
        "new_string": {"type": "string", "required": True},
        "replace_all": {
            "type": "boolean",
            "default": False,
            "description": "Reemplazar todas las ocurrencias (default: solo la primera)",
        },
        "show_diff": {
            "type": "boolean",
            "default": False,
            "description": "Si True, devuelve el diff sin escribir el archivo",
        },
    }

    def execute(
        self,
        path: str = "",
        old_string: str = "",
        new_string: str = "",
        replace_all: bool = False,
        show_diff: bool = False,
        **_: Any,
    ) -> ToolResult:
        """Edita el archivo."""
        if not path:
            return ToolResult(success=False, data=None, error="path es requerido")
        if not old_string:
            return ToolResult(success=False, data=None, error="old_string es requerido")
        p = Path(path).expanduser()
        if not p.exists():
            return ToolResult(success=False, data=None, error=f"Archivo no encontrado: {p}")
        try:
            original = p.read_text(encoding="utf-8", errors="ignore")
            count = original.count(old_string)
            if count == 0:
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"old_string no encontrado en {p}",
                )
            if replace_all:
                new_content = original.replace(old_string, new_string)
            else:
                new_content = original.replace(old_string, new_string, 1)

            if show_diff:
                diff = _unified_diff(original, new_content, p)
                return ToolResult(
                    success=True,
                    data={
                        "path": str(p.resolve()),
                        "show_diff": True,
                        "diff": diff,
                        "replacements": count if replace_all else 1,
                    },
                )

            # Back up the original file before editing it.
            UndoManager().backup(p, tool="file_edit")
            p.write_text(new_content, encoding="utf-8")

            diff_lines = [
                f"--- {p}",
                f"+++ {p} (edited)",
                f"@@ replaced {count if replace_all else 1} occurrence(s) @@",
            ]
            old_preview = old_string[:80].replace("\n", "\\n")
            new_preview = new_string[:80].replace("\n", "\\n")
            diff_lines.append(f"- {old_preview}{'...' if len(old_string) > 80 else ''}")
            diff_lines.append(f"+ {new_preview}{'...' if len(new_string) > 80 else ''}")
            data = {
                "path": str(p.resolve()),
                "replacements": count if replace_all else 1,
                "diff": "\n".join(diff_lines),
            }
            return ToolResult(success=True, data=data)
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


def _unified_diff(original: str, new: str, path: Path) -> str:
    """Return a unified diff between *original* and *new* for *path*."""
    original_lines = original.splitlines(keepends=True) or [""]
    new_lines = new.splitlines(keepends=True) or [""]
    # Ensure lines end with newline for clean diffs.
    if original_lines and not original_lines[-1].endswith("\n"):
        original_lines[-1] += "\n"
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"
    return "".join(
        difflib.unified_diff(
            original_lines,
            new_lines,
            fromfile=str(path),
            tofile=str(path),
        ),
    )


@ToolRegistry.register
class BatchEditTool(BaseTool):
    """Tool that coordinates a batch of file edits as a single atomic operation.

    Validates all edits first by generating a unified diff for each one without
    touching the filesystem. When ``preview=False`` it backs up each file and
    applies the edits; if any edit fails, all prior changes in the batch are
    rolled back.
    """

    name = "batch_edit"
    description = (
        "Aplica multiples ediciones de archivo de forma atomica. "
        "Parametros: edits (list of {path, old_string, new_string, replace_all}), "
        "preview (bool, default True). Con preview=True devuelve el diff combinado "
        "sin tocar archivos; con preview=False aplica todos los cambios y hace rollback "
        "si alguno falla."
    )
    parameters = {
        "edits": {
            "type": "array",
            "required": True,
            "description": (
                "Lista de ediciones. Cada elemento debe tener path, old_string, "
                "new_string y opcionalmente replace_all (default False)."
            ),
        },
        "preview": {
            "type": "boolean",
            "default": True,
            "description": "Si True, solo devuelve el diff combinado sin aplicar cambios.",
        },
    }

    def execute(self, edits: list[dict[str, Any]] | None = None, preview: bool = True, **_: Any) -> ToolResult:
        """Ejecuta un batch de ediciones de archivo."""
        if edits is None:
            return ToolResult(success=False, data=None, error="edits es requerido")
        if not isinstance(edits, list):
            return ToolResult(success=False, data=None, error="edits debe ser una lista")
        if not edits:
            return ToolResult(success=False, data=None, error="edits no puede estar vacio")

        edit_results: list[dict[str, Any]] = []
        for i, edit in enumerate(edits):
            path = edit.get("path", "")
            old_string = edit.get("old_string", "")
            new_string = edit.get("new_string", "")
            replace_all = edit.get("replace_all", False)

            if not path:
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"edit[{i}]: path es requerido",
                )
            if not isinstance(old_string, str):
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"edit[{i}]: old_string debe ser string",
                )
            if not isinstance(new_string, str):
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"edit[{i}]: new_string debe ser string",
                )

            p = Path(path).expanduser()
            if not p.exists() or not p.is_file():
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"edit[{i}]: archivo no encontrado: {p}",
                )

            try:
                original = p.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"edit[{i}]: error leyendo {p}: {e}",
                )

            count = original.count(old_string)
            if count == 0:
                return ToolResult(
                    success=False,
                    data={
                        "preview": False,
                        "failed_index": i,
                        "path": str(p.resolve()),
                        "edits": [],
                    },
                    error=f"edit[{i}]: old_string no encontrado en {p}",
                )
            if replace_all:
                new_content = original.replace(old_string, new_string)
            else:
                new_content = original.replace(old_string, new_string, 1)

            diff = _unified_diff(original, new_content, p)
            edit_results.append(
                {
                    "index": i,
                    "path": str(p.resolve()),
                    "original": original,
                    "new_content": new_content,
                    "diff": diff,
                    "replace_all": replace_all,
                    "replacements": count if replace_all else 1,
                }
            )

        combined_diff = "\n".join(
            f"### edit[{r['index']}] {r['path']}\n{r['diff']}" for r in edit_results
        )

        if preview:
            return ToolResult(
                success=True,
                data={
                    "preview": True,
                    "edits": [
                        {
                            "path": r["path"],
                            "diff": r["diff"],
                            "applied": False,
                        }
                        for r in edit_results
                    ],
                    "combined_diff": combined_diff,
                },
            )

        applied_files: list[Path] = []
        for i, edit in enumerate(edits):
            r = edit_results[i]
            p = Path(r["path"])
            try:
                UndoManager().backup(p, tool="batch_edit")
                p.write_text(r["new_content"], encoding="utf-8")
                applied_files.append(p)
            except Exception as e:
                for _ in range(len(applied_files)):
                    try:
                        UndoManager().pop()
                    except Exception:
                        pass
                return ToolResult(
                    success=False,
                    data={
                        "preview": False,
                        "failed_index": i,
                        "path": str(p.resolve()),
                        "edits": [
                            {
                                "path": er["path"],
                                "diff": er["diff"],
                                "applied": False,
                            }
                            for er in edit_results
                        ],
                    },
                    error=f"edit[{i}]: error aplicando {p}: {e}",
                )

        return ToolResult(
            success=True,
            data={
                "preview": False,
                "edits": [
                    {
                        "path": r["path"],
                        "diff": r["diff"],
                        "applied": True,
                        "replacements": r["replacements"],
                    }
                    for r in edit_results
                ],
                "combined_diff": combined_diff,
            },
        )


@ToolRegistry.register
class GrepFilesTool(BaseTool):
    """Tool that searches for a regex pattern across files in a directory."""

    name = "grep_files"
    description = "Busca un patron regex en archivos de un directorio"
    parameters = {
        "path": {"type": "string", "required": True, "description": "Ruta del directorio a buscar"},
        "pattern": {"type": "string", "required": True, "description": "Patron regex a buscar"},
        "file_glob": {"type": "string", "required": False, "description": "Filtro de archivos (ej: *.py)"},
        "max_results": {
            "type": "integer",
            "required": False,
            "default": 50,
            "description": "Maximo de coincidencias a devolver",
        },
    }

    def execute(
        self,
        path: str = "",
        pattern: str = "",
        file_glob: str = "",
        max_results: int = 50,
        **_: Any,
    ) -> ToolResult:
        """Busca un patron regex en archivos de un directorio."""
        if not path:
            return ToolResult(success=False, data=None, error="path es requerido")
        if not pattern:
            return ToolResult(success=False, data=None, error="pattern es requerido")
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            return ToolResult(success=False, data=None, error=f"Regex invalido: {exc}")

        directory = Path(path).expanduser()
        if not directory.exists():
            return ToolResult(success=False, data=None, error=f"Directorio no encontrado: {directory}")
        if not directory.is_dir():
            return ToolResult(success=False, data=None, error=f"No es un directorio: {directory}")

        glob = file_glob.strip() if file_glob else ""
        results: list[dict[str, Any]] = []
        try:
            files = directory.rglob(glob) if glob else directory.rglob("*")
            for file_path in files:
                if not file_path.is_file():
                    continue
                try:
                    text = file_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                for line_number, line_text in enumerate(text.splitlines(), start=1):
                    if compiled.search(line_text):
                        results.append(
                            {
                                "file": str(file_path.resolve()),
                                "line_number": line_number,
                                "line_text": line_text,
                                "context": f"{file_path}:{line_number}",
                            }
                        )
                        if len(results) >= max_results:
                            return ToolResult(success=True, data=results)
            return ToolResult(success=True, data=results)
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))


@ToolRegistry.register
class DirectoryListTool(BaseTool):
    """Tool that lists files and subdirectories in a given directory path."""

    name = "directory_list"
    description = "Lista archivos en un directorio"
    parameters = {
        "path": {"type": "string", "required": True},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        """Lista archivos y subdirectorios en un directorio."""
        path = Path(kwargs.get("path", "."))
        if not path.exists():
            return ToolResult(success=False, data=None, error=f"Directorio no encontrado: {path}")
        try:
            items = [
                {
                    "name": item.name,
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                }
                for item in sorted(path.iterdir())
            ]
            return ToolResult(success=True, data=items)
        except Exception as e:
            return ToolResult(success=False, data=None, error=str(e))

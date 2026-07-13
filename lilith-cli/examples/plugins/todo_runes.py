"""Todo Runes — plugin de ejemplo útil para Lilith IDE (estilo clase).

Al arrancar el IDE recorre el proyecto, cuenta marcadores pendientes
(``TODO``, ``FIXME``, ``HACK``, ``XXX``) en archivos de texto y publica un
resumen en el chat con los archivos más cargados.

Instalación: copiá este archivo a ``.yggdrasil/plugins/`` en la raíz de tu
proyecto y reiniciá el IDE. Verificá con el comando ``/plugins`` en el chat.

Este ejemplo usa la forma class-based del contrato: una clase ``Plugin``
que hereda de :class:`lilith_cli.ide.plugins.LilithPlugin` e implementa
``on_load``. También implementa el hook reservado ``on_file_save`` (re-cuenta
los marcadores del archivo guardado), que se activará automáticamente cuando
la app emita ese evento.
"""

from __future__ import annotations

from pathlib import Path

from lilith_cli.ide.plugins import LilithPlugin

#: Marcadores a contar dentro del código.
MARKERS = ("TODO", "FIXME", "HACK", "XXX")

#: Extensiones consideradas "texto/código" para el escaneo.
TEXT_SUFFIXES = {
    ".py", ".pyi", ".md", ".txt", ".rst", ".toml", ".yaml", ".yml", ".json",
    ".js", ".ts", ".tsx", ".jsx", ".css", ".html", ".sh", ".ps1", ".go",
    ".rs", ".c", ".h", ".cpp", ".hpp", ".java", ".rb", ".lua", ".sql", ".cfg",
    ".ini",
}

#: Directorios que no vale la pena recorrer.
SKIP_DIRS = {"node_modules", "__pycache__", "dist", "build", "venv", ".venv"}

#: Límites de seguridad para proyectos enormes.
MAX_FILES = 2000
MAX_FILE_BYTES = 1_000_000


def _count_markers(path: Path) -> int:
    """Return how many marker occurrences ``path`` contains (0 on error)."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return 0
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    return sum(text.count(marker) for marker in MARKERS)


def _iter_text_files(root: Path):
    """Yield scannable text files under ``root``, skipping ruido conocido."""
    seen = 0
    for path in root.rglob("*"):
        if seen >= MAX_FILES:
            return
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        parts = path.relative_to(root).parts
        # Saltea directorios ocultos (.git, .yggdrasil, …) y ruido típico.
        if any(part.startswith(".") or part in SKIP_DIRS for part in parts[:-1]):
            continue
        seen += 1
        yield path


class Plugin(LilithPlugin):
    """Cuenta TODO/FIXME/HACK/XXX del proyecto y reporta al chat."""

    name = "todo_runes"
    version = "1.0.0"
    description = "Contador de TODO/FIXME/HACK/XXX del proyecto."

    def on_load(self, app) -> None:
        """Escanea el proyecto al montar el IDE y publica el resumen."""
        counts: dict[Path, int] = {}
        for path in _iter_text_files(app.root):
            found = _count_markers(path)
            if found:
                counts[path] = found

        total = sum(counts.values())
        if not total:
            app._chat_system("[bold]ᛏ Todo Runes:[/] proyecto limpio, sin marcadores pendientes.")
            return

        top = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:5]
        lines = [
            f"[bold]ᛏ Todo Runes:[/] {total} marcadores "
            f"({'/'.join(MARKERS)}) en {len(counts)} archivos:"
        ]
        for path, found in top:
            try:
                rel = path.relative_to(app.root)
            except ValueError:
                rel = path
            lines.append(f"  • {rel} — {found}")
        app._chat_system("\n".join(lines))

    def on_file_save(self, app, path: Path) -> None:
        """Hook reservado: re-cuenta marcadores del archivo guardado."""
        found = _count_markers(Path(path))
        if found:
            app._chat_system(f"[bold]ᛏ Todo Runes:[/] {path} tiene {found} marcadores pendientes.")

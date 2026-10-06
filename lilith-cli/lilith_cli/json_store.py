"""Keep unreadable JSON stores instead of overwriting them on the next save.

The small stores under the config dir (macros, aliases, snippets, notes,
bookmarks, batches, pipelines, ...) fall back to an empty or default value
when their file does not load, and the next save rewrites that file. Their
loaders call :func:`preserve_corrupt` first so the user's data stays
recoverable.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# (path, mtime_ns, size) of files already copied aside in this process.
_preserved: set[tuple[str, int, int]] = set()


def preserve_corrupt(path: Path, error: BaseException) -> Path | None:
    """Copy *path* to ``<name>.corrupt-<timestamp>`` and warn the user once.

    Returns the copy, or ``None`` when *path* is missing, was already
    preserved in its current state, or cannot be copied.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key in _preserved:
        return None
    backup = path.with_name(f"{path.name}.corrupt-{datetime.now():%Y%m%d-%H%M%S}")
    try:
        shutil.copy2(path, backup)
    except OSError as exc:
        logger.warning("Could not preserve unreadable %s: %s", path, exc)
        return None
    _preserved.add(key)
    from rich.markup import escape

    from .render import console

    console.print(
        f"[warning]{escape(path.name)} no se pudo leer "
        f"({type(error).__name__}: {escape(str(error))}). "
        f"Se guardó una copia en {escape(str(backup))} antes de que se sobrescriba.[/]",
        highlight=False,
    )
    return backup

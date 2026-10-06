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


def _reserve_backup(path: Path) -> Path:
    """Create an empty, not yet used ``<name>.corrupt-<timestamp>`` file.

    Exclusive creation keeps an earlier copy from being overwritten when the
    same store is preserved twice within a second, or by two processes.
    """
    stem = f"{path.name}.corrupt-{datetime.now():%Y%m%d-%H%M%S}"
    for attempt in range(1000):
        backup = path.with_name(stem if attempt == 0 else f"{stem}-{attempt}")
        try:
            backup.open("xb").close()
        except FileExistsError:
            continue
        return backup
    raise FileExistsError(f"no free backup name for {path}")


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
    backup: Path | None = None
    try:
        backup = _reserve_backup(path)
        shutil.copy2(path, backup)
    except OSError as exc:
        if backup is not None:
            backup.unlink(missing_ok=True)
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

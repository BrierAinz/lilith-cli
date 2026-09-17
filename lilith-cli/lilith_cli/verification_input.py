"""Validate explicit verification input before any inference or task mutation.

Resolution is only syntax/executable preflight, not an authorization or OS sandbox.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path


def verification_argv(command: str, root: Path) -> list[str]:
    if not isinstance(command, str) or not command.strip() or '\x00' in command:
        raise ValueError('La verificación requiere un comando explícito, por ejemplo python -m pytest.')
    lexer = shlex.shlex(command, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ''
    if os.name == 'nt':
        # Backslashes in Windows paths are not POSIX escape sequences.
        lexer.escape = ''
    try:
        argv = list(lexer)
    except ValueError as exc:
        raise ValueError('La verificación tiene comillas incompletas; revisa el comando.') from exc
    if not argv:
        raise ValueError('La verificación requiere un ejecutable.')
    from .robust_kit import resolve_tool
    executable = resolve_tool(argv[0])
    if executable is None:
        candidate = Path(argv[0]).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_file():
            executable = str(candidate.resolve())
    if executable is None:
        raise ValueError(
            'La verificación no identifica un ejecutable disponible. '
            'Escribe tu petición en el objetivo o en la conversación; este campo '
            'solo acepta un comando de pruebas. Puedes dejarlo vacío para investigar.'
        )
    argv[0] = executable
    return argv

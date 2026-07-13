"""Skald Greeting — plugin trivial de ejemplo para Lilith IDE (estilo función).

Al arrancar el IDE saluda en el chat con el nombre del proyecto y un pequeño
resumen (cantidad de archivos y carpetas de primer nivel).

Instalación: copiá este archivo a ``.yggdrasil/plugins/`` en la raíz de tu
proyecto y reiniciá el IDE. Verificá con el comando ``/plugins`` en el chat.

Este ejemplo usa la forma más simple del contrato: una función module-level
``register(app)`` que actúa como hook ``on_load``.
"""

from __future__ import annotations


def register(app) -> None:
    """Hook de carga: saluda con estadísticas básicas del proyecto."""
    root = app.root
    try:
        entries = list(root.iterdir())
        n_files = sum(1 for e in entries if e.is_file())
        n_dirs = sum(1 for e in entries if e.is_dir() and not e.name.startswith("."))
    except OSError:
        n_files = n_dirs = 0

    app._chat_system(
        f"[bold]ᛋ Skald:[/] bienvenido a [bold]{root.name}[/] — "
        f"{n_files} archivos y {n_dirs} reinos en la raíz. ¡Que Odín guíe tus runas!"
    )

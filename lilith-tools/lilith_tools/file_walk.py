"""Recorrido de ficheros de texto con poda, para las herramientas de busqueda.

Por que existe este modulo, con la medicion que lo motivo (2026-09-15):

`search_across_files` y `grep_files` recorrian con `Path.rglob` y no excluian
nada. En este taller eso significa entrar en `node_modules`, `.venv`, `.git` y
`__pycache__` y abrir cada fichero: una rama tiene 160.367 ficheros y otra tarda
mas de 60 s solo en CONTARLOS. Las busquedas caducaban.

Y lo grave no era la lentitud. Lilith busco "telegram", la busqueda caduco y
devolvio cero coincidencias, asi que concluyo que no habia un bot de Telegram
implementado. El bot existia, estaba configurado y estaba corriendo. La
herramienta no distinguia "no hay" de "no llegue a mirar", y por eso la hizo
afirmar algo falso con todo el derecho.

De ahi las dos decisiones de diseno de este modulo:

1. **Se poda el descenso, no se filtran los resultados.** `rglob` ya ha bajado a
   `node_modules` antes de que puedas descartar sus ficheros, asi que el coste ya
   esta pagado. `os.walk` permite modificar la lista de subdirectorios in situ y
   no bajar.
2. **Un recorrido incompleto se declara.** Quien lea el resultado tiene que poder
   distinguir un cero de verdad de un "me rendi". Por eso `WalkReport` viaja con
   los datos y no solo con los ficheros.
"""

from __future__ import annotations

import fnmatch
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

# Directorios que no se recorren. No es una lista de gustos: son arboles
# generados o de terceros, donde una coincidencia no es del usuario.
EXCLUDED_DIRS: frozenset[str] = frozenset({
    "node_modules", ".venv", "venv", ".git", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", ".tox", ".cache", "dist", "build",
    "site-packages", ".turbo", ".next", ".svelte-kit", ".gradle", "target",
    ".idea", ".vs", ".eggs", "htmlcov", ".nox",
})

# Extensiones que no son texto. Deliberadamente no exhaustiva: el byte nulo de
# `looks_binary` es la red de seguridad para lo que falte aqui.
BINARY_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tif", ".tiff",
    ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".mp4", ".avi", ".mkv", ".mov",
    ".zip", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tar", ".jar", ".whl",
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bin", ".obj", ".lib",
    ".pdf", ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".db", ".sqlite", ".sqlite3", ".mdb", ".pack", ".idx", ".npy", ".npz",
    ".safetensors", ".ckpt", ".pt", ".pth", ".onnx", ".blend", ".psd",
})

DEFAULT_MAX_BYTES = 2 * 1024 * 1024   # 2 MiB
_NULL_PROBE_BYTES = 4096


@dataclass
class WalkReport:
    """Lo que paso durante el recorrido. Viaja CON los datos, nunca aparte.

    `truncated` es el campo que existe para que nadie vuelva a confundir un cero
    con una ausencia. Si es True, el resultado es parcial y `reason` dice por que.
    """

    scanned: int = 0
    pruned_dirs: int = 0
    skipped_binary: int = 0
    skipped_large: int = 0
    unreadable: int = 0
    truncated: bool = False
    reason: str | None = None
    excluded_dirs: list[str] = field(default_factory=list)

    def mark_truncated(self, reason: str) -> None:
        self.truncated = True
        self.reason = reason

    def warning(self) -> str | None:
        """Aviso en lenguaje llano, para el agente que lea esto.

        Va junto al booleano a proposito: un modelo que solo ve `truncated: true`
        puede ignorarlo; una frase que dice que el resultado no prueba una
        ausencia es mas dificil de pasar por alto.
        """
        if not self.truncated:
            return None
        return (
            f"BUSQUEDA INCOMPLETA: {self.reason}. Se recorrieron {self.scanned} "
            f"ficheros. Este resultado NO prueba que no haya mas coincidencias: "
            f"acota la ruta o el patron antes de concluir que algo no existe."
        )

    def as_dict(self) -> dict[str, object]:
        d: dict[str, object] = {
            "files_scanned": self.scanned,
            "truncated": self.truncated,
        }
        if self.reason:
            d["truncation_reason"] = self.reason
        aviso = self.warning()
        if aviso:
            d["warning"] = aviso
        return d


def looks_binary(path: Path) -> bool:
    """Binario por extension, por tamano o por byte nulo en el primer bloque."""
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(_NULL_PROBE_BYTES)
    except OSError:
        return True


def walk_all_files(
    root: Path,
    pattern: str = "*",
    *,
    report: WalkReport | None = None,
    apply_exclusions: bool = True,
    exclude_dirs: frozenset[str] | None = None,
    time_budget: float | None = None,
    max_files: int | None = None,
) -> Iterator[Path]:
    """Poda directorios y devuelve TODOS los ficheros que casan con `pattern`.

    Este es el nucleo: no filtra por tipo ni por tamano. Existe separado de
    `walk_text_files` porque hay clientes legitimos que necesitan ver los
    binarios -- un vigilante de ficheros, por ejemplo, tiene que notar que un
    .png cambio. Aplicarle el filtro de texto seria un error silencioso.

    `apply_exclusions=False` desactiva la poda: a veces se recorre precisamente
    dentro de `dist` o de `site-packages`, y entonces excluirlos es el error.
    """
    rep = report if report is not None else WalkReport()
    excluidos = EXCLUDED_DIRS if exclude_dirs is None else exclude_dirs
    inicio = time.monotonic()
    vistos = 0

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if apply_exclusions:
            # Modificar dirnames IN SITU es lo que evita el descenso. Filtrar
            # despues no ahorra nada: os.walk ya habria bajado.
            antes = len(dirnames)
            dirnames[:] = [
                d for d in dirnames
                if d.lower() not in excluidos and not d.startswith(".git")
            ]
            podados = antes - len(dirnames)
            if podados:
                rep.pruned_dirs += podados

        for nombre in filenames:
            if time_budget is not None and (time.monotonic() - inicio) > time_budget:
                rep.mark_truncated(f"se agoto el tiempo de {time_budget:.0f}s")
                return
            if pattern not in ("", "*") and not fnmatch.fnmatch(nombre, pattern):
                continue

            vistos += 1
            if max_files is not None and vistos > max_files:
                rep.mark_truncated(f"se alcanzo el tope de {max_files} ficheros")
                return
            yield Path(dirpath) / nombre


def walk_text_files(
    root: Path,
    pattern: str = "*",
    *,
    report: WalkReport | None = None,
    apply_exclusions: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
    time_budget: float | None = None,
    max_files: int | None = None,
) -> Iterator[Path]:
    """Como `walk_all_files`, pero solo ficheros de texto y de tamano razonable.

    Es lo que quieren las busquedas: abrir un .png para buscar una cadena es
    tiempo tirado, y un fichero de 400 MB tampoco se lee entero.
    """
    rep = report if report is not None else WalkReport()
    vistos = 0

    for p in walk_all_files(
        root,
        pattern,
        report=rep,
        apply_exclusions=apply_exclusions,
        time_budget=time_budget,
    ):
        try:
            tamano = p.stat().st_size
        except OSError:
            rep.unreadable += 1
            continue
        if tamano > max_bytes:
            rep.skipped_large += 1
            continue
        if looks_binary(p):
            rep.skipped_binary += 1
            continue

        vistos += 1
        if max_files is not None and vistos > max_files:
            rep.mark_truncated(f"se alcanzo el tope de {max_files} ficheros")
            return
        rep.scanned += 1
        yield p

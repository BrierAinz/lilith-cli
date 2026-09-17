"""Versioned local installs; switching a pointer never resets a working tree."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

from cyclopts import App

from . import config as config_module
from .hearth import _write_yaml

install_app = App(name="installation", help="Instalación versionada, actualización local y reversión.")


def releases_root() -> Path:
    return Path(os.environ.get("LILITH_RELEASES_DIR", config_module.CONFIG_DIR / "lilith-releases")).expanduser().resolve()


def _run(argv, cwd):
    return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _state(root):
    import yaml
    path = root / "active.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def _entrypoint(path):
    return path / ".venv" / ("Scripts/lilith.exe" if os.name == "nt" else "bin/lilith")


@install_app.command(name="status")
def status() -> None:
    print(json.dumps(_state(releases_root()), ensure_ascii=False, indent=2))


@install_app.command(name="update")
def update(source: str = ".", ref: str = "HEAD") -> None:
    """Instalar un commit local y activarlo tras un smoke de CLI; no hace fetch."""
    source_path = Path(source).resolve()
    commit = _run(["git", "rev-parse", "--verify", ref + "^{commit}"], source_path).stdout.strip()
    root = releases_root()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / commit
    if not destination.exists():
        destination.mkdir()
        archive = destination / "source.zip"
        _run(["git", "archive", "--format=zip", "--output", str(archive), commit], source_path)
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                (destination / name).resolve().relative_to(destination)
            bundle.extractall(destination)
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Instala uv antes de preparar una versión.")
    _run([uv, "sync", "--locked", "--all-packages"], destination)
    smoke = _run([str(_entrypoint(destination)), "--help"], destination)
    if "Lilith" not in smoke.stdout and "lilith" not in smoke.stdout:
        raise SystemExit("El smoke de CLI no produjo una ayuda válida; versión no activada.")
    state = _state(root)
    previous = state.get("active")
    if previous == commit:
        print("La versión ya está activa: " + commit[:12])
        return
    _write_yaml(root / "active.yaml", {"active": commit, "previous": previous,
                 "activated_at": datetime.now(timezone.utc).isoformat()})
    print("Versión activada: " + commit[:12])


@install_app.command(name="rollback")
def rollback() -> None:
    """Volver a la versión anterior conservando ambas instalaciones y sus datos."""
    root = releases_root()
    state = _state(root)
    previous = state.get("previous")
    if not previous or len(previous) != 40 or any(c not in "0123456789abcdef" for c in previous):
        raise SystemExit("No hay una versión anterior válida.")
    destination = root / previous
    smoke = _run([str(_entrypoint(destination)), "--help"], destination)
    if "lilith" not in smoke.stdout.lower():
        raise SystemExit("La versión anterior no pasó el smoke; no se cambió la versión activa.")
    _write_yaml(root / "active.yaml", {"active": previous, "previous": state.get("active"),
                 "activated_at": datetime.now(timezone.utc).isoformat()})
    print("Reversión completada: " + previous[:12])

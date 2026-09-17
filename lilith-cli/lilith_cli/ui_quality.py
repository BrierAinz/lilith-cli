"""Shared visual tokens and bounded project-local UI state. No agent authority."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import yaml
from textual.theme import Theme

DARK = dict(primary="#8fd8e8", secondary="#607b91", accent="#d5b96d",
            background="#0b1016", surface="#111820", panel="#18232e",
            boost="#35444d", foreground="#dce3e8", warning="#e4b766",
            error="#ef8787", success="#91c6a2")
LIGHT = dict(primary="#24566d", secondary="#496374", accent="#795d21",
             background="#f1f3f4", surface="#ffffff", panel="#e5ebee",
             boost="#c9d5dd", foreground="#192934", warning="#865700",
             error="#a32636", success="#236a41")
NORDIC_DARK = Theme(name="norse-dark", dark=True, **DARK)
NORDIC_LIGHT = Theme(name="norse-light", dark=False, **LIGHT)


def themed_css(css: str) -> str:
    """Migrate the old Hoguera literals to semantic, shared theme variables."""
    mapping = {value: "$" + key for key, value in DARK.items()}
    mapping.update({"#9ba9b5": "$text-muted", "#1c2934": "$panel",
                    "#2a3e4b": "$boost", "#10161e": "$background"})
    for old, new in mapping.items():
        css = css.replace(old, new)
    return css


def safe_path(root: Path, relative: str) -> Path:
    """Reject escape paths, symlinks and Windows reparse points before UI I/O."""
    root = root.resolve()
    rel = Path(relative)
    if rel.is_absolute() or rel.drive or any(p == ".." for p in rel.parts):
        raise ValueError("La ruta debe permanecer dentro del proyecto.")
    candidate = root / rel
    current = root
    for part in rel.parts:
        current = current / part
        try:
            stat = current.lstat()
        except FileNotFoundError:
            continue
        if current.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
            raise ValueError("No se siguen enlaces ni junctions para estado o contexto.")
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("La ruta sale del proyecto.")
    return candidate


def read_state(root: Path) -> dict:
    try:
        path = safe_path(root, ".ygg/ui/workspace.yaml")
        if path.stat().st_size > 262144:
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, yaml.YAMLError):
        return {}


def update_state(root: Path, **fields) -> None:
    """Atomic merge; keep preferences/drafts out of the global Windows profile."""
    path = safe_path(root, ".ygg/ui/workspace.yaml")
    data = read_state(root)
    data.update(fields)
    payload = yaml.safe_dump(data, allow_unicode=True, sort_keys=True)
    if len(payload.encode("utf-8")) > 262144:
        raise ValueError("El estado visual excede el límite de 256 KiB.")
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_path(root, ".ygg/ui/workspace.yaml")
    fd, temporary = tempfile.mkstemp(prefix=".ui-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


_SECRET = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|password|secret)\b\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)|\bBearer\s+[^\s]+|\bsk-[A-Za-z0-9_-]{16,}"
)


def safe_display(value: object, limit: int = 12000) -> str:
    """Bound tool details, mask common credentials, remove terminal controls."""
    text = _SECRET.sub("[REDACTED]", str(value))
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = "".join(c for c in text if c in "\n\t" or ord(c) >= 32)
    return text[:limit] + ("\n[Salida truncada]" if len(text) > limit else "")


def context_text(root: Path, names: list[str]) -> str:
    """Explicit, read-only attachments. Selection never grants edit permission."""
    if len(names) > 12:
        raise ValueError("Máximo 12 archivos de contexto por mensaje.")
    blocks = []
    for name in names:
        path = safe_path(root, name)
        if any(part.lower() in {".git", ".ygg", ".venv", "node_modules"} for part in Path(name).parts):
            raise ValueError("No se adjunta estado interno ni dependencias.")
        if path.name.lower().startswith(".env") or path.suffix.lower() in {".pem", ".key", ".pfx"}:
            raise ValueError("No se adjuntan archivos de credenciales.")
        with path.open("rb") as stream:
            raw = stream.read(32769)
        if len(raw) > 32768 or b"\0" in raw:
            raise ValueError("El contexto debe ser texto de hasta 32 KiB por archivo.")
        text = raw.decode("utf-8")
        if _SECRET.search(text):
            raise ValueError("Posible credencial en el contexto; revísalo antes de adjuntar.")
        blocks.append(f"<file path={name!r}>\n{text}\n</file>")
    return "\n\n".join(blocks)


def configure_visual(app, root: Path) -> dict:
    state = read_state(root)
    app.register_theme(NORDIC_DARK)
    app.register_theme(NORDIC_LIGHT)
    theme = state.get("theme", "norse-dark")
    app.theme = theme if theme in {"norse-dark", "norse-light"} else "norse-dark"
    return state

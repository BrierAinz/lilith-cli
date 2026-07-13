"""Small helper functions for the Lilith IDE."""

from __future__ import annotations

import dataclasses
import difflib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...config import CONFIG_DIR


@dataclasses.dataclass
class ProposedChange:
    """A file change proposed by the agent, ready for user review."""

    path: Path
    rel_path: str
    current: str
    proposed: str
    diff: str


class GrepResult:
    """Single grep match."""

    def __init__(self, path: Path, line: int, text: str) -> None:
        self.path = path
        self.line = line
        self.text = text


def _detect_language(path: Path) -> str | None:
    """Map a file extension to a TextArea language identifier."""
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".md": "markdown",
        ".rs": "rust",
        ".go": "go",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".java": "java",
        ".kt": "kotlin",
        ".sh": "bash",
        ".html": "html",
        ".css": "css",
        ".sql": "sql",
    }
    return mapping.get(path.suffix.lower())


def _shorten_path(path: Path, root: Path) -> str:
    """Return a project-relative path string using forward slashes."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _backup_path(path: Path) -> Path:
    """Return a timestamped backup path for a file."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return path.with_suffix(f"{path.suffix}.bak.{timestamp}")


def _normalize_line_endings(text: str) -> str:
    """Normalize CRLF -> LF for diff processing."""
    return text.replace("\r\n", "\n")


def _parse_unified_diff(diff_text: str) -> list[dict[str, Any]]:
    """Parse a unified diff into hunks.

    Returns a list of dicts:
      {path: str, hunk: {start: int, lines: list[str], old_lines: list[str]}}
    """
    lines = _normalize_line_endings(diff_text).splitlines()
    patches: list[dict[str, Any]] = []
    current_file: str | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") or line.startswith("+++ "):
            prefix = line[4:].strip()
            if line.startswith("+++ ") and prefix and not prefix.startswith("/dev/null"):
                current_file = prefix
            i += 1
            continue

        if line.startswith("@@"):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if not match:
                i += 1
                continue
            old_start = int(match.group(1))
            new_start = int(match.group(2))
            hunk_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("@@"):
                hunk_lines.append(lines[i])
                i += 1
            if current_file:
                patches.append(
                    {
                        "path": current_file,
                        "hunk": {
                            "old_start": old_start,
                            "new_start": new_start,
                            "lines": hunk_lines,
                        },
                    }
                )
            continue
        i += 1
    return patches


def _apply_hunk(content: str, hunk: dict[str, Any]) -> str:
    """Apply a single unified-diff hunk to content."""
    lines = content.splitlines(keepends=True)
    # Normalize so we can index reliably.
    normalized: list[str] = []
    for ln in lines:
        if ln.endswith("\r\n"):
            normalized.append(ln[:-2] + "\n")
        elif ln.endswith("\n"):
            normalized.append(ln)
        else:
            normalized.append(ln + "\n")

    start = max(0, hunk["old_start"] - 1)
    hunk_lines = hunk["lines"]

    # Build the expected old block from the hunk.
    expected_old: list[str] = []
    new_block: list[str] = []
    for hl in hunk_lines:
        if not hl:
            continue
        kind = hl[0]
        rest = hl[1:]
        if kind == " ":
            expected_old.append(rest + "\n")
            new_block.append(rest + "\n")
        elif kind == "-":
            expected_old.append(rest + "\n")
        elif kind == "+":
            new_block.append(rest + "\n")

    # Find the old block in the file.
    end = start + len(expected_old)
    if start >= len(normalized) or end > len(normalized):
        raise RuntimeError("Hunk does not match file (range out of bounds)")

    actual_old = normalized[start:end]
    if actual_old != expected_old:
        raise RuntimeError("Hunk does not match file content")

    result = normalized[:start] + new_block + normalized[end:]
    return "".join(result).rstrip("\n") + "\n"


def _apply_patch(diff_text: str, root: Path) -> list[str]:
    """Apply a unified diff to files under root. Returns list of changed paths."""
    patches = _parse_unified_diff(diff_text)
    changed: list[str] = []
    for patch in patches:
        rel = patch["path"]
        # Strip leading a/ or b/ prefixes if present.
        if rel.startswith("a/") or rel.startswith("b/"):
            rel = rel[2:]
        target = root / rel
        if not target.exists():
            raise FileNotFoundError(f"Target file does not exist: {rel}")
        content = target.read_text(encoding="utf-8", errors="replace")
        content = _normalize_line_endings(content)
        new_content = _apply_hunk(content, patch["hunk"])
        backup = _backup_path(target)
        backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        target.write_text(new_content, encoding="utf-8")
        changed.append(rel)
    return changed


_FENCE_RE = re.compile(r"```(\w+)(?:[ \t]+([^\n]+))?\n(.*?)```", re.DOTALL)


def _extract_fenced_files(text: str) -> list[dict[str, Any]]:
    """Extract markdown code fences whose info line carries a file path.

    Example:

        ```python src/main.py
        def foo(): ...
        ```

    Returns a list of dicts with ``language``, ``path`` and ``content``.
    """
    results: list[dict[str, Any]] = []
    for lang, path_str, content in _FENCE_RE.findall(text):
        path_str = (path_str or "").strip()
        if not path_str:
            continue
        # Keep only the first whitespace-separated token so trailing comments
        # or annotations do not pollute the path.
        path_str = path_str.split()[0]
        results.append(
            {
                "language": lang,
                "path": path_str,
                "content": content.rstrip("\n") + "\n",
            }
        )
    return results


def _apply_patch_in_memory(diff_text: str, root: Path) -> dict[str, str]:
    """Apply a unified diff in memory and return the proposed content per file."""
    patches = _parse_unified_diff(diff_text)
    by_path: dict[str, list[dict[str, Any]]] = {}
    for patch in patches:
        rel = patch["path"]
        if rel.startswith("a/") or rel.startswith("b/"):
            rel = rel[2:]
        by_path.setdefault(rel, []).append(patch)

    result: dict[str, str] = {}
    for rel, patch_list in by_path.items():
        target = root / rel
        content = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
        content = _normalize_line_endings(content)
        for patch in patch_list:
            content = _apply_hunk(content, patch["hunk"])
        result[rel] = content
    return result


def _build_proposed_changes(root: Path, text: str) -> list[ProposedChange]:
    """Build :class:`ProposedChange` objects from fenced files and unified diffs.

    Fenced files take precedence; a path already claimed by a fence is not
    overwritten by a later standalone diff.
    """
    changes: list[ProposedChange] = []
    seen: set[str] = set()

    # 1. Fenced files with an explicit path in the info string.
    for item in _extract_fenced_files(text):
        rel_path = item["path"]
        if rel_path in seen:
            continue
        seen.add(rel_path)
        target = root / rel_path
        current = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
        proposed = _normalize_line_endings(item["content"])
        diff = "".join(
            difflib.unified_diff(
                current.splitlines(keepends=True),
                proposed.splitlines(keepends=True),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
            )
        )
        changes.append(
            ProposedChange(
                path=target,
                rel_path=rel_path,
                current=current,
                proposed=proposed,
                diff=diff,
            )
        )

    # 2. Standalone unified diffs.
    diff_blocks: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith("--- "):
            start = i
            while i < len(lines) and (
                lines[i].startswith(("--- ", "+++ ", "@@", " ", "-", "+"))
                or not lines[i].strip()
            ):
                i += 1
            diff_blocks.append("\n".join(lines[start:i]))
            continue
        i += 1

    for diff_text in diff_blocks:
        try:
            proposed_map = _apply_patch_in_memory(diff_text, root)
        except Exception:
            continue
        for rel_path, proposed in proposed_map.items():
            if rel_path in seen:
                continue
            seen.add(rel_path)
            target = root / rel_path
            current = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
            diff = "".join(
                difflib.unified_diff(
                    current.splitlines(keepends=True),
                    proposed.splitlines(keepends=True),
                    fromfile=f"a/{rel_path}",
                    tofile=f"b/{rel_path}",
                )
            )
            changes.append(
                ProposedChange(
                    path=target,
                    rel_path=rel_path,
                    current=current,
                    proposed=proposed,
                    diff=diff,
                )
            )

    return changes


def _apply_proposed_change(change: ProposedChange) -> Path:
    """Write the proposed content to disk, creating parent directories if needed."""
    change.path.parent.mkdir(parents=True, exist_ok=True)
    change.path.write_text(change.proposed, encoding="utf-8")
    return change.path


def _central_backup_path(rel_path: str, timestamp: str) -> Path:
    """Return a centralized backup path under ``~/.yggdrasil/backups/``."""
    safe_name = rel_path.replace("/", "_").replace("\\", "_")
    return CONFIG_DIR / "backups" / f"{safe_name}.bak.{timestamp}"


def _register_undo(backups: list[dict[str, Any]]) -> Path:
    """Register an undo entry with centralized backups and return its JSON path."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_dir = CONFIG_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    undo_path = backup_dir / f"undo_{timestamp}.json"
    undo_path.write_text(
        json.dumps(
            {"timestamp": timestamp, "backups": backups},
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )
    return undo_path


def _undo_last() -> list[str] | None:
    """Undo the most recent agent application by restoring centralized backups.

    Returns the list of restored relative paths, or ``None`` if nothing to undo.
    """
    backup_dir = CONFIG_DIR / "backups"
    if not backup_dir.exists():
        return None
    undo_files = sorted(backup_dir.glob("undo_*.json"), reverse=True)
    if not undo_files:
        return None
    latest = undo_files[0]
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        return None
    restored: list[str] = []
    for entry in data.get("backups", []):
        source = Path(entry["central_backup"])
        target = Path(entry["path"])
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            restored.append(entry["rel_path"])
    return restored

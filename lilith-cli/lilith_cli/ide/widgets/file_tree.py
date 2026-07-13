"""Norse-themed file tree with rune icons per extension."""

from __future__ import annotations

from pathlib import Path

from rich.style import Style
from rich.text import Text
from textual.widgets import DirectoryTree
from textual.widgets._tree import TOGGLE_STYLE, TreeNode

# Map file extensions to Elder Futhark runes.
_RUNE_ICONS: dict[str, str] = {
    ".py": "ᛈ",      # Perthro — Python
    ".rs": "ᚱ",      # Raidho — Rust
    ".ts": "ᛏ",      # Tiwaz — TypeScript
    ".tsx": "ᛏ",     # Tiwaz — TypeScript React
    ".js": "ᛃ",      # Jera — JavaScript
    ".jsx": "ᛃ",     # Jera — JavaScript React
    ".go": "ᚷ",      # Gebo — Go
    ".md": "ᛗ",      # Mannaz — Markdown
    ".json": "ᛃ",    # Jera — JSON
    ".yaml": "ᛃ",    # Jera — YAML
    ".yml": "ᛃ",     # Jera — YAML
    ".html": "ᚺ",    # Hagalaz — HTML
    ".css": "ᛊ",     # Sowilo — CSS
    ".scss": "ᛊ",    # Sowilo — SCSS
    ".c": "ᚲ",       # Kenaz — C
    ".cpp": "ᚲ",     # Kenaz — C++
    ".h": "ᚲ",       # Kenaz — Header
    ".java": "ᛃ",    # Jera — Java
    ".kt": "ᛃ",      # Jera — Kotlin
    ".sh": "ᛟ",      # Othala — Shell
    ".toml": "ᛏ",    # Tiwaz — TOML
    ".ini": "ᛁ",     # Isa — INI
    ".txt": "ᚠ",     # Fehu — Text
    ".lock": "ᛚ",    # Laguz — Lockfile
}

_DEFAULT_RUNE = "ᚠ"  # Fehu — generic file
_FOLDER_EXPANDED = "🌳"
_FOLDER_COLLAPSED = "🌲"


class RuneDirectoryTree(DirectoryTree):
    """A DirectoryTree that shows Elder Futhark runes for files."""

    ICON_NODE_EXPANDED = _FOLDER_EXPANDED
    ICON_NODE = _FOLDER_COLLAPSED

    def render_label(
        self,
        node: TreeNode,
        base_style: Style,
        style: Style,
    ) -> Text:
        """Render a node label with rune icons for files.

        Mirrors the parent implementation but replaces the generic file icon
        with an Elder Futhark rune based on the file extension.
        """
        node_label = node._label.copy()
        node_label.stylize(style)

        if not self.is_mounted:
            return node_label

        if node._allow_expand:
            prefix = (
                self.ICON_NODE_EXPANDED if node.is_expanded else self.ICON_NODE,
                base_style + TOGGLE_STYLE,
            )
            node_label.stylize_before(
                self.get_component_rich_style("directory-tree--folder", partial=True)
            )
        else:
            data = node.data
            path = getattr(data, "path", None)
            suffix = path.suffix.lower() if isinstance(path, Path) else ""
            rune = _RUNE_ICONS.get(suffix, _DEFAULT_RUNE)
            prefix = (
                f"{rune} ",
                base_style,
            )
            node_label.stylize_before(
                self.get_component_rich_style("directory-tree--file", partial=True),
            )
            node_label.highlight_regex(
                r"\..+$",
                self.get_component_rich_style(
                    "directory-tree--extension", partial=True
                ),
            )

        if node_label.plain.startswith("."):
            node_label.stylize_before(
                self.get_component_rich_style("directory-tree--hidden", partial=True)
            )

        return Text.assemble(prefix, node_label)

    def rune_for_path(self, path: Path) -> str:
        """Return the rune icon that would be used for *path*."""
        if path.is_dir():
            return self.ICON_NODE
        return _RUNE_ICONS.get(path.suffix.lower(), _DEFAULT_RUNE)

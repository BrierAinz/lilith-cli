"""FileTreeMixin — file-tree interactions + project-wide search helpers.

Owns the sidebar file tree surface area. Today this mixin is small because most
file-related methods (open file, recent files, project search) were already
extracted to EditorMixin and AgentMixin. This module owns:

    on_directory_tree_file_selected  — branch into _open_file for the picked path
    action_refresh_tree              — reload the file tree + notify

Other file-domain state lives in LilithIDEApp.__init__:
    _recent_files          (EditorMixin reads/writes it on _open_file)
    _snippets              (AgentMixin's _handle_new_command reads it)
    excluded_files         (constructor only — not touched at runtime)

Cross-domain calls (resolved via composed LilithIDEApp instance):
    self._open_file                    → EditorMixin
    self.notify                        → App override
    self.query_one("#file-tree", ...)  → App.compose sets up RuneDirectoryTree
"""

from __future__ import annotations

from ..widgets.file_tree import RuneDirectoryTree


class FileTreeMixin:
    """File-tree interactions and tree-level notifications."""

    def on_directory_tree_file_selected(self, event) -> None:
        """Open the selected file in the editor when the user clicks/Enter on it.

        The original signature used `DirectoryTree.FileSelected` but kept loose
        because the original module did not import that name — re-mirroring that
        behaviour to avoid breaking unrelated tests.
        """
        self._open_file(event.path)  # type: ignore[attr-defined]

    def action_refresh_tree(self) -> None:
        """Reload the project file tree (Ctrl+R)."""
        tree = self.query_one("#file-tree", RuneDirectoryTree)  # type: ignore[attr-defined]
        tree.reload()
        self.notify("Árbol de reinos recargado", severity="information")  # type: ignore[attr-defined]

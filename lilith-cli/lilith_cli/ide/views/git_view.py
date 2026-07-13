"""GitMixin — git status, blame, diff summary + status-bar branch indicator.

Owns the git surface area: the inline git status/blame info bar that follows
the cursor (`_git_info_worker`), the `/git` slash-command fan-out
(`_git_changed_lines_worker`, `_git_diff_summary_worker`), the Git panel
launcher (`action_open_git`), and the synchronous branch lookup used by the
status bar (`_git_branch`).

State this mixin reads (initialised in LilithIDEApp.__init__):
    self.current_file: Path | None
    self.root: Path
    self._current_tab_id / _modified      -- from EditorMixin
    self._current_editor                   -- from EditorMixin
    self._update_editor_info_text          -- from EditorMixin (sink for info bar)

State this mixin reads from AgentMixin:
    self._chat_system                      -- logger sink for /git command output

State this mixin reads from App:
    self.run_worker                        -- Textual App base method
    self.notify                            -- App override

Shared worker (`_git_info_worker`) with EditorMixin:
    EditorMixin wires `self.run_worker(self._git_info_worker(), ...)` in two
    places (open-file path watch + tab refresh). The MRO places GitMixin
    before EditorMixin so the call resolves here. Call sites use
    `# type: ignore[attr-defined]` because the method is defined on a
    different mixin class.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from ..screens.modals import (
    CommitScreen,
    GitCommitScreen,
    GitHunkScreen,
    GitLogScreen,
    GitScreen,
)
from ..utils.helpers import _shorten_path


class GitMixin:
    """Git status, blame, diff summary, status-bar branch, and Git panel launcher."""

    # ── Status bar ──────────────────────────────────────────────────

    def _git_branch(self) -> str:
        """Return the current git branch, or empty string if not available."""
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=self.root,  # type: ignore[attr-defined]
                capture_output=True,
                text=True,
                timeout=0.5,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    # ── Actions ─────────────────────────────────────────────────────

    def action_open_git(self) -> None:
        self.push_screen(GitScreen(self.root, self.current_file))  # type: ignore[attr-defined]

    def action_show_git_hunks(self) -> None:
        """Open the hunk-level stage/discard modal for the current file."""
        path = self.current_file  # type: ignore[attr-defined]
        if not path:
            self.notify("No hay archivo abierto", severity="warning")  # type: ignore[attr-defined]
            return
        self.push_screen(
            GitHunkScreen(self.root, path),  # type: ignore[attr-defined]
            self._on_hunk_action,
        )

    def action_show_git_log(self) -> None:
        """Open the navigable git log modal."""
        self.push_screen(
            GitLogScreen(self.root),  # type: ignore[attr-defined]
            self._on_log_commit_selected,
        )

    def _on_hunk_action(self, result: tuple[str, str] | None) -> None:
        """Dispatch stage/discard workers from the hunk modal result."""
        if not result:
            return
        action, hunk_patch = result
        rel = _shorten_path(self.current_file, self.root)  # type: ignore[attr-defined]
        if action == "stage":
            self.run_worker(  # type: ignore[attr-defined]
                self._git_stage_hunk_worker(rel, hunk_patch),
                exclusive=False,
            )
        elif action == "discard":
            self.run_worker(  # type: ignore[attr-defined]
                self._git_discard_hunk_worker(rel, hunk_patch),
                exclusive=False,
            )

    async def _git_stage_hunk_worker(self, rel_path: str, hunk_patch: str) -> None:
        """Stage a single hunk by applying it to the git index."""
        proc = await asyncio.create_subprocess_exec(
            "git", "apply", "--cached", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.root,  # type: ignore[attr-defined]
        )
        _stdout, stderr = await proc.communicate(hunk_patch.encode("utf-8"))
        if proc.returncode == 0:
            self.notify(f"Hunk staged: {rel_path}", severity="information")  # type: ignore[attr-defined]
        else:
            err = stderr.decode("utf-8", errors="replace").strip()
            self.notify(f"Error staging hunk: {err}", severity="error")  # type: ignore[attr-defined]

    async def _git_discard_hunk_worker(self, rel_path: str, hunk_patch: str) -> None:
        """Discard a single hunk by reversing the patch on the working tree."""
        proc = await asyncio.create_subprocess_exec(
            "git", "apply", "--reverse", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.root,  # type: ignore[attr-defined]
        )
        _stdout, stderr = await proc.communicate(hunk_patch.encode("utf-8"))
        if proc.returncode == 0:
            self.notify(f"Hunk descartado: {rel_path}", severity="information")  # type: ignore[attr-defined]
        else:
            err = stderr.decode("utf-8", errors="replace").strip()
            self.notify(f"Error descartando hunk: {err}", severity="error")  # type: ignore[attr-defined]

    def action_commit(self) -> None:
        """Open the commit modal if there are staged changes."""
        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=self.root,  # type: ignore[attr-defined]
                capture_output=True,
            )
        except Exception as exc:
            self.notify(f"Error validando staged: {exc}", severity="error")  # type: ignore[attr-defined]
            return
        if result.returncode == 0:
            self.notify("No hay cambios staged para commit", severity="warning")  # type: ignore[attr-defined]
            return
        self.push_screen(
            CommitScreen(self.root),  # type: ignore[attr-defined]
            self._on_commit_message,
        )

    def _on_commit_message(self, message: str | None) -> None:
        """Run the actual commit once the modal returns a message."""
        if not message:
            return
        self.run_worker(self._git_commit_worker(message), exclusive=True)  # type: ignore[attr-defined]

    async def _git_commit_worker(self, message: str) -> None:
        """Create a commit with the given message."""
        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", message,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.root,  # type: ignore[attr-defined]
        )
        _stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            self.notify("Commit creado", severity="success")  # type: ignore[attr-defined]
        else:
            err = stderr.decode("utf-8", errors="replace").strip()
            self.notify(f"Error commit: {err}", severity="error")  # type: ignore[attr-defined]

    def _on_log_commit_selected(self, commit_hash: str | None) -> None:
        """Open the commit detail screen for the selected log entry."""
        if not commit_hash:
            return
        self.push_screen(
            GitCommitScreen(self.root, commit_hash),  # type: ignore[attr-defined]
            self._on_commit_checkout,
        )

    def _on_commit_checkout(self, result: tuple[str, str] | None) -> None:
        """Checkout the commit chosen in the detail screen."""
        if not result or result[0] != "checkout":
            return
        commit_hash = result[1]
        self.run_worker(  # type: ignore[attr-defined]
            self._git_checkout_commit_worker(commit_hash),
            exclusive=True,
        )

    async def _git_checkout_commit_worker(self, commit_hash: str) -> None:
        """Run git checkout for the given commit hash."""
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", commit_hash,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.root,  # type: ignore[attr-defined]
        )
        _stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            self.notify(f"Checkout a {commit_hash[:8]}", severity="information")  # type: ignore[attr-defined]
        else:
            err = stderr.decode("utf-8", errors="replace").strip()
            self.notify(f"Error checkout: {err}", severity="error")  # type: ignore[attr-defined]

    # ── Editor info bar worker ──────────────────────────────────────

    async def _git_info_worker(self) -> None:
        """Fetch git status and inline blame for the current file/line.

        Driven by EditorMixin on tab refresh / open-file. Writes the
        composed "<rel>[ <status>][ ●] | Runa: <author> · <date>" string into
        the editor info bar via `self._update_editor_info_text` (defined in
        EditorMixin; resolves via MRO).
        """
        path = self.current_file  # type: ignore[attr-defined]
        if not path:
            return
        rel = _shorten_path(path, self.root)  # type: ignore[attr-defined]
        try:
            status = await self._git_status_for_file(rel)
            status_str = f" [{status}]" if status else ""
        except Exception:
            status_str = ""

        editor = self._current_editor()  # type: ignore[attr-defined]
        line = 1
        if editor:
            try:
                line = editor.cursor_location[0] + 1
            except Exception:
                pass

        try:
            blame = await self._git_blame_for_line(rel, line)
        except Exception:
            blame = ""

        tab_id = self._current_tab_id()  # type: ignore[attr-defined]
        modified = " ●" if tab_id in self._modified else ""  # type: ignore[attr-defined]
        parts = [f"{rel}{modified}{status_str}"]
        if blame:
            parts.append(f"Runa: {blame}")
        self._update_editor_info_text("  |  ".join(parts))  # type: ignore[attr-defined]

    async def _git_status_for_file(self, rel_path: str) -> str:
        """Return a one-letter git status for *rel_path* (M/A/D/?)."""
        proc = await asyncio.create_subprocess_shell(
            f'git status --porcelain -- "{rel_path}"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.root,  # type: ignore[attr-defined]
        )
        stdout, _ = await proc.communicate()
        line = stdout.decode("utf-8", errors="replace").strip()
        if not line:
            return ""
        # XY format: first char is index, second is working tree.
        return line.split()[0][-1] if len(line.split()[0]) >= 1 else "?"

    async def _git_blame_for_line(self, rel_path: str, line: int) -> str:
        """Return a short blame string for *line* of *rel_path*."""
        proc = await asyncio.create_subprocess_shell(
            f'git blame -L {line},{line} --date=short -- "{rel_path}"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.root,  # type: ignore[attr-defined]
        )
        stdout, _ = await proc.communicate()
        text = stdout.decode("utf-8", errors="replace").strip()
        if not text:
            return ""
        # Typical output: abc1234 (Author 2024-01-01 12:00:00 +0000 1) code
        parts = text.split("(", 1)
        if len(parts) < 2:
            return ""
        meta = parts[1].split(")", 1)[0]
        # meta ~ "Author 2024-01-01 12:00:00 +0000"
        tokens = meta.split()
        if len(tokens) >= 2:
            author = " ".join(tokens[:-2])
            date = tokens[-2]
            return f"{author} · {date}"
        return meta

    # ── /git slash-command workers ──────────────────────────────────

    async def _git_changed_lines_worker(self) -> None:
        """Show which lines of the current file are modified/added/deleted."""
        path = self.current_file  # type: ignore[attr-defined]
        if not path:
            self._chat_system("[red]No hay archivo abierto.[/]")  # type: ignore[attr-defined]
            return
        rel = _shorten_path(path, self.root)  # type: ignore[attr-defined]
        try:
            proc = await asyncio.create_subprocess_shell(
                f'git diff --unified=0 -- "{rel}"',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,  # type: ignore[attr-defined]
            )
            stdout, _ = await proc.communicate()
            text = stdout.decode("utf-8", errors="replace").strip()
            if not text:
                self._chat_system(f"[dim]{rel} no tiene cambios.[/]")  # type: ignore[attr-defined]
                return
            lines: list[str] = [f"[bold cyan]Cambios en {rel}:[/]"]
            for line in text.splitlines():
                if line.startswith("@@"):
                    lines.append(f"[dim]{line}[/]")
                elif line.startswith("+") and not line.startswith("+++"):
                    lines.append(f"[green]{line[:120]}[/]")
                elif line.startswith("-") and not line.startswith("---"):
                    lines.append(f"[red]{line[:120]}[/]")
            self._chat_system("\n".join(lines))  # type: ignore[attr-defined]
        except Exception as exc:
            self._chat_system(f"[red]Error git-lines:[/] {exc}")  # type: ignore[attr-defined]

    async def _git_diff_summary_worker(self) -> None:
        """Fetch a short git diff summary and print it to the chat."""
        try:
            proc = await asyncio.create_subprocess_shell(
                "git diff --stat",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.root,  # type: ignore[attr-defined]
            )
            stdout, _ = await proc.communicate()
            text = stdout.decode("utf-8", errors="replace").strip()
            self._chat_system(  # type: ignore[attr-defined]
                f"[bold]Git diff summary:[/]\n{text or '[sin cambios]'}",
            )
        except Exception as exc:
            self._chat_system(f"[red]Error git diff:[/] {exc}")  # type: ignore[attr-defined]
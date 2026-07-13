"""AgentMixin — chat panel, agent worker, slash commands, plan/execute, runestones glue.

Owns the chat surface area. The agent pipe (`session.process_message_stream`)
lives in the host project (lilith-agent); here we glue Textual's chat log widget
to it, dispatch slash commands, drive plan/execute loops, and forge Runestones
from assistant responses.

State initialised in LilithIDEApp.__init__:
    _chat_history:            list[str]
    _chat_history_index:      int
    _current_plan:            AgentPlan
    _token_usage:             dict[str, int]   (prompt/completion/total)
    _thinking:                bool
    _thinking_frame:          int
    _thinking_worker_task:    asyncio.Task
    _active_worker:           Textual Worker
    _snippets:                dict[str, str]

Cross-domain calls (resolved via the composed LilithIDEApp instance):
    self._chat_user / _chat_assistant_chunk / _chat_tool_call / _chat_tool_result
        (also defined on App, but the call is forwarded)
    self._refresh_current_editor / _refresh_editor_tab   → EditorMixin
    self._chat_system                                     → App cross-domain
    self._save_session                                    → App cross-domain
    self._update_status / _status_right                   → App cross-domain
    self._open_file                                       → EditorMixin
    self._shell_worker                                    → TerminalMixin (when extracted)
    self.runestone_forge / _list_runestones               → App cross-domain
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from textual.widgets import Input, RichLog

from ..config import CONFIG_DIR
from ..utils.helpers import (
    ProposedChange,
    _apply_patch,
    _backup_path,
    _build_proposed_changes,
    _central_backup_path,
    _register_undo,
    _shorten_path,
    _undo_last,
)
from ..screens.modals import (
    AgentDiffScreen,
    CommitScreen,
    FileSearchScreen,
    GitBlameScreen,
    PatchScreen,
    RunestoneScreen,
)
from ..plan import (
    AgentPlan,
    build_execution_prompt,
    build_planning_prompt,
    parse_plan,
)


class AgentMixin:
    """Chat panel, agent worker, slash commands, plan/execute, runestones glue."""

    # ── Slash command dispatcher ──────────────────────────────────

    def _send_message(self) -> None:
        input_widget = self.query_one("#chat-input", Input)  # type: ignore[attr-defined]
        text = input_widget.value.strip()
        if not text:
            return
        input_widget.value = ""
        self._chat_history_index = -1
        if not self._chat_history or self._chat_history[-1] != text:
            self._chat_history.append(text)
            if len(self._chat_history) > 100:
                self._chat_history = self._chat_history[-100:]

        if text.startswith("/"):
            self._handle_slash(text)
            return

        self._chat_user(text)
        self._active_worker = self.run_worker(self._agent_worker(text), exclusive=True)  # type: ignore[attr-defined]

    def _handle_slash(self, text: str) -> None:
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/run":
            if not arg:
                self._chat_system("Uso: /run <comando>")  # type: ignore[attr-defined]
                return
            self._chat_user(f"/run {arg}")
            self._active_worker = self.run_worker(self._shell_worker(arg), exclusive=True)  # type: ignore[attr-defined]
        elif cmd == "/patch":
            self.push_screen(PatchScreen(arg or "Pegá el diff acá…"), self._on_patch_applied)  # type: ignore[attr-defined]
        elif cmd == "/clear":
            self.query_one("#chat-log", RichLog).clear()  # type: ignore[attr-defined]
        elif cmd == "/context":
            self._show_context(arg)
        elif cmd == "/help":
            self.action_show_help()  # type: ignore[attr-defined]
        elif cmd == "/theme":
            self.action_toggle_theme()  # type: ignore[attr-defined]
        elif cmd == "/save":
            self.action_save_file()  # type: ignore[attr-defined]
        elif cmd == "/history":
            self.action_open_history()  # type: ignore[attr-defined]
        elif cmd == "/test":
            self._chat_user("/test" if not arg else f"/test {arg}")
            self._active_worker = self.run_worker(self._test_worker(arg or ""), exclusive=True)  # type: ignore[attr-defined]
        elif cmd == "/blame":
            self.push_screen(GitBlameScreen(self.root, self.current_file))  # type: ignore[attr-defined]
        elif cmd == "/export":
            self._export_conversation()
        elif cmd == "/git-stash":
            self._chat_user("/git-stash")
            self._active_worker = self.run_worker(self._shell_worker("git stash"), exclusive=True)  # type: ignore[attr-defined]
        elif cmd == "/git-checkout":
            if not arg:
                self._chat_system("Uso: /git-checkout <branch>")  # type: ignore[attr-defined]
                return
            self._chat_user(f"/git-checkout {arg}")
            self._active_worker = self.run_worker(self._shell_worker(f"git checkout {arg}"), exclusive=True)  # type: ignore[attr-defined]
        elif cmd == "/git-branch":
            if not arg:
                self._chat_system("Uso: /git-branch <nombre>")  # type: ignore[attr-defined]
                return
            self._chat_user(f"/git-branch {arg}")
            self._active_worker = self.run_worker(self._shell_worker(f"git branch {arg}"), exclusive=True)  # type: ignore[attr-defined]
        elif cmd == "/git-commit":
            self.push_screen(
                CommitScreen(self.root, message=arg or ""),  # type: ignore[attr-defined]
                self._on_commit_message,  # type: ignore[attr-defined]
            )
        elif cmd == "/undo-last":
            self._chat_user("/undo-last")
            self._active_worker = self.run_worker(self._undo_worker(), exclusive=True)  # type: ignore[attr-defined]
        elif cmd == "/new":
            self._handle_new_command(arg)
        elif cmd == "/preview":
            if not arg:
                self._chat_system("Uso: /preview <id-runestone>")  # type: ignore[attr-defined]
                return
            self._preview_runestone(arg.strip())
        elif cmd == "/runestone":
            self._list_runestones()
        elif cmd == "/plan":
            if not arg:
                self._chat_system("Uso: /plan <objetivo>")  # type: ignore[attr-defined]
                return
            self._chat_user(f"/plan {arg}")
            self._active_worker = self.run_worker(self._plan_worker(arg), exclusive=True)  # type: ignore[attr-defined]
        elif cmd == "/execute":
            self._chat_user("/execute")
            self._active_worker = self.run_worker(self._execute_plan_worker(), exclusive=True)  # type: ignore[attr-defined]
        elif cmd == "/review":
            self._review_plan()
        elif cmd == "/git-lines":
            self._active_worker = self.run_worker(self._git_changed_lines_worker(), exclusive=False)  # type: ignore[attr-defined]
        elif cmd == "/realm":
            self._show_realm()
        elif cmd == "/remember":
            if not arg:
                self._chat_system("Uso: /remember <texto>")  # type: ignore[attr-defined]
                return
            self._remember(arg)
        elif cmd == "/forget":
            if not arg:
                self._chat_system("Uso: /forget <texto o número>")  # type: ignore[attr-defined]
                return
            self._forget(arg)
        elif cmd == "/knowledge":
            self._show_knowledge()
        elif cmd == "/standard":
            if not arg:
                self._chat_system("Uso: /standard <texto>")  # type: ignore[attr-defined]
                return
            self._add_standard(arg)
        elif cmd == "/lsp":
            self._show_lsp_status()
        elif cmd == "/debug":
            self.action_debug_current_file()  # type: ignore[attr-defined]
        elif cmd == "/hover":
            self.action_show_hover()  # type: ignore[attr-defined]
        elif cmd == "/definition":
            self.action_go_to_definition()  # type: ignore[attr-defined]
        elif cmd == "/diagnostics":
            self.action_show_diagnostics()  # type: ignore[attr-defined]
        elif cmd == "/plugins":
            self._show_plugins()
        elif cmd == "/delegate":
            if not arg:
                self._chat_system("Uso: /delegate <preset>")  # type: ignore[attr-defined]
                return
            self._chat_user(f"/delegate {arg}")
            self._delegate_to_preset(arg.strip())  # type: ignore[attr-defined]
        else:
            self._chat_system(f"Comando desconocido: {cmd}")  # type: ignore[attr-defined]

    def action_cancel_generation(self) -> None:
        if self._active_worker and not self._active_worker.is_cancelled:  # type: ignore[attr-defined]
            self._active_worker.cancel()
            self.notify("Generación cancelada", severity="warning")  # type: ignore[attr-defined]

    # ── Slash-command target methods ──────────────────────────────

    def _handle_new_command(self, arg: str) -> None:
        """Handle /new <template> <relative-path>: create a file from a snippet."""
        parts = arg.split(None, 1)
        if len(parts) != 2:
            self._chat_system("Uso: /new <template> <ruta>\nTemplates: py, test, class, md")
            return
        template, rel_path = parts
        template = template.lower()
        if template not in self._snippets:  # type: ignore[attr-defined]
            self._chat_system(f"Template desconocido: {template}. Usá: py, test, class, md")
            return
        target = self.root / rel_path  # type: ignore[attr-defined]
        if target.exists():
            self._chat_system(f"El archivo ya existe: {rel_path}")
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            snippet = self._snippets[template]  # type: ignore[attr-defined]
            if template == "class":
                # Insert the class name derived from the file stem.
                class_name = "".join(p.capitalize() for p in target.stem.replace("_", "-").split("-") if p)
                snippet = snippet.replace("class :", f"class {class_name}:")
            target.write_text(snippet, encoding="utf-8")
            self._chat_system(f"[green]Creado:[/] {rel_path}")
            self._open_file(target)  # type: ignore[attr-defined]
        except Exception as exc:
            self._chat_system(f"[red]Error creando archivo:[/] {exc}")

    def _show_context(self, arg: str = "") -> None:
        """Show current context selectors available to the agent."""
        log = self.query_one("#chat-log", RichLog)  # type: ignore[attr-defined]
        lines = [
            "[bold cyan]Contexto disponible (@-mentions):[/]",
            "  @file:<ruta>        — contenido de un archivo (o @file para el actual)",
            "  @selection          — texto seleccionado en el editor",
            "  @folder:<ruta>      — listado recursivo de una carpeta",
            "  @project            — árbol del proyecto + README",
            "  @git-diff           — diff del working tree",
            "  @terminal-output    — última salida de la terminal",
            "",
        ]
        current = self.current_file  # type: ignore[attr-defined]
        if current:
            rel = _shorten_path(current, self.root)  # type: ignore[attr-defined]
            lines.append(f"[dim]Archivo actual:[/] {rel}")
            selection = self._get_editor_selection()  # type: ignore[attr-defined]
            if selection:
                preview = selection[:200].replace("\n", " ")
                if len(selection) > 200:
                    preview += "…"
                lines.append(f"[dim]Selección:[/] {preview}")
        else:
            lines.append("[dim]No hay archivo abierto.[/]")
        log.write("\n" + "\n".join(lines))

    def _show_realm(self) -> None:
        """Show the current project Realm and persist it."""
        realm = self.realm_manager.load()  # type: ignore[attr-defined]
        self.realm_manager.save()  # type: ignore[attr-defined]
        log = self.query_one("#chat-log", RichLog)  # type: ignore[attr-defined]
        lines = [
            f"[bold cyan]Realm:[/] {realm.name}",
            f"[dim]Raíz:[/] {self.root}",  # type: ignore[attr-defined]
            f"[dim]Archivo:[/] {self.realm_manager.realm_path()}",  # type: ignore[attr-defined]
        ]
        if realm.important_files:
            lines.append("[dim]Archivos importantes:[/]")
            for f in realm.important_files:
                lines.append(f"  • {f}")
        if realm.standards:
            lines.append("[dim]Estándares:[/]")
            for i, s in enumerate(realm.standards, start=1):
                lines.append(f"  {i}. {s}")
        if realm.memories:
            lines.append("[dim]Memorias:[/]")
            for i, m in enumerate(realm.memories, start=1):
                lines.append(f"  {i}. {m}")
        log.write("\n" + "\n".join(lines))

    def _remember(self, text: str) -> None:
        """Remember a fact about the project."""
        realm = self.realm_manager.load()  # type: ignore[attr-defined]
        realm.remember(text)
        self.realm_manager.save()  # type: ignore[attr-defined]
        self._chat_system(f"[green]Recordado:[/] {text}")

    def _forget(self, text: str) -> None:
        """Forget a previously remembered fact."""
        realm = self.realm_manager.load()  # type: ignore[attr-defined]
        if realm.forget(text):
            self.realm_manager.save()  # type: ignore[attr-defined]
            self._chat_system("[green]Memoria olvidada.[/]")
        else:
            self._chat_system("[red]No se encontró esa memoria.[/]")

    def _show_knowledge(self) -> None:
        """Show the knowledge prompt that Lilith will receive."""
        prompt = self.realm_manager.build_knowledge_prompt()  # type: ignore[attr-defined]
        log = self.query_one("#chat-log", RichLog)  # type: ignore[attr-defined]
        if not prompt:
            log.write("\n[dim]El Realm aún no tiene conocimiento. Usá /remember o /standard.[/]")
            return
        log.write("\n[bold cyan]Conocimiento que se adjunta a cada mensaje:[/]\n" + prompt)

    def _add_standard(self, text: str) -> None:
        """Add a coding standard to the Realm."""
        realm = self.realm_manager.load()  # type: ignore[attr-defined]
        realm.add_standard(text)
        self.realm_manager.save()  # type: ignore[attr-defined]
        self._chat_system(f"[green]Estándar guardado:[/] {text}")

    def _show_lsp_status(self) -> None:
        """Show which language servers are active."""
        status = self.lsp_manager.status()  # type: ignore[attr-defined]
        log = self.query_one("#chat-log", RichLog)  # type: ignore[attr-defined]
        if not status:
            log.write("\n[dim]Ningún servidor LSP activo todavía. Abrí un archivo soportado.[/]")
            return
        lines = ["[bold cyan]Servidores LSP:[/]"]
        for language, state in status.items():
            lines.append(f"  • {language}: {state}")
        log.write("\n" + "\n".join(lines))

    def _show_plugins(self) -> None:
        """List loaded plugins and the plugin directory path."""
        log = self.query_one("#chat-log", RichLog)  # type: ignore[attr-defined]
        plugins = self.plugin_manager.list()  # type: ignore[attr-defined]
        lines = [
            f"[bold cyan]Plugins:[/] ({self.plugin_manager.plugin_dir()})",  # type: ignore[attr-defined]
        ]
        if plugins:
            for plugin in plugins:
                lines.append(f"  • {plugin.name}")
        else:
            lines.append("  [dim]No hay plugins cargados.[/]")
        lines.append("\nColocá archivos .py en esa carpeta con una función ``register(app)``.")
        log.write("\n" + "\n".join(lines))

    # ── Conversation persistence ──────────────────────────────────

    def _export_conversation(self, filepath: Path | None = None) -> None:
        if not self.session.history:  # type: ignore[attr-defined]
            self.notify("No hay conversación para exportar", severity="warning")  # type: ignore[attr-defined]
            return
        from datetime import UTC, datetime  # local import to keep top-level clean

        target = filepath or (CONFIG_DIR / "conversations" / f"export_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Conversación con Lilith\n"]
        for msg in self.session.history:  # type: ignore[attr-defined]
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            lines.append(f"## {role}\n\n{content}\n")
        try:
            target.write_text("\n".join(lines), encoding="utf-8")
            self.notify(f"Exportada: {target.name}", severity="information")  # type: ignore[attr-defined]
        except Exception as exc:
            self.notify(f"Error exportando: {exc}", severity="error")  # type: ignore[attr-defined]

    def _auto_save_conversation(self) -> None:
        if not self.session.history:  # type: ignore[attr-defined]
            return
        from datetime import UTC, datetime

        conv_dir = CONFIG_DIR / "conversations"
        conv_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        path = conv_dir / f"conv_{timestamp}.json"
        try:
            import json as _json

            path.write_text(
                _json.dumps(
                    {
                        "timestamp": timestamp,
                        "model": self.session.config.model,  # type: ignore[attr-defined]
                        "provider": self.session.config.provider,  # type: ignore[attr-defined]
                        "messages": self.session.history,  # type: ignore[attr-defined]
                        "usage": self.session.total_usage,  # type: ignore[attr-defined]
                    },
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ── Thinking animation (status bar rune cycle) ───────────────

    async def _thinking_animation_worker(self) -> None:
        """Cycle rune symbols in the status bar while the agent is thinking."""
        from textual.worker import get_current_worker

        worker = get_current_worker()
        while not worker.is_cancelled and self._thinking:  # type: ignore[attr-defined]
            await asyncio.sleep(0.4)
            if not self._thinking or worker.is_cancelled:  # type: ignore[attr-defined]
                break
            self._thinking_frame += 1  # type: ignore[attr-defined]
            self._update_status()

    # ── Runestones: glue between /preview and RunestoneScreen ────

    def _list_runestones(self) -> None:
        """Show all Runestones forged in the current session."""
        log = self.query_one("#chat-log", RichLog)  # type: ignore[attr-defined]
        stones = self.runestone_forge.list()  # type: ignore[attr-defined]
        if not stones:
            log.write("\n[dim]No hay Runestones forjados todavía.[/]")
            return
        lines = ["[bold cyan]Runestones disponibles:[/]"]
        for stone in stones:
            lines.append(f"  [bold]{stone.id}[/] — {stone.title} ({stone.language})")
        lines.append("\nUsá [bold]/preview <id>[/] para ver uno.")
        log.write("\n" + "\n".join(lines))

    def _preview_runestone(self, rune_id: str) -> None:
        """Open the Runestone preview modal."""
        stone = self.runestone_forge.get(rune_id)  # type: ignore[attr-defined]
        if not stone:
            self._chat_system(f"[red]Runestone no encontrado:[/] {rune_id}")
            return
        self.push_screen(RunestoneScreen(stone), self._on_runestone_action)  # type: ignore[attr-defined]

    def _on_runestone_action(self, result: tuple[str, str] | None) -> None:
        """Handle apply/save/evolve actions from the Runestone modal."""
        if not result:
            return
        action, rune_id = result
        stone = self.runestone_forge.get(rune_id)  # type: ignore[attr-defined]
        if not stone:
            self._chat_system(f"[red]Runestone no encontrado:[/] {rune_id}")
            return

        if action == "apply":
            if not self.current_file:  # type: ignore[attr-defined]
                self._chat_system("[red]No hay archivo abierto para aplicar el Runestone.[/]")
                return
            try:
                backup = _backup_path(self.current_file)
                backup.write_text(self.current_file.read_text(encoding="utf-8"), encoding="utf-8")  # type: ignore[attr-defined]
                self.current_file.write_text(stone.content, encoding="utf-8")  # type: ignore[attr-defined]
                self._refresh_current_editor()  # type: ignore[attr-defined]
                self._chat_system(f"[green]Runestone {rune_id} aplicado a {_shorten_path(self.current_file, self.root)}[/]")  # type: ignore[attr-defined]
            except Exception as exc:
                self._chat_system(f"[red]Error aplicando Runestone:[/] {exc}")
        elif action == "save":
            self.push_screen(  # type: ignore[attr-defined]
                FileSearchScreen(self.root),  # type: ignore[attr-defined]
                lambda path: self._save_runestone_as(rune_id, path),
            )
        elif action == "evolve":
            prompt = f"Evolucioná el siguiente Runestone. Mejorá su calidad, claridad y robustez.\n\n```{stone.language}\n{stone.content}\n```"
            self._chat_user(f"/evolve {stone.title}")
            self._active_worker = self.run_worker(self._agent_worker(prompt), exclusive=True)  # type: ignore[attr-defined]

    def _save_runestone_as(self, rune_id: str, path: Path | None) -> None:
        """Save a Runestone to a newly selected file path."""
        if not path:
            return
        try:
            target = self.runestone_forge.apply(rune_id, path)  # type: ignore[attr-defined]
            self._chat_system(f"[green]Runestone guardado en {_shorten_path(target, self.root)}[/]")  # type: ignore[attr-defined]
            self._open_file(target)  # type: ignore[attr-defined]
        except Exception as exc:
            self._chat_system(f"[red]Error guardando Runestone:[/] {exc}")

    # ── Plan / execute / review ──────────────────────────────────

    async def _plan_worker(self, goal: str) -> None:
        """Ask Lilith to generate a numbered plan for *goal*."""
        from textual.worker import get_current_worker

        worker = get_current_worker()
        self._current_plan = AgentPlan(goal=goal)  # type: ignore[attr-defined]
        prompt = build_planning_prompt(goal)
        accumulated = ""

        try:
            async for event in self.session.process_message_stream(prompt):  # type: ignore[attr-defined]
                if worker.is_cancelled:
                    break
                etype = event.get("type", "")
                if etype == "text":
                    chunk = event.get("content", "")
                    if chunk:
                        accumulated += chunk
                        self._chat_assistant_chunk(chunk)  # type: ignore[attr-defined]
                elif etype == "done":
                    break
        except asyncio.CancelledError:
            self._chat_system("[dim]Planificación cancelada.[/]")
            return
        except Exception as exc:
            self._chat_system(f"[red]Error del oráculo:[/] {exc}")
            return

        self._finalize_plan(accumulated)

    def _finalize_plan(self, text: str) -> None:
        """Parse the agent response and store the resulting plan."""
        self._current_plan = parse_plan(text)  # type: ignore[attr-defined]
        log = self.query_one("#chat-log", RichLog)  # type: ignore[attr-defined]
        if not self._current_plan.steps:  # type: ignore[attr-defined]
            log.write("\n[dim]No se detectó un plan numerado en la respuesta.[/]")
            return
        log.write("\n[bold cyan]Plan forjado:[/]")
        for step in self._current_plan.steps:  # type: ignore[attr-defined]
            log.write(f"  {step.number}. {step.description}")
        log.write("\nUsá [bold]/execute[/] para ejecutarlo paso a paso.")

    async def _execute_plan_worker(self) -> None:
        """Execute the current plan step by step."""
        from textual.worker import get_current_worker

        worker = get_current_worker()
        if not self._current_plan.steps:  # type: ignore[attr-defined]
            self._chat_system("[red]No hay un plan activo. Usá /plan primero.[/]")
            return

        while not worker.is_cancelled:
            step = self._current_plan.next_pending()  # type: ignore[attr-defined]
            if not step:
                break
            self._chat_system(
                f"[bold cyan]▶ Paso {step.number}:[/] {step.description}",
            )
            prompt = build_execution_prompt(step, previous_steps=self._current_plan.steps)  # type: ignore[attr-defined]
            await self._agent_worker(prompt)
            step.done = True

        if self._current_plan.is_complete():  # type: ignore[attr-defined]
            self._chat_system("[bold green]✓ Plan completado.[/]")

    def _review_plan(self) -> None:
        """Show the status of the current plan and a git diff summary."""
        log = self.query_one("#chat-log", RichLog)  # type: ignore[attr-defined]
        lines: list[str] = []
        if self._current_plan.steps:  # type: ignore[attr-defined]
            lines.append("[bold cyan]Estado del plan:[/]")
            for step in self._current_plan.steps:  # type: ignore[attr-defined]
                mark = "[green]✓[/]" if step.done else "[dim]○[/]"
                lines.append(f"  {mark} {step.number}. {step.description}")
        else:
            lines.append("[dim]No hay un plan activo.[/]")
        log.write("\n" + "\n".join(lines))
        # Trigger a non-blocking git diff summary.
        self.run_worker(self._git_diff_summary_worker(), exclusive=False)  # type: ignore[attr-defined]

    # ── /test, /patch, /debug ────────────────────────────────────

    async def _test_worker(self, args: str) -> None:
        """Run pytest and stream the output to the chat log."""
        self.call_from_thread(self._chat_system, "[dim]Ejecutando tests…[/]")  # type: ignore[attr-defined]
        try:
            cmd = ["python", "-m", "pytest"] + (args.split() if args else ["-q"])
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.root,  # type: ignore[attr-defined]
            )
            output = ""
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip()
                output += decoded + "\n"
                self.call_from_thread(self._chat_system, f"[dim]{decoded}[/]")  # type: ignore[attr-defined]
            await proc.wait()
            summary = "[green]Tests passed[/]" if proc.returncode == 0 else f"[red]Tests failed (exit {proc.returncode})[/]"
            self.call_from_thread(self._chat_system, summary)  # type: ignore[attr-defined]
        except Exception as exc:
            self.call_from_thread(self._chat_system, f"[red]Error corriendo tests:[/] {exc}")  # type: ignore[attr-defined]

    def _on_patch_applied(self, diff_text: str | None) -> None:
        if not diff_text:
            self._chat_system("Parche cancelado.")
            return
        self._active_worker = self.run_worker(self._patch_worker(diff_text), exclusive=True)  # type: ignore[attr-defined]

    async def _patch_worker(self, diff_text: str) -> None:
        self.call_from_thread(self._chat_system, "Aplicando parche…")  # type: ignore[attr-defined]
        try:
            changed = await asyncio.get_event_loop().run_in_executor(
                None, _apply_patch, diff_text, self.root  # type: ignore[attr-defined]
            )
            self.call_from_thread(
                self._chat_system,
                f"[green]Parche aplicado:[/] {', '.join(changed)}",  # type: ignore[attr-defined]
            )
            # Refresh current file if it was changed.
            if self.current_file:  # type: ignore[attr-defined]
                self.call_from_thread(self._refresh_current_editor)  # type: ignore[attr-defined]
        except Exception as exc:
            self.call_from_thread(
                self._chat_system,
                f"[red]Error aplicando parche:[/] {exc}",  # type: ignore[attr-defined]
            )

    # ── Agent worker (the big one) ───────────────────────────────

    async def _build_prompt(self, text: str) -> str:
        """Resolve @-mentions and prepend realm knowledge + context to the user message."""
        items = await self.context_manager.resolve_all(  # type: ignore[attr-defined]
            text,
            current_file=self.current_file,  # type: ignore[attr-defined]
            get_selection=self._get_editor_selection,  # type: ignore[attr-defined]
        )
        cleaned = self.context_manager.strip_mentions(text)  # type: ignore[attr-defined]
        sections: list[str] = []

        realm_knowledge = self.realm_manager.build_knowledge_prompt()  # type: ignore[attr-defined]
        if realm_knowledge:
            sections.append(realm_knowledge)

        if items:
            blocks = "\n\n".join(str(item) for item in items)
            sections.append(f"[Contexto adjunto]\n{blocks}\n\n[/Contexto]")

        if not sections:
            return cleaned
        return "\n\n".join(sections) + "\n\n" + cleaned

    async def _agent_worker(self, text: str) -> None:
        from textual.worker import get_current_worker

        worker = get_current_worker()
        accumulated = ""
        usage: dict[str, int] = {}
        self._thinking = True  # type: ignore[attr-defined]
        self._thinking_frame = 0  # type: ignore[attr-defined]
        self._thinking_worker_task = self.run_worker(  # type: ignore[attr-defined]
            self._thinking_animation_worker(),
            exclusive=False,
        )
        self._update_status()

        try:
            prompt = await self._build_prompt(text)
            async for event in self.session.process_message_stream(prompt):  # type: ignore[attr-defined]
                if worker.is_cancelled:
                    break

                etype = event.get("type", "")
                if etype == "text":
                    chunk = event.get("content", "")
                    if chunk:
                        accumulated += chunk
                        self.call_from_thread(self._chat_assistant_chunk, chunk)  # type: ignore[attr-defined]
                elif etype == "reasoning":
                    chunk = event.get("content", "")
                    if chunk:
                        self.call_from_thread(
                            self._chat_system,
                            f"[dim magenta]💭 {chunk}[/]",  # type: ignore[attr-defined]
                        )
                elif etype == "tool_call":
                    name = event.get("name", "tool")
                    args = event.get("arguments", {})
                    self.call_from_thread(self._chat_tool_call, name, args)  # type: ignore[attr-defined]
                elif etype == "tool_result":
                    name = event.get("name", "tool")
                    content = event.get("content", "")
                    self.call_from_thread(self._chat_tool_result, name, content)  # type: ignore[attr-defined]
                elif etype == "done":
                    usage = event.get("usage", {})
                    break

        except asyncio.CancelledError:
            self._thinking = False  # type: ignore[attr-defined]
            self._update_status()
            self.call_from_thread(self._chat_system, "[dim]Generación cancelada.[/]")  # type: ignore[attr-defined]
            return
        except Exception as exc:
            self._thinking = False  # type: ignore[attr-defined]
            self._update_status()
            self.call_from_thread(
                self._chat_system,
                f"[red]Error del oráculo:[/] {exc}",  # type: ignore[attr-defined]
            )
            return

        self._thinking = False  # type: ignore[attr-defined]
        self._update_status()
        self.call_from_thread(self._finalize_turn, usage, accumulated)  # type: ignore[attr-defined]

    def _finalize_turn(self, usage: dict[str, int], text: str = "") -> None:
        log = self.query_one("#chat-log", RichLog)  # type: ignore[attr-defined]
        log.write("\n")
        total = self.session.total_usage  # type: ignore[attr-defined]
        self._token_usage = {  # type: ignore[attr-defined]
            "prompt": total.get("prompt_tokens", 0),
            "completion": total.get("completion_tokens", 0),
            "total": total.get("total_tokens", 0),
        }

        # Intercept agent-proposed file changes before applying them.
        if text:
            proposed_changes = _build_proposed_changes(self.root, text)  # type: ignore[attr-defined]
            if proposed_changes:
                self.push_screen(
                    AgentDiffScreen(proposed_changes, self.root),  # type: ignore[attr-defined]
                    self._on_agent_diff_action,
                )
                self._update_status()
                self._auto_save_conversation()
                if self.current_file:  # type: ignore[attr-defined]
                    self._refresh_current_editor()  # type: ignore[attr-defined]
                return

        # Forge Runestones from any code fences in the assistant response.
        if text:
            stones = self.runestone_forge.forge(text, source="agent")  # type: ignore[attr-defined]
            if stones:
                for stone in stones:
                    log.write(
                        f"[bold cyan]🜚 Runestone forjado:[/] {stone.title} — "
                        f"usá [bold]/preview {stone.id}[/] para verlo"
                    )
        self._update_status()
        self._auto_save_conversation()
        if self.current_file:  # type: ignore[attr-defined]
            self._refresh_current_editor()  # type: ignore[attr-defined]

    def _on_agent_diff_action(self, accepted: list[ProposedChange] | None) -> None:
        """Apply accepted proposed changes, creating local and centralized backups."""
        if not accepted:
            self._chat_system("[dim]Cambios del agente rechazados.[/]")  # type: ignore[attr-defined]
            return

        from datetime import UTC, datetime

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backups: list[dict[str, Any]] = []
        for change in accepted:
            target = change.path
            original = target.read_text(encoding="utf-8") if target.exists() else ""

            # Local backup next to the file.
            local_backup = _backup_path(target)
            local_backup.write_text(original, encoding="utf-8")

            # Centralized backup for undo.
            central_backup = _central_backup_path(change.rel_path, timestamp)
            central_backup.parent.mkdir(parents=True, exist_ok=True)
            central_backup.write_text(original, encoding="utf-8")

            # Apply the change.
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.proposed, encoding="utf-8")

            backups.append(
                {
                    "rel_path": change.rel_path,
                    "path": str(target),
                    "local_backup": str(local_backup),
                    "central_backup": str(central_backup),
                }
            )
            self._refresh_editor_for_path(target)

        if backups:
            undo_path = _register_undo(backups)
            rels = ", ".join(b["rel_path"] for b in backups)
            self._chat_system(
                f"[green]Aplicados {len(accepted)} cambios:[/] {rels} — undo: {undo_path.name}"  # type: ignore[attr-defined]
            )

    def _refresh_editor_for_path(self, path: Path) -> None:
        """Refresh any open editor tab that is displaying *path*."""
        for tab_id, tab_path in self._open_files.items():  # type: ignore[attr-defined]
            if tab_path == path:
                self._refresh_editor_tab(tab_id, path)  # type: ignore[attr-defined]

    async def _undo_worker(self) -> None:
        """Revert the most recent agent application."""
        self._chat_system("[dim]Deshaciendo últimos cambios…[/]")  # type: ignore[attr-defined]
        try:
            restored = await asyncio.get_event_loop().run_in_executor(None, _undo_last)
            if restored is None:
                self._chat_system(
                    "[yellow]No hay cambios del agente para deshacer.[/]",  # type: ignore[attr-defined]
                )
                return
            for rel_path in restored:
                target = self.root / rel_path  # type: ignore[attr-defined]
                self._refresh_editor_for_path(target)
            self._chat_system(
                f"[green]Desh echo:[/] {', '.join(restored)}",  # type: ignore[attr-defined]
            )
        except Exception as exc:
            self._chat_system(
                f"[red]Error deshaciendo cambios:[/] {exc}",  # type: ignore[attr-defined]
            )

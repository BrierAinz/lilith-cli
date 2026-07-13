"""YggdrasilPanelMixin — IDE orchestration panel (plan-29 item 7).

This mixin adds a modal orchestration dashboard to the Lilith IDE.  It is
kept separate from :class:`~lilith_cli.ide.app.LilithIDEApp` so the app
core stays small; composition happens via the MRO.

Public surface consumed by the app / screens:

- ``action_open_yggdrasil_panel`` — open the dashboard.
- ``_delegate_to_preset(preset)`` — enqueue the last user message targeted
  at a Hlidskjalf preset.
- ``_yggdrasil_cancel_queue_message(msg_id)`` — cancel (claim + ack) a
  queued task.
- ``_yggdrasil_kill_spawn(agent_name)`` — request cancellation of a spawn.
- ``_load_subagent_presets`` — cached wrapper around
  :func:`lilith_cli.main._load_subagent_presets`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ... import ops_queue, ops_spawn
from ..screens.modals import YggdrasilPanelScreen


class YggdrasilPanelMixin:
    """Orchestration dashboard for the Yggdrasil operator console."""

    # ── Entry point ──────────────────────────────────────────────────────

    def action_open_yggdrasil_panel(self) -> None:
        """Push the Yggdrasil orchestration modal."""
        self.push_screen(YggdrasilPanelScreen())  # type: ignore[attr-defined]

    # ── Data sources ─────────────────────────────────────────────────────

    def _yggdrasil_bus_db_path(self) -> Path:
        """Return the bus DB path rooted at the IDE workspace."""
        return self.root / ".ygg" / "lilith_bus.db"  # type: ignore[attr-defined]

    def _load_subagent_presets(self) -> dict[str, Any]:
        """Load Hlidskjalf subagent presets from the canonical YAML file."""
        from lilith_cli.main import _load_subagent_presets as _load

        return _load()

    def _yggdrasil_queue_messages(self) -> list[dict[str, Any]]:
        """Return pending ``queue.**`` messages as plain dicts."""
        db = self._yggdrasil_bus_db_path()
        if not db.exists():
            return []

        from lilith_core.bus import LilithBus

        bus = LilithBus(db)
        try:
            return [
                {
                    "id": m.id,
                    "topic": m.topic,
                    "role": m.role,
                    "task": m.payload.get("task", "") if isinstance(m.payload, dict) else "",
                    "agent": m.payload.get("agent") if isinstance(m.payload, dict) else None,
                    "claimed_by": m.claimed_by,
                    "published_at": m.published_at,
                }
                for m in bus.poll(ops_queue.QUEUE_PATTERN, limit=50)
            ]
        finally:
            bus.close()

    def _yggdrasil_active_spawns(self) -> list[dict[str, Any]]:
        """Return active subagent spawns from the bus / spawn status."""
        db = self._yggdrasil_bus_db_path()
        try:
            return ops_spawn.run_spawn_status(db=db, repo_root=self.root)  # type: ignore[attr-defined]
        except Exception:
            return []

    # ── Actions ──────────────────────────────────────────────────────────

    def _last_user_message(self) -> str | None:
        """Return the most recent user message in the chat history."""
        history = self.session.history  # type: ignore[attr-defined]
        for msg in reversed(history):
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    return content.strip()
        # Fallback to the chat input box if the history is empty.
        chat_input = self.query_one("#chat-input")  # type: ignore[attr-defined]
        if chat_input and chat_input.value.strip():  # type: ignore[attr-defined]
            return chat_input.value.strip()
        return None

    def _delegate_to_preset(self, preset_name: str) -> None:
        """Enqueue the last user message as a task targeted at *preset_name*."""
        last = self._last_user_message()
        if not last:
            self.notify(  # type: ignore[attr-defined]
                "No hay mensaje de usuario para delegar",
                severity="warning",
            )
            return

        presets = self._load_subagent_presets()
        if preset_name not in presets:
            self.notify(  # type: ignore[attr-defined]
                f"Preset '{preset_name}' no encontrado",
                severity="error",
            )
            return

        db = self._yggdrasil_bus_db_path()
        code = ops_queue.run_queue_add(
            task=last,
            agent=preset_name,
            queued_by="yggdrasil-panel",
            db=db,
            repo_root=self.root,  # type: ignore[attr-defined]
        )
        if code == 0:
            self.notify(  # type: ignore[attr-defined]
                f"Delegado a {preset_name}: {last[:60]}{'…' if len(last) > 60 else ''}",
                severity="information",
            )
        else:
            self.notify(  # type: ignore[attr-defined]
                f"Error encolando tarea para {preset_name}",
                severity="error",
            )

    def _yggdrasil_cancel_queue_message(self, msg_id: int) -> bool:
        """Cancel a queued message by claiming + acking it."""
        db = self._yggdrasil_bus_db_path()
        return ops_queue.run_queue_cancel(
            msg_id=msg_id,
            claimer="yggdrasil-panel",
            db=db,
            repo_root=self.root,  # type: ignore[attr-defined]
        ) == 0

    def _yggdrasil_kill_spawn(self, agent_name: str) -> bool:
        """Request cancellation of an active spawn."""
        code = ops_spawn.run_spawn_kill(agent_name)
        return code == 0

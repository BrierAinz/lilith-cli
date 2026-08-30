"""Session-owned usage and activity telemetry.

The conversation runtime should orchestrate messages, not own five unrelated
mutable telemetry collections. ``SessionTelemetry`` provides one explicit
boundary for token usage, model costs, tool calls, slash commands and file
edits. A small legacy adapter keeps lightweight embedders and test doubles
working while consumers migrate to the public API.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol, cast


_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")
_HISTORY_KINDS = ("tools", "commands", "files")


def _empty_usage() -> dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class TelemetryRuntime(Protocol):
    """Capabilities shared by native and legacy telemetry implementations."""

    @property
    def total_usage(self) -> dict[str, int]: ...

    @property
    def per_model_usage(self) -> dict[str, dict[str, Any]]: ...

    @property
    def tool_calls(self) -> list[dict[str, Any]]: ...

    @property
    def commands(self) -> list[dict[str, Any]]: ...

    @property
    def file_edits(self) -> list[dict[str, Any]]: ...

    def status(self) -> dict[str, bool]: ...

    def merge_usage(
        self,
        total_usage: dict[str, Any] | None = None,
        per_model_usage: dict[str, dict[str, Any]] | None = None,
    ) -> None: ...

    def record_tool_call(self, entry: dict[str, Any]) -> None: ...

    def record_command(self, entry: dict[str, Any]) -> None: ...

    def record_file_edit(self, entry: dict[str, Any]) -> None: ...

    def replace_file_edits(self, entries: list[dict[str, Any]]) -> None: ...

    def snapshot(self) -> dict[str, Any]: ...

    def restore(self, payload: dict[str, Any]) -> None: ...


class SessionTelemetry:
    """Mutable telemetry state with copy-on-read public views."""

    def __init__(self) -> None:
        self._total_usage: dict[str, int] = _empty_usage()
        self._per_model_usage: dict[str, dict[str, Any]] = {}
        self._tool_calls: list[dict[str, Any]] = []
        self._commands: list[dict[str, Any]] = []
        self._file_edits: list[dict[str, Any]] = []
        self._enabled_history: set[str] = set(_HISTORY_KINDS)

    @property
    def total_usage(self) -> dict[str, int]:
        """Return aggregate token usage without exposing mutable state."""
        return dict(self._total_usage)

    @property
    def per_model_usage(self) -> dict[str, dict[str, Any]]:
        """Return per-model token and cost totals."""
        return {model: dict(stats) for model, stats in self._per_model_usage.items()}

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return deepcopy(self._tool_calls)

    @property
    def commands(self) -> list[dict[str, Any]]:
        return deepcopy(self._commands)

    @property
    def file_edits(self) -> list[dict[str, Any]]:
        return deepcopy(self._file_edits)

    def status(self) -> dict[str, bool]:
        """Report which activity streams are enabled."""
        return {kind: kind in self._enabled_history for kind in _HISTORY_KINDS}

    def enable_history(self, kind: str) -> None:
        if kind not in _HISTORY_KINDS:
            raise ValueError(f"Unknown telemetry history kind: {kind}")
        self._enabled_history.add(kind)

    def disable_history(self, kind: str) -> None:
        if kind not in _HISTORY_KINDS:
            raise ValueError(f"Unknown telemetry history kind: {kind}")
        self._enabled_history.discard(kind)

    def ensure_model(self, model: str) -> dict[str, Any]:
        """Return the mutable accumulator for *model*, creating it if needed."""
        if model not in self._per_model_usage:
            self._per_model_usage[model] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost": 0.0,
            }
        return self._per_model_usage[model]

    def track_usage(self, usage: dict[str, Any], model: str) -> None:
        """Accumulate one provider usage record and estimate its model cost."""
        from .providers import estimate_cost

        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        total_tokens = int(
            usage.get("total_tokens", prompt_tokens + completion_tokens) or 0
        )

        self._total_usage["prompt_tokens"] = (
            int(self._total_usage.get("prompt_tokens", 0)) + prompt_tokens
        )
        self._total_usage["completion_tokens"] = (
            int(self._total_usage.get("completion_tokens", 0)) + completion_tokens
        )
        self._total_usage["total_tokens"] = (
            int(self._total_usage.get("total_tokens", 0)) + total_tokens
        )

        model_usage = self.ensure_model(model)
        model_usage["prompt_tokens"] += prompt_tokens
        model_usage["completion_tokens"] += completion_tokens
        model_usage["total_tokens"] += total_tokens
        model_usage["cost"] += estimate_cost(model, prompt_tokens, completion_tokens)

    def merge_usage(
        self,
        total_usage: dict[str, Any] | None = None,
        per_model_usage: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Merge persisted usage totals into the active session."""
        for key in _USAGE_KEYS:
            value = (total_usage or {}).get(key)
            if value is not None:
                self._total_usage[key] = int(self._total_usage.get(key, 0)) + int(value)

        for model, incoming in (per_model_usage or {}).items():
            current = self.ensure_model(model)
            for key in _USAGE_KEYS:
                current[key] += int(incoming.get(key, 0) or 0)
            current["cost"] += float(incoming.get("cost", 0.0) or 0.0)

    def replace_total_usage(self, usage: dict[str, Any]) -> None:
        self._total_usage = dict(usage)

    def replace_per_model_usage(
        self,
        usage: dict[str, dict[str, Any]],
    ) -> None:
        self._per_model_usage = {
            model: dict(stats) for model, stats in usage.items()
        }

    def record_tool_call(self, entry: dict[str, Any]) -> None:
        self.enable_history("tools")
        self._tool_calls.append(deepcopy(entry))

    def record_command(self, entry: dict[str, Any]) -> None:
        self.enable_history("commands")
        self._commands.append(deepcopy(entry))

    def record_file_edit(self, entry: dict[str, Any]) -> None:
        self.enable_history("files")
        self._file_edits.append(deepcopy(entry))

    def replace_tool_calls(self, entries: list[dict[str, Any]]) -> None:
        self.enable_history("tools")
        self._tool_calls = deepcopy(entries)

    def replace_commands(self, entries: list[dict[str, Any]]) -> None:
        self.enable_history("commands")
        self._commands = deepcopy(entries)

    def replace_file_edits(self, entries: list[dict[str, Any]]) -> None:
        self.enable_history("files")
        self._file_edits = deepcopy(entries)

    def snapshot(self) -> dict[str, Any]:
        """Return the JSON-compatible telemetry part of a session snapshot."""
        return {
            "total_usage": self.total_usage,
            "per_model_usage": self.per_model_usage,
            "tool_call_history": self.tool_calls,
            "command_history": self.commands,
            "file_edit_history": self.file_edits,
        }

    def restore(self, payload: dict[str, Any]) -> None:
        """Replace telemetry state from a session snapshot."""
        self.replace_total_usage(payload.get("total_usage", self.total_usage))
        self.replace_per_model_usage(
            payload.get("per_model_usage", self.per_model_usage)
        )
        self.replace_tool_calls(list(payload.get("tool_call_history", [])))
        self.replace_commands(list(payload.get("command_history", [])))
        self.replace_file_edits(list(payload.get("file_edit_history", [])))

    # Mutable views are intentionally private and exist only for AgentSession's
    # legacy underscore properties. New consumers must use the methods above.
    def _mutable_total_usage(self) -> dict[str, int]:
        return self._total_usage

    def _mutable_per_model_usage(self) -> dict[str, dict[str, Any]]:
        return self._per_model_usage

    def _mutable_tool_calls(self) -> list[dict[str, Any]]:
        return self._tool_calls

    def _mutable_commands(self) -> list[dict[str, Any]]:
        return self._commands

    def _mutable_file_edits(self) -> list[dict[str, Any]]:
        return self._file_edits


class _LegacySessionTelemetry:
    """Public telemetry API backed by historical attributes on a test double."""

    def __init__(self, session: Any) -> None:
        self._session = session

    @property
    def total_usage(self) -> dict[str, int]:
        value = getattr(self._session, "total_usage", None)
        if isinstance(value, dict):
            return dict(value)
        return dict(getattr(self._session, "_total_usage", {}) or {})

    @property
    def per_model_usage(self) -> dict[str, dict[str, Any]]:
        value = getattr(self._session, "per_model_usage", None)
        if not isinstance(value, dict):
            value = getattr(self._session, "_per_model_usage", {}) or {}
        return {model: dict(stats) for model, stats in value.items()}

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return deepcopy(getattr(self._session, "_tool_call_history", []) or [])

    @property
    def commands(self) -> list[dict[str, Any]]:
        return deepcopy(getattr(self._session, "_command_history", []) or [])

    @property
    def file_edits(self) -> list[dict[str, Any]]:
        return deepcopy(getattr(self._session, "_file_edit_history", []) or [])

    def status(self) -> dict[str, bool]:
        return {
            "tools": hasattr(self._session, "_tool_call_history"),
            "commands": hasattr(self._session, "_command_history"),
            "files": hasattr(self._session, "_file_edit_history"),
        }

    def merge_usage(
        self,
        total_usage: dict[str, Any] | None = None,
        per_model_usage: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        total = dict(getattr(self._session, "_total_usage", {}) or {})
        for key in _USAGE_KEYS:
            total[key] = int(total.get(key, 0)) + int((total_usage or {}).get(key, 0))
        self._session._total_usage = total

        models = {
            model: dict(stats)
            for model, stats in (
                getattr(self._session, "_per_model_usage", {}) or {}
            ).items()
        }
        for model, incoming in (per_model_usage or {}).items():
            current = models.setdefault(
                model,
                {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost": 0.0,
                },
            )
            for key in _USAGE_KEYS:
                current[key] += int(incoming.get(key, 0) or 0)
            current["cost"] += float(incoming.get("cost", 0.0) or 0.0)
        self._session._per_model_usage = models

    def record_tool_call(self, entry: dict[str, Any]) -> None:
        history = list(getattr(self._session, "_tool_call_history", []) or [])
        history.append(deepcopy(entry))
        self._session._tool_call_history = history

    def record_command(self, entry: dict[str, Any]) -> None:
        history = list(getattr(self._session, "_command_history", []) or [])
        history.append(deepcopy(entry))
        self._session._command_history = history

    def record_file_edit(self, entry: dict[str, Any]) -> None:
        history = list(getattr(self._session, "_file_edit_history", []) or [])
        history.append(deepcopy(entry))
        self._session._file_edit_history = history

    def replace_file_edits(self, entries: list[dict[str, Any]]) -> None:
        self._session._file_edit_history = deepcopy(entries)

    def snapshot(self) -> dict[str, Any]:
        return {
            "total_usage": self.total_usage,
            "per_model_usage": self.per_model_usage,
            "tool_call_history": self.tool_calls,
            "command_history": self.commands,
            "file_edit_history": self.file_edits,
        }

    def restore(self, payload: dict[str, Any]) -> None:
        self._session._total_usage = dict(payload.get("total_usage", self.total_usage))
        self._session._per_model_usage = {
            model: dict(stats)
            for model, stats in payload.get(
                "per_model_usage", self.per_model_usage
            ).items()
        }
        self._session._tool_call_history = list(payload.get("tool_call_history", []))
        self._session._command_history = list(payload.get("command_history", []))
        self._session._file_edit_history = list(payload.get("file_edit_history", []))


def get_session_telemetry(session: Any) -> TelemetryRuntime:
    """Return native telemetry or a compatibility adapter for a legacy session."""
    telemetry = getattr(session, "telemetry", None)
    if telemetry is not None:
        return cast(TelemetryRuntime, telemetry)
    return _LegacySessionTelemetry(session)


__all__ = ["SessionTelemetry", "TelemetryRuntime", "get_session_telemetry"]

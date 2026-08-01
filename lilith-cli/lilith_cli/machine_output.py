"""Machine-readable one-shot output for ``lilith prompt``.

This module turns ``lilith prompt`` into a safe interface for another AI
to consume as a sub-agent. It reuses ``AgentSession.process_message_stream``
(the same agent loop the interactive Rich UI uses) but consumes the event
stream *without* rendering: no banners, no separators, no thinking panels,
no tool cards, no timers, and no ANSI escape sequences on stdout.

Two output formats:

- ``text``: stdout carries only the final response text.
- ``json``: stdout carries a single stable JSON document with
  ``schema_version``, ``success``, ``response``, ``usage``,
  ``duration_ms`` and ``error``. Internal reasoning is never included.

Diagnostics and errors go to stderr and produce a non-zero exit code
(via :class:`MachineOutputError`, which the CLI maps to ``SystemExit(1)``).

The default ``lilith prompt`` invocation (no flags) never touches this
module — the Rich path in ``repl.run_oneshot`` is unchanged.
"""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    from .agent import AgentSession

SCHEMA_VERSION = "1.0"

# Matches CSI / OSC and other common ANSI escape sequences.
_ANSI_RE = None  # lazily compiled on first use


class MachineOutputError(Exception):
    """Raised when the machine-readable run fails.

    The CLI catches this, prints the message to stderr and exits with a
    non-zero status.
    """


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from *text*.

    Defence in depth: this module never emits ANSI itself, but the LLM
    response may contain escape sequences. Machine consumers must never
    have to parse them out.
    """
    global _ANSI_RE
    if _ANSI_RE is None:
        import re

        _ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
    return _ANSI_RE.sub("", text)


def _err(msg: str, *, err: TextIO | None = None) -> None:
    """Print a diagnostic line to stderr (never stdout)."""
    stream = err if err is not None else sys.stderr
    print(msg, file=stream, flush=True)


async def _run_agent_stream(session: AgentSession, text: str) -> dict[str, Any]:
    """Consume ``session.process_message_stream`` and collect the result.

    Returns a dict with ``response``, ``usage``, ``tool_errors`` and
    ``cancelled``. This is the single place that talks to the agent —
    the same event stream the Rich UI renders — so the agent logic is
    reused, not duplicated.
    """
    text_parts: list[str] = []
    usage: dict[str, Any] = {}
    tool_errors: list[str] = []
    cancelled = False

    async for event in session.process_message_stream(text):
        event_type = event.get("type", "")
        if event_type == "text":
            text_parts.append(event.get("content") or "")
        elif event_type == "reasoning":
            # Internal reasoning is intentionally dropped in machine mode.
            continue
        elif event_type == "tool_call":
            # A tool round starts: any text accumulated before this point
            # is preamble/intermediate reasoning output, NOT the final
            # answer. Drop it so ``--quiet`` emits only the segment the
            # model produces AFTER its last tool call.
            text_parts.clear()
        elif event_type == "tool_result":
            error = event.get("error") or event.get("is_error")
            if error:
                tool_errors.append(f"{event.get('name', '?')}: {error}")
        elif event_type == "done":
            usage = event.get("usage") or {}
            content = event.get("content")
            if content and not text_parts:
                # No text was streamed this round: fall back to the
                # accumulated content carried by the done event (real
                # process_message_stream field).
                text_parts.append(content)
            break
        elif event_type == "cancelled":
            cancelled = True
            break

    return {
        "response": "".join(text_parts),
        "usage": usage,
        "tool_errors": tool_errors,
        "cancelled": cancelled,
    }


def _normalise_usage(raw: dict[str, Any]) -> dict[str, int]:
    """Return a stable usage dict (zero-filled, int-coerced)."""
    if not isinstance(raw, dict):
        raw = {}
    out: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = raw.get(key, 0)
        try:
            out[key] = int(value or 0)
        except (TypeError, ValueError):
            out[key] = 0
    return out


async def run_oneshot_machine(
    session: AgentSession,
    text: str,
    *,
    output_format: str = "text",
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run a single prompt and emit machine-readable output.

    Parameters
    ----------
    session:
        The ``AgentSession`` to run the prompt through (same object the
        Rich UI uses).
    text:
        The user prompt.
    output_format:
        ``"text"`` (final response only) or ``"json"`` (single stable
        JSON document). ``json.dumps`` handles escaping, so stdout stays
        a single parseable document.
    out / err:
        Optional stream overrides for testing. Default to
        ``sys.stdout`` / ``sys.stderr``.

    Returns
    -------
    int
        Process exit code: ``0`` on success, ``1`` on any error.

    Raises
    ------
    ValueError
        If *output_format* is not ``"text"`` or ``"json"``.
    """
    if output_format not in ("text", "json"):
        raise ValueError(f"output_format inválido: {output_format!r}")

    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr

    started = time.perf_counter()
    try:
        result = await _run_agent_stream(session, text)
    except Exception as exc:  # noqa: BLE001 — CLI frontier: any agent/provider failure must become a machine-readable error, never propagate.
        duration_ms = int((time.perf_counter() - started) * 1000)
        message = f"{type(exc).__name__}: {exc}"
        if output_format == "json":
            _emit_json(
                stdout,
                success=False,
                response=None,
                usage=None,
                duration_ms=duration_ms,
                error=message,
            )
        else:
            _err(f"error: {message}", err=stderr)
        return 1

    duration_ms = int((time.perf_counter() - started) * 1000)
    response = _strip_ansi(result["response"])

    # Surface tool execution failures without leaking the internal event
    # protocol: stderr diagnostics + non-zero exit in text mode; in JSON
    # mode they stay as diagnostics on stderr while the document still
    # carries the final response (the model recovered from the tool
    # failure to produce an answer).
    if result["tool_errors"]:
        for tool_error in result["tool_errors"]:
            _err(f"warning: tool error: {tool_error}", err=stderr)

    if result["cancelled"]:
        if output_format == "json":
            _emit_json(
                stdout,
                success=False,
                response=None,
                usage=None,
                duration_ms=duration_ms,
                error="cancelled",
            )
        else:
            _err("error: cancelled", err=stderr)
        return 1

    if output_format == "json":
        _emit_json(
            stdout,
            success=True,
            response=response,
            usage=_normalise_usage(result["usage"]),
            duration_ms=duration_ms,
            error=None,
        )
    else:
        stdout.write(response)
        if response and not response.endswith("\n"):
            stdout.write("\n")
        stdout.flush()
    return 0


def _emit_json(
    stdout: TextIO,
    *,
    success: bool,
    response: str | None,
    usage: dict[str, int] | None,
    duration_ms: int,
    error: str | None,
) -> None:
    """Write the single stable JSON document to *stdout*."""
    import json

    payload = {
        "schema_version": SCHEMA_VERSION,
        "success": success,
        "response": response,
        "usage": usage,
        "duration_ms": duration_ms,
        "error": error,
    }
    stdout.write(json.dumps(payload, ensure_ascii=False))
    stdout.write("\n")
    stdout.flush()

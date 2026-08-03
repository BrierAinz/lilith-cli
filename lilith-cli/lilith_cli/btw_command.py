"""``/btw`` slash command — quick side question that does NOT touch history.

Pattern borrowed from Claude Code's ``/btw``: the user wants to ask a
short, side-of-the-conversation question ("what's the syntax of X again?",
"is this Python 3.10+?", "recite the regex docs") and does not want it to
bloat the conversation context that the model sees on the next real turn.

The flow is:

    1. Build a minimal message list: a one-off system nudge + the question.
    2. Call ``session.provider.complete`` directly — no tools, no streaming,
       no history mutation. The model answers in one shot.
    3. Render the answer with a small "BTW" banner so the user knows it was
       a transient ask.
    4. Return — the next user turn sees an unchanged ``session.history``.

The provider layer already handles retries/backoff and returns a
standardised ``{"content": ..., "usage": ...}`` dict, so we reuse it
verbatim rather than re-implementing HTTP plumbing.

Aliases: ``/aside``, ``/side``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .render import console, render_error

if TYPE_CHECKING:
    from .agent import AgentSession


_BTW_SYSTEM = (
    "You are answering a one-off side question that the user explicitly "
    "asked NOT to keep in their conversation history. Answer concisely and "
    "to the point, like a quick aside. Do not preface with 'Sure!' or "
    "re-state the question. Do not use tools."
)


async def run_btw_command(session: AgentSession, args: str) -> None:
    """Send a transient question to the provider without touching history.

    Parameters
    ----------
    session:
        The current ``AgentSession``. Only ``session.provider`` and
        ``session.system_prompt`` are read; ``session.history`` is not
        mutated.
    args:
        The user's free-form question. Empty / whitespace-only input
        is rejected with a friendly error.
    """
    question = args.strip()
    if not question:
        render_error("/btw necesita una pregunta. Uso: /btw <pregunta>")
        return

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _BTW_SYSTEM},
        {"role": "user", "content": question},
    ]

    console.print(f"\n[dim]┌─ BTW ─[/]")
    try:
        response = await session.provider.complete(messages)
    except Exception as exc:
        console.print(f"[dim]└─[/] [red]Error al consultar el modelo:[/] {exc}")
        return

    answer = _extract_answer(response)
    if not answer:
        console.print("[dim]└─[/] [yellow]El modelo no devolvió contenido.[/]")
        return

    # Render the answer with a light frame so the user can distinguish a
    # transient ask from a real turn. Markdown keeps things readable when
    # the answer contains code blocks or lists.
    console.print(f"[dim]│[/] {answer}")
    console.print("[dim]└─ (no se guardó en el historial)[/]\n")


def _extract_answer(response: Any) -> str:
    """Pull the textual answer out of whatever shape the provider returns.

    Different providers wrap the assistant message slightly differently;
    we accept either ``response["choices"][0]["message"]["content"]``
    (OpenAI-style) or a flat ``response["content"]`` (some local mocks).
    """
    if not isinstance(response, dict):
        return ""

    # OpenAI-style envelope.
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message")
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str):
                    return content.strip()

    # Flat shape used by some test mocks and a few local backends.
    content = response.get("content")
    if isinstance(content, str):
        return content.strip()

    # Last resort: some providers return a list of content parts.
    parts = response.get("content_parts")
    if isinstance(parts, list):
        joined = "".join(p for p in parts if isinstance(p, str))
        return joined.strip()

    return ""
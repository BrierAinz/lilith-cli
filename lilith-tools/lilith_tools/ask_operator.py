"""Interactive, terminal-safe operator choice tool."""

from __future__ import annotations

import queue
import sys
import threading
from typing import Any, TextIO

from .base import BaseTool, ToolResult
from .registry import ToolRegistry


_OTHER = {"label": "Otra cosa…", "detail": "Escribe una respuesta libre."}
_DEFAULT_TIMEOUT = 180.0


def _is_tty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):
        return False


def _readline_with_timeout(timeout: float, stream: TextIO) -> str | None:
    result: queue.Queue[str | BaseException] = queue.Queue(maxsize=1)

    def read() -> None:
        try:
            result.put(stream.readline())
        except BaseException as exc:
            result.put(exc)

    threading.Thread(target=read, name="lilith-operator-input", daemon=True).start()
    try:
        value = result.get(timeout=timeout)
    except queue.Empty:
        return None
    if isinstance(value, BaseException):
        raise value
    return value


def _validate_options(options: Any) -> str | None:
    if not isinstance(options, list) or not 2 <= len(options) <= 5:
        return "options debe ser una lista de entre 2 y 5 opciones."
    for index, option in enumerate(options, 1):
        if not isinstance(option, dict):
            return f"La opción {index} debe ser un objeto con label y detail."
        label = option.get("label")
        detail = option.get("detail")
        if not isinstance(label, str) or not label.strip():
            return f"La opción {index} tiene label vacío."
        if len(label.split()) > 5:
            return f"La opción {index} tiene un label de más de 5 palabras."
        if not isinstance(detail, str) or not detail.strip():
            return f"La opción {index} tiene detail vacío."
        if "\n" in detail or "\r" in detail:
            return f"La opción {index} debe tener detail en una sola línea."
    return None


@ToolRegistry.register
class AskOperatorTool(BaseTool):
    name = "ask_operator"
    description = (
        "Presenta opciones realmente distintas para que el operador elija. "
        "Una lista donde una opción es obviamente la correcta no es una pregunta, "
        "es una forma lenta de no preguntar nada: decide y sigue. No la uses si "
        "la respuesta se deduce del código o hay un valor por defecto sensato."
    )
    parameters = {
        "question": {"type": "string", "required": True},
        "options": {"type": "array", "required": True},
        "multi": {"type": "boolean", "required": False},
        "timeout": {"type": "number", "required": False},
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        question = kwargs.get("question")
        if not isinstance(question, str) or not question.strip():
            return ToolResult(False, None, "question es obligatoria y no puede estar vacía.")
        if not question.rstrip().endswith("?"):
            return ToolResult(False, None, "question debe ser una pregunta completa terminada en '?'.")
        options = kwargs.get("options")
        error = _validate_options(options)
        if error:
            return ToolResult(False, None, error)
        try:
            timeout = float(kwargs.get("timeout", _DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            return ToolResult(False, None, "timeout debe ser un número positivo.")
        if timeout <= 0:
            return ToolResult(False, None, "timeout debe ser un número positivo.")

        stdin, stdout = sys.stdin, sys.stdout
        if not (_is_tty(stdin) and _is_tty(stdout)):
            return ToolResult(False, None, "No hay un operador delante de una terminal interactiva. No puedo esperar una selección: formula la pregunta en prosa y continúa.")

        choices = [dict(option) for option in options] + [_OTHER]
        theme = None
        try:
            from lilith_cli.render import console, get_theme
            theme = get_theme()
            console.print(f"[{theme.theme['realm']}]᛭ {question}[/]")
            for index, option in enumerate(choices, 1):
                console.print(f"[{theme.theme['realm']}]  {index}  {option['label']}[/]")
                console.print(f"[{theme.theme['frost']}]      {option['detail']}[/]")
            suffix = " (separa números con comas)" if kwargs.get("multi", False) else ""
            console.print(f"[{theme.theme['realm']}]  › Elige una opción{suffix}:[/]")
        except Exception:
            stdout.write(question + "\n")
            stdout.flush()

        try:
            answer = _readline_with_timeout(timeout, stdin)
        except (KeyboardInterrupt, EOFError) as exc:
            return ToolResult(False, None, f"La selección fue interrumpida: {exc}.")
        if answer is None:
            return ToolResult(False, None, f"Tiempo agotado tras {timeout:g} segundos; pregunta en prosa.")
        raw = answer.strip()
        if not raw:
            return ToolResult(False, None, "No se eligió ninguna opción; pregunta en prosa o vuelve a intentarlo.")
        try:
            numbers = [int(part.strip()) for part in raw.split(",")]
        except ValueError:
            return ToolResult(False, None, "Respuesta inválida: escribe el número de una opción.")
        if not kwargs.get("multi", False) and len(numbers) != 1:
            return ToolResult(False, None, "Elige exactamente una opción.")
        if len(set(numbers)) != len(numbers) or any(n < 1 or n > len(choices) for n in numbers):
            return ToolResult(False, None, "Número de opción fuera de rango.")

        selected = [choices[n - 1] for n in numbers]
        free_text = None
        if any(n == len(choices) for n in numbers):
            try:
                from lilith_cli.render import console
                color = theme.theme["realm"] if theme else "realm"
                console.print(f"[{color}]  › Escribe tu respuesta:[/]")
            except Exception:
                pass
            try:
                free_text = _readline_with_timeout(timeout, stdin)
            except (KeyboardInterrupt, EOFError) as exc:
                return ToolResult(False, None, f"La respuesta libre fue interrumpida: {exc}.")
            if free_text is None:
                return ToolResult(False, None, f"Tiempo agotado tras {timeout:g} segundos; pregunta en prosa.")
            if not free_text.strip():
                return ToolResult(False, None, "La respuesta libre no puede estar vacía.")

        return ToolResult(True, {"selected": selected, "indices": numbers, "free_text": free_text.strip() if free_text is not None else None})

"""Conclave tool — fan-out the same question across multiple Hlidskjalf presets.

The conclave is a "council of models": the orchestrator hands it one
question and a list of 2-4 presets, and each preset is asked the same
question **in parallel** through the existing ``DelegateSubagentTool``
machinery. The tool returns every individual response (one row per
preset) and performs **no synthesis** — that is the orchestrator's job.

Design notes:

* **Reuse, don't reinvent.** Each preset call invokes
  :class:`lilith_tools.delegate.DelegateSubagentTool` (one-shot,
  optionally ``structured=True``) so the existing chain of provider
  config resolution, prompt construction, usage accounting, and
  structured-output degradation all keep working.
* **Per-preset timeout.** A single preset hanging on the network must
  not block the others. We cap each preset at 60s with a thread-based
  join + a final-state inspection; presets that exceed the budget
  show up in ``data['responses']`` with ``error='timeout'`` and an
  empty ``content``.
* **One failure does not take down the rest.** A preset that raises,
  times out, or returns ``success=False`` is reported in its own row;
  the tool still succeeds overall as long as at least one preset ran
  to completion. When ALL presets fail, the tool returns
  ``success=False`` so the orchestrator can short-circuit.
* **No synthesis.** The orchestrator chooses how to combine answers.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .base import BaseTool, ToolResult
from .delegate import DelegateSubagentTool
from .registry import ToolRegistry


logger = logging.getLogger(__name__)


# Hard ceiling per preset. Matches the per-tool ``timeout_seconds`` the
# delegate declares for its own call path (180s) being too generous for
# a fan-out — we want the conclave to fail fast and let the orchestrator
# pick up the partial result.
DEFAULT_PRESET_TIMEOUT_SECONDS = 60.0
# Conclave must run 2-4 presets; constants here so callers/tests share them.
MIN_PRESETS = 2
MAX_PRESETS = 4
# Default presets when the caller doesn't specify any. These are the two
# complementary presets from the standard Hlidskjalf roster — a
# minimal, opinionated default that makes "drop me a conclave" useful
# without forcing the orchestrator to memorise names.
DEFAULT_PRESETS = ("batch-deepseek", "grok-research")


@ToolRegistry.register
class ConclaveTool(BaseTool):
    """Run the same question in parallel across 2-4 sub-agent presets.

    Each preset is dispatched through :class:`DelegateSubagentTool` so
    all existing provider resolution, usage accounting, and structured
    degradation chains apply uniformly. The tool returns a list of rows
    — one per preset — and performs no synthesis.
    """

    name = "conclave"
    # Conclave runs N presets in parallel; the floor is the per-preset
    # budget. The agent honours this as a timeout floor (and the
    # per-preset cap inside ``execute`` enforces the real wall clock).
    timeout_seconds = int(DEFAULT_PRESET_TIMEOUT_SECONDS * MAX_PRESETS) + 30
    description = (
        "Lanzar la misma pregunta a 2-4 presets de Hlidskjalf en paralelo "
        "y devolver TODAS las respuestas crudas (sin sintesis). "
        "Cada preset se ejecuta con la maquinaria estandar de "
        "delegate_subagent; un preset que falle o timeout no tumba al resto. "
        "Sintesis: la hace el orquestador a partir de data['responses']. "
        "Presets por defecto: batch-deepseek + grok-research."
    )
    parameters = {
        "question": {
            "type": "string",
            "description": (
                "Pregunta completa y autocontenida. Se envia identica a "
                "cada preset."
            ),
            "required": True,
        },
        "presets": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Lista de 2-4 nombres de preset. Default: "
                "['batch-deepseek', 'grok-research']."
            ),
            "required": False,
        },
        "structured": {
            "type": "boolean",
            "default": False,
            "description": (
                "Si True, exige respuesta estructurada por preset "
                "(TASK_SCHEMA con cadena de degradacion)."
            ),
            "required": False,
        },
        "max_tokens": {
            "type": "integer",
            "description": (
                "Limite opcional de tokens de salida para CADA preset."
            ),
            "required": False,
        },
        "timeout": {
            "type": "number",
            "default": DEFAULT_PRESET_TIMEOUT_SECONDS,
            "description": (
                "Timeout en segundos por preset (default 60s). "
                "Un preset que exceda el budget aparece con error='timeout'."
            ),
            "required": False,
        },
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        question = str(kwargs.get("question", "")).strip()
        if not question:
            return ToolResult(
                success=False, data=None,
                error="'question' es requerido",
            )

        presets = kwargs.get("presets")
        if presets is None or (isinstance(presets, (list, tuple)) and not presets):
            presets = list(DEFAULT_PRESETS)
        else:
            presets = [str(p).strip() for p in presets if str(p).strip()]

        if len(presets) < MIN_PRESETS:
            return ToolResult(
                success=False, data=None,
                error=(
                    f"conclave requiere al menos {MIN_PRESETS} presets; "
                    f"recibidos: {len(presets)}"
                ),
            )
        if len(presets) > MAX_PRESETS:
            return ToolResult(
                success=False, data=None,
                error=(
                    f"conclave acepta maximo {MAX_PRESETS} presets; "
                    f"recibidos: {len(presets)}"
                ),
            )

        structured = bool(kwargs.get("structured", False))
        per_timeout = float(kwargs.get("timeout", DEFAULT_PRESET_TIMEOUT_SECONDS))
        max_tokens = kwargs.get("max_tokens")

        rows = self._run_parallel(
            question=question,
            presets=presets,
            structured=structured,
            max_tokens=max_tokens,
            per_timeout=per_timeout,
        )

        ok_count = sum(1 for r in rows if not r.get("error"))
        data: dict[str, Any] = {
            "question": question,
            "presets_requested": list(presets),
            "responses": rows,
            "ok_count": ok_count,
            "failed_count": len(rows) - ok_count,
        }
        if ok_count == 0:
            # All presets failed — the conclave is useless to the orchestrator.
            return ToolResult(
                success=False, data=data,
                error="todos los presets fallaron",
            )
        # Partial success: still a success; the orchestrator decides whether
        # the surviving answers are enough.
        return ToolResult(success=True, data=data, error="")

    # ── Parallel fan-out ─────────────────────────────────────────────

    def _run_parallel(
        self,
        *,
        question: str,
        presets: list[str],
        structured: bool,
        max_tokens: Any,
        per_timeout: float,
    ) -> list[dict[str, Any]]:
        """Run each preset in its own thread, capped at ``per_timeout`` each.

        Threading (not asyncio.gather) is the right fit because
        ``DelegateSubagentTool.execute`` is synchronous and itself runs
        ``asyncio.run`` internally; spawning N OS threads gives true
        parallel network calls without forcing us to plumb an event
        loop through the tool layer.
        """
        rows: list[dict[str, Any] | None] = [None] * len(presets)
        results: list[ToolResult | BaseException | None] = [None] * len(presets)

        def _worker(idx: int, preset: str) -> None:
            try:
                tool = DelegateSubagentTool()
                kwargs: dict[str, Any] = {
                    "preset": preset,
                    "prompt": question,
                    "structured": structured,
                }
                if max_tokens is not None:
                    kwargs["max_tokens"] = int(max_tokens)
                results[idx] = tool.execute(**kwargs)
            except BaseException as exc:  # noqa: BLE001 — last-resort guard
                # Stash the exception; the main thread turns it into a row.
                results[idx] = exc

        threads = [
            threading.Thread(
                target=_worker, args=(i, p), name=f"conclave-{p}", daemon=True,
            )
            for i, p in enumerate(presets)
        ]
        for t in threads:
            t.start()

        # Per-thread join with its own ceiling so one slow preset can't
        # starve the others. ``join(timeout)`` returns once the budget
        # elapses whether or not the thread finished.
        for i, t in enumerate(threads):
            t.join(timeout=per_timeout)
            if t.is_alive():
                # Thread is still running; mark it timed out and let the
                # daemon die when the process exits. We do NOT call
                # ``close()`` on the in-flight provider — that would race
                # with the running ``complete()`` call. The thread is a
                # daemon, so the interpreter won't wait for it on exit.
                results[i] = None  # sentinel: still in flight
                logger.warning(
                    "conclave preset %r excedio timeout de %.1fs",
                    presets[i], per_timeout,
                )

        # Build the rows in input order.
        for i, preset in enumerate(presets):
            res = results[i]
            if res is None:
                rows[i] = {
                    "preset": preset,
                    "model": None,
                    "content": "",
                    "usage": {},
                    "error": "timeout",
                }
                continue
            if isinstance(res, BaseException):
                rows[i] = {
                    "preset": preset,
                    "model": None,
                    "content": "",
                    "usage": {},
                    "error": f"{type(res).__name__}: {res}",
                }
                continue
            # Successful ToolResult from DelegateSubagentTool.
            data = res.data if isinstance(res.data, dict) else {}
            row: dict[str, Any] = {
                "preset": preset,
                "model": data.get("model"),
                "content": data.get("content") or "",
                "usage": data.get("usage") or {},
            }
            if structured and "structured" in data:
                row["structured"] = data.get("structured")
                row["validation_errors"] = data.get("validation_errors") or []
            if not res.success:
                row["error"] = res.error or "sub-agent reported failure"
            else:
                row["error"] = ""
            rows[i] = row

        # ``rows`` is fully populated; cast for the type checker.
        return [r for r in rows if r is not None]

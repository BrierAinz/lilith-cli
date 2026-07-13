"""LilithEngine — Motor de orquestación multi-agente de Lilith

Este módulo es el punto de entrada principal de lilith-orchestrator.
LilithEngine conecta la configuración de Lilith con el sistema swarm
para producir respuestas orquestadas por múltiples agentes.

Uso:
    from lilith_orchestrator.engine import LilithEngine
    engine = LilithEngine(config, memory)
    result = engine.process("Hola, ¿cómo estás?")
    # result = {"response": "...", "usage": {...}, "tool_call": None}

Cuando el swarm no está disponible o no tiene agentes registrados,
LilithEngine realiza un fallback a una llamada LLM directa usando la
configuración proporcionada.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from lilith_core.hooks import HookContext, HookType, get_hook_registry
from lilith_core.token_optimizer import (
    ResponseCache,
    TokenTracker,
    estimate_tokens,
)

# Optional: HeimdallAuditor for post-processing quality gates
try:
    from lilith_skills.heimdall_auditor import HeimdallAuditor
    _HEIMDALL_AVAILABLE = True
except ImportError:
    _HEIMDALL_AVAILABLE = False
    HeimdallAuditor = None


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


logger = logging.getLogger("lilith.engine")

# ── Tipos de resultado ────────────────────────────────────────────────────────


@dataclass
class EngineUsage:
    """Uso de tokens y métricas de una llamada al engine."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    agents_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert usage stats to a plain dictionary."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": round(self.latency_ms, 2),
            "agents_used": self.agents_used,
        }


# ── Tracing helpers (AgentLens-inspired observability) ────────────────────────
#
# These context managers wrap the in-process Tracer so engine code can
# instrument lifecycle events without knowing whether tracing is enabled.
# When the tracer is disabled or the orchestrator's tracing module is not
# installed, ``__enter__`` yields ``None`` — callers always check before
# calling ``set_attribute`` so the hot path stays branch-free of error
# handling. The wrappers themselves never raise.


class _NoOpSpan:
    """A span-shaped no-op returned when tracing is disabled.

    Implements just enough of the Span API for engine code to call
    ``set_attribute`` and ``record_exception`` without raising.
    """

    def __init__(
        self,
        name: str,
        kind: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.kind = kind
        self.attributes: dict[str, Any] = dict(attributes or {})
        self.status = "ok"
        self.error_message: str | None = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_attributes(self, **attrs: Any) -> None:
        self.attributes.update(attrs)

    def record_exception(self, message: str) -> None:
        self.status = "error"
        self.error_message = message

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "error_message": self.error_message,
            "attributes": self.attributes,
        }


def _no_op_span(
    name: str,
    kind: str | None,
    attributes: dict[str, Any] | None,
) -> _NoOpSpan:
    """Factory used by ``_span`` when tracer span creation is unavailable."""
    span = _NoOpSpan(name=name, kind=kind, attributes=attributes)
    return span


class _TracingContext:
    """Context manager wrapping ``tracer.trace_request``.

    ``_traced`` returns one of these; ``__enter__`` yields the active
    root span (or ``None`` if tracing is unavailable) and ``__exit__``
    delegates to the underlying context manager.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._span: Any = None

    def __enter__(self) -> Any:
        if self._inner is None:
            return None
        try:
            self._span = self._inner.__enter__()
        except Exception as exc:  # noqa: BLE001
            logger.debug("trace_request enter failed: %s", exc)
            self._inner = None
            self._span = None
        return self._span

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool | None:
        if self._inner is None:
            return None
        try:
            return self._inner.__exit__(exc_type, exc, tb)
        except Exception as ex:  # noqa: BLE001
            logger.debug("trace_request exit failed: %s", ex)
            return None


class _SpanContext:
    """Context manager wrapping ``tracer.span`` (or a no-op fallback)."""

    def __init__(self, span_like: Any) -> None:
        self._span = span_like

    def __enter__(self) -> Any:
        # When the span was created directly (no-op path), the "context
        # manager" is the span itself, so just return it.
        if hasattr(self._span, "__enter__") and not isinstance(self._span, _NoOpSpan):
            try:
                self._span = self._span.__enter__()
            except Exception as exc:  # noqa: BLE001
                logger.debug("span enter failed: %s", exc)
                self._span = None
        return self._span

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool | None:
        if self._span is None:
            return None
        closer = getattr(self._span, "__exit__", None)
        if closer is None:
            return None
        try:
            return closer(exc_type, exc, tb)
        except Exception as ex:  # noqa: BLE001
            logger.debug("span exit failed: %s", ex)
            return None


# ── LilithEngine ──────────────────────────────────────────────────────────────


class LilithEngine:
    """Motor central de orquestación de Lilith.

    Conecta la configuración de Lilith (LilithConfig) y el almacén de
    memoria (MemoryStore opcional) con el sistema swarm para producir
    respuestas orquestadas.

    Flujo:
        1. Recibe mensaje del usuario.
        2. Intenta usar el sistema swarm (Coordinator + Swarm + TaskPlanner).
        3. Si el swarm no está disponible, falla a una llamada LLM directa.
        4. Retorna resultado con response, usage y tool_call.

    El engine es async-compatible:
        - ``process()`` se puede llamar desde código síncrono.
        - ``process_stream()`` es un async generator para respuestas streaming.
    """

    def __init__(self, config: Any, memory: Any = None) -> None:
        """Inicializa LilithEngine.

        Args:
            config: Instancia de LilithConfig con la configuración del modelo.
                     Se accede a ``config.model``, ``config.base_url``,
                     ``config.api_key``, etc.
            memory: Instancia opcional de MemoryStore para consulta de contexto.

        """
        self.config = config
        self.memory = memory

        # Hook registry for lifecycle events
        self._hooks = get_hook_registry()

        # Componentes del swarm (lazy init)
        self._swarm: Any = None
        self._coordinator: Any = None

        # Métricas
        self._request_count: int = 0
        self._error_count: int = 0
        self._total_latency_ms: float = 0.0
        self._cache_hits: int = 0
        self._cache_misses: int = 0

        # Response caching (Talon-style: cache LLM responses to avoid redundant calls)
        # Disabled by default; enable with engine.enable_cache() or pass cache_size in __init__.
        self._cache_enabled: bool = False
        self._response_cache: ResponseCache = ResponseCache(
            max_size=getattr(config, "cache_size", 256),
            ttl_seconds=getattr(config, "cache_ttl_seconds", 3600.0),
        )

        # Token tracking (per-session and per-agent budgets)
        self._token_tracker: TokenTracker = TokenTracker(
            default_limit=getattr(config, "token_budget", 100000),
        )

        # Optional: HeimdallAuditor for post-processing quality gates
        self._auditor: Any = None
        if _HEIMDALL_AVAILABLE:
            try:
                self._auditor = HeimdallAuditor()
                logger.info("HeimdallAuditor enabled for output quality gates")
            except Exception as exc:
                logger.warning("Failed to initialize HeimdallAuditor: %s", exc)

        logger.info("LilithEngine initialized (model=%s)", getattr(config, "model", "unknown"))

    # ── Inicialización lazy ───────────────────────────────────────────────

    def _init_swarm(self) -> bool:
        """Intenta inicializar el sistema swarm.

        Returns:
            True si el swarm se inicializó con al menos un agente, False si no.

        """
        if self._swarm is not None:
            # Ya inicializado — verificar que tenga agentes
            return len(self._swarm.list_agents()) > 0

        try:
            from lilith_core.agents.swarm import get_swarm
            from lilith_core.agents.swarm.coordinator import get_coordinator

            self._swarm = get_swarm()
            self._coordinator = get_coordinator()

            # Verificar que haya agentes registrados
            agents = self._swarm.list_agents()
            if agents:
                logger.info(
                    "Swarm initialized with %d agents: %s",
                    len(agents),
                    [a["name"] for a in agents],
                )
                return True
            logger.warning("Swarm initialized but no agents registered — will use LLM fallback")
            return False

        except ImportError:
            logger.debug("lilith_core swarm not available — using LLM fallback")
            return False
        except Exception as exc:
            logger.warning("Failed to init swarm: %s — using LLM fallback", exc)
            return False

    def _ensure_coordinator(self) -> Any:
        """Retorna el coordinator, creando si es necesario."""
        if self._coordinator is None:
            try:
                from lilith_core.agents.swarm.coordinator import get_coordinator

                self._coordinator = get_coordinator()
            except ImportError:
                pass
        return self._coordinator

    # ── Tracing helpers (AgentLens-inspired observability) ──────────────────

    def _traced(
        self,
        name: str,
        **attributes: Any,
    ) -> "_TracingContext":
        """Return a context manager that opens a root trace.

        The wrapper degrades to a no-op when tracing is unavailable
        (e.g. test runs that monkeypatch the tracer) so callers can use
        it unconditionally::

            with self._traced("chat", session_id=...) as span:
                ... do work ...
                if span is not None:
                    span.set_attribute("model", model)

        Returns a :class:`_TracingContext` that always yields either a
        real span or ``None`` — never raises.
        """
        try:
            from lilith_orchestrator.tracing import get_tracer  # type: ignore[import-not-found]

            tracer = get_tracer()
            return _TracingContext(tracer.trace_request(name, **attributes))
        except Exception as exc:  # noqa: BLE001
            logger.debug("tracing skipped for %s: %s", name, exc)
            return _TracingContext(None)

    def _span(
        self,
        kind_name: str,
        name: str,
        **attributes: Any,
    ) -> "_SpanContext":
        """Return a context manager that opens a nested span under the active root.

        ``kind_name`` is matched against :class:`SpanKind` if available;
        otherwise the span is created with kind=``"custom"`` and the
        raw name is preserved in attributes for downstream debugging.
        """
        try:
            from lilith_orchestrator.tracing import (  # type: ignore[import-not-found]
                SpanKind,
                get_tracer,
            )

            tracer = get_tracer()
            kind = SpanKind(kind_name) if kind_name in SpanKind._value2member_map_ else None  # type: ignore[attr-defined]
            if kind is None:
                # Unknown kind — fall back to a string kind the tracer accepts.
                return _SpanContext(_no_op_span(name, kind_name, attributes))
            return _SpanContext(tracer.span(kind, name, **attributes))
        except Exception as exc:  # noqa: BLE001
            logger.debug("span skipped for %s: %s", name, exc)
            return _SpanContext(_no_op_span(name, kind_name, attributes))

    def _trace_stats(self) -> dict[str, Any]:
        """Return a snapshot of tracer state (used by engine.get_stats)."""
        try:
            from lilith_orchestrator.tracing import get_tracer  # type: ignore[import-not-found]

            tracer = get_tracer()
            return {
                "enabled": bool(tracer.is_enabled()),
                "active_traces": int(tracer.active_trace_count()),
                "store_attached": bool(getattr(tracer, "_store", None) is not None),
            }
        except Exception:  # noqa: BLE001
            return {"enabled": False, "active_traces": 0, "store_attached": False}

    # ── Procesamiento principal ────────────────────────────────────────────

    def process(self, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Procesa un mensaje del usuario y retorna la respuesta.

        Hook lifecycle:
            1. on_session_start — fired before processing begins
            2. pre_llm_call / post_llm_call — fired around the LLM call
            3. on_session_end — fired after processing completes

        If on_session_start returns None (aborted), returns an error result.
        If on_session_end returns None, the result is suppressed.

        Args:
            message: Mensaje del usuario.
            context: Contexto adicional (opcional).

        Returns:
            Diccionario con response, usage, tool_call.
        """
        start = time.time()
        self._request_count += 1
        session_id = uuid.uuid4().hex[:12]

        # ── Open the root trace (AgentLens-inspired) ────────────────────────
        # The trace wraps the entire request so dashboards can replay one
        # user message end-to-end. Nested spans below cover routing, the
        # LLM call, and the post-processing quality gate.
        with self._traced(
            "engine.process",
            session_id=session_id,
            model=getattr(self.config, "model", "lilith"),
        ) as root_span:
            return self._process_with_trace(
                message=message,
                context=context,
                session_id=session_id,
                start=start,
                root_span=root_span,
            )

    def _process_with_trace(
        self,
        message: str,
        context: dict[str, Any] | None,
        session_id: str,
        start: float,
        root_span: Any,
    ) -> dict[str, Any]:
        """Inner body of ``process()`` once the root trace is open."""
        if root_span is not None:
            root_span.set_attribute("message_length", len(message or ""))

        # ── on_session_start hook ───────────────────────────────────────────
        start_ctx = HookContext(
            hook_type=HookType.ON_SESSION_START,
            agent_name=getattr(self.config, "model", "lilith"),
            session_id=session_id,
            data={"message": message, "context": dict(context or {})},
        )
        start_result = self._hooks.fire(start_ctx)
        if start_result is None:
            if root_span is not None:
                root_span.set_attribute("aborted_by", "on_session_start")
            return self._normalize_result(
                {
                    "response": "[Lilith] Session aborted by on_session_start hook.",
                    "usage": EngineUsage(),
                    "tool_call": None,
                },
                (time.time() - start) * 1000,
            )

        # Use potentially modified message/context from hook
        effective_message = start_result.data.get("message", message)
        effective_context = start_result.data.get("context", context or {})

        # ── Routing decision (swarm vs LLM fallback) ────────────────────────
        with self._span("agent", "engine.route", session_id=session_id) as route_span:
            has_swarm = self._init_swarm()
            if route_span is not None:
                route_span.set_attribute("has_swarm", has_swarm)
                route_span.set_attribute("coordinator_ready", self._coordinator is not None)

            if has_swarm and self._coordinator is not None:
                if route_span is not None:
                    route_span.set_attribute("path", "swarm")
                result = self._process_swarm_sync(effective_message, effective_context)
            else:
                if route_span is not None:
                    route_span.set_attribute("path", "llm_fallback")
                result = self._process_llm_fallback(effective_message, effective_context, session_id)

        # Registrar métricas
        elapsed_ms = (time.time() - start) * 1000
        self._total_latency_ms += elapsed_ms
        if root_span is not None:
            root_span.set_attribute("latency_ms", round(elapsed_ms, 2))
            root_span.set_attribute("agents_used", result.get("usage", {}).get("agents_used", []) if isinstance(result.get("usage"), dict) else [])

        # Normalizar resultado
        normalized = self._normalize_result(result, elapsed_ms)

        # ── on_session_end hook ─────────────────────────────────────────────
        with self._span("hook", "engine.on_session_end", session_id=session_id):
            end_ctx = HookContext(
                hook_type=HookType.ON_SESSION_END,
                agent_name=getattr(self.config, "model", "lilith"),
                session_id=session_id,
                data={"result": normalized, "message": effective_message},
            )
            end_result = self._hooks.fire(end_ctx)
            if end_result is None:
                # A hook suppressed the result
                normalized["response"] = "[Lilith] Result suppressed by on_session_end hook."
            else:
                normalized = end_result.data.get("result", normalized)

        # ── HeimdallAuditor post-processing quality gate ────────────────────────
        if self._auditor is not None and normalized.get("response"):
            with self._span("gate", "engine.heimdall_audit", session_id=session_id) as gate_span:
                try:
                    audit_result = self._auditor.audit(
                        response=normalized["response"],
                        context={"session_id": session_id, "message": effective_message},
                    )
                    audit_status = audit_result.status.value
                    if gate_span is not None:
                        gate_span.set_attribute("status", audit_status)
                        gate_span.set_attribute("confidence", float(getattr(audit_result, "confidence", 0.0)))
                    if audit_status == "vetoed":
                        normalized["response"] = f"[Lilith] Output vetoed by HeimdallAuditor: {audit_result.reason}"
                        normalized["_audit"] = {"status": "vetoed", "reason": audit_result.reason}
                    elif audit_status == "escalated":
                        normalized["response"] = f"[Lilith] Output escalated: {audit_result.reason}"
                        normalized["_audit"] = {"status": "escalated", "reason": audit_result.reason}
                    else:
                        normalized["_audit"] = {"status": "approved", "confidence": audit_result.confidence}
                except Exception as exc:
                    if gate_span is not None:
                        gate_span.record_exception(str(exc))
                    logger.warning("HeimdallAuditor audit failed: %s", exc)

        # Tag root span with the final response metadata.
        if root_span is not None:
            root_span.set_attribute("response_length", len(str(normalized.get("response", ""))))
            audit_meta = normalized.get("_audit", {}) or {}
            if audit_meta:
                root_span.set_attribute("audit_status", str(audit_meta.get("status", "unknown")))

        return normalized

    async def process_stream(
        self,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Procesa un mensaje en modo streaming, yieldando fragmentos.

        Este es un async generator — se usa con ``async for``:
            async for chunk in engine.process_stream(message):
                print(chunk["response"])

        Args:
            message: Mensaje del usuario.
            context: Contexto adicional (opcional).

        Yields:
            Diccionarios parciales con:
                - response: str — Fragmento de texto.
                - usage: dict | None — Métricas (solo en el último chunk).
                - tool_call: dict | None — Tool call (solo si se detecta).
                - done: bool — True en el último fragmento.

        """
        self._request_count += 1
        start = time.time()
        has_swarm = self._init_swarm()

        if has_swarm and self._coordinator is not None:
            async for chunk in self._process_swarm_stream(message, context or {}):
                yield chunk
        else:
            async for chunk in self._process_llm_stream(message, context or {}):
                yield chunk

        elapsed_ms = (time.time() - start) * 1000
        self._total_latency_ms += elapsed_ms

        # Yield métricas finales
        yield {
            "response": "",
            "usage": {"latency_ms": round(elapsed_ms, 2)},
            "tool_call": None,
            "done": True,
        }

    # ── Procesamiento swarm ────────────────────────────────────────────────

    def _process_swarm_sync(self, message: str, context: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta el procesamiento víaCoordinator (swarm multi-agente) de forma síncrona.

        Internamente corre el event loop para ejecutar la coroutine del Coordinator.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        async def _run() -> dict[str, Any]:
            return await self._process_swarm_async(message, context)

        if loop and loop.is_running():
            # Ya estamos en un event loop — crear tarea y esperar resultado
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _run())
                return future.result(timeout=120)
        else:
            return asyncio.run(_run())

    async def _process_swarm_async(self, message: str, context: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta procesamiento vía swarm de forma asíncrona."""
        try:
            result = await self._coordinator.execute(
                task_description=message,
                context=context,
            )

            # Reconstruir respuesta del CoordinationResult
            agents_used = getattr(result, "agents_used", [])
            response_text = getattr(result, "final_output", "")
            if not response_text:
                response_text = str(getattr(result, "error", "Sin respuesta del swarm"))

            return {
                "response": response_text,
                "usage": EngineUsage(
                    agents_used=agents_used,
                    latency_ms=getattr(result, "execution_time_ms", 0),
                ),
                "tool_call": None,
            }

        except Exception as exc:
            logger.error("Swarm processing failed: %s", exc, exc_info=True)
            self._error_count += 1
            # Fallback a LLM directo
            return self._process_llm_fallback(message, context)

    async def _process_swarm_stream(
        self,
        message: str,
        context: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Streaming vía swarm — por ahora delega al async y yieldea el resultado completo."""
        try:
            result = await self._coordinator.execute(
                task_description=message,
                context=context,
            )

            agents_used = getattr(result, "agents_used", [])
            response_text = getattr(result, "final_output", "")
            if not response_text:
                response_text = str(getattr(result, "error", "Sin respuesta del swarm"))

            yield {
                "response": response_text,
                "usage": EngineUsage(
                    agents_used=agents_used,
                    latency_ms=getattr(result, "execution_time_ms", 0),
                ),
                "tool_call": None,
                "done": False,
            }

        except Exception as exc:
            logger.error("Swarm stream failed: %s", exc, exc_info=True)
            self._error_count += 1
            async for chunk in self._process_llm_stream(message, context):
                yield chunk

    # ── Fallback: llamada LLM directa ──────────────────────────────────────

    def _process_llm_fallback(self, message: str, context: dict[str, Any], session_id: str = "") -> dict[str, Any]:
        """Fallback cuando el swarm no está disponible.

        Usa la configuración del engine para hacer una llamada directa al LLM.
        Fires pre_llm_call and post_llm_call hooks around the LLM call.

        Response caching: when self._cache_enabled is True, identical prompts
        hit the LRU cache instead of going to the provider.
        """
        logger.debug("Using LLM fallback for message: %s", message[:80])

        # ── pre_llm_call hook ───────────────────────────────────────────────
        pre_ctx = HookContext(
            hook_type=HookType.PRE_LLM_CALL,
            agent_name=getattr(self.config, "model", "lilith"),
            session_id=session_id,
            data={"message": message, "context": dict(context)},
        )
        pre_result = self._hooks.fire(pre_ctx)
        if pre_result is None:
            return {
                "response": "[Lilith] LLM call aborted by pre_llm_call hook.",
                "usage": EngineUsage(agents_used=["llm_fallback"]),
                "tool_call": None,
            }

        effective_message = pre_result.data.get("message", message)

        # ── Response cache lookup ───────────────────────────────────────────
        cache_params = {
            "temperature": getattr(self.config, "temperature", 0.7),
            "max_tokens": getattr(self.config, "max_tokens", 2048),
        }
        model_name = getattr(self.config, "model", "gpt-4")
        cache_key = (
            ResponseCache.make_key(model_name, effective_message, cache_params)
            if self._cache_enabled
            else None
        )

        if cache_key is not None and self._response_cache.has(cache_key):
            cached_response = self._response_cache.get(cache_key)
            self._cache_hits += 1
            logger.debug("Cache HIT for message: %s", effective_message[:60])

            post_ctx = HookContext(
                hook_type=HookType.POST_LLM_CALL,
                agent_name=model_name,
                session_id=session_id,
                data={
                    "response": cached_response,
                    "message": effective_message,
                    "from_cache": True,
                },
            )
            post_result = self._hooks.fire(post_ctx)
            if post_result is None:
                cached_response = "[Lilith] Response suppressed by post_llm_call hook."
            else:
                cached_response = post_result.data.get("response", cached_response)

            return {
                "response": cached_response,
                "usage": EngineUsage(agents_used=["llm_fallback_cache"]),
                "tool_call": None,
                "_cached": True,
            }

        if cache_key is not None:
            self._cache_misses += 1

        try:
            client = self._get_llm_client()
            if client is not None:
                response_text = self._call_llm(client, effective_message, context)

                post_ctx = HookContext(
                    hook_type=HookType.POST_LLM_CALL,
                    agent_name=model_name,
                    session_id=session_id,
                    data={"response": response_text, "message": effective_message},
                )
                post_result = self._hooks.fire(post_ctx)
                if post_result is None:
                    response_text = "[Lilith] Response suppressed by post_llm_call hook."
                else:
                    response_text = post_result.data.get("response", response_text)

                if cache_key is not None and response_text:
                    tokens_saved = estimate_tokens(response_text)
                    self._response_cache.put(cache_key, response_text, tokens_saved=tokens_saved)

                if session_id:
                    try:
                        self._token_tracker.record(
                            session_id=session_id,
                            agent_name="llm_fallback",
                            prompt_tokens=estimate_tokens(effective_message),
                            completion_tokens=estimate_tokens(response_text),
                        )
                    except Exception as exc:
                        logger.debug("Token tracking failed (non-fatal): %s", exc)

                return {
                    "response": response_text,
                    "usage": EngineUsage(
                        prompt_tokens=estimate_tokens(effective_message),
                        completion_tokens=estimate_tokens(response_text),
                        total_tokens=estimate_tokens(effective_message)
                        + estimate_tokens(response_text),
                        agents_used=["llm_fallback"],
                    ),
                    "tool_call": None,
                }
        except Exception as exc:
            logger.exception("LLM fallback call failed: %s", exc)
            self._error_count += 1

        return {
            "response": "[Lilith] No pude procesar tu mensaje en este momento. "
            "El sistema swarm y el LLM directo no están disponibles.",
            "usage": EngineUsage(),
            "tool_call": None,
        }

    async def _process_llm_stream(
        self,
        message: str,
        context: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Fallback streaming vía LLM directo."""
        result = self._process_llm_fallback(message, context)
        yield {
            "response": result["response"],
            "usage": result["usage"].to_dict()
            if isinstance(result["usage"], EngineUsage)
            else result["usage"],
            "tool_call": None,
            "done": False,
        }

    def _get_llm_client(self) -> Any:
        """Obtiene un cliente LLM a partir de la configuración.

        Intenta importar y configurar el cliente HTTP para llamadas API
        usando los parámetros de LilithConfig.
        """
        config = self.config

        # Intentar usar LLMClient de lilith_core si está disponible
        try:
            from lilith_core.llm import LLMClient

            return LLMClient(
                model=getattr(config, "model", "gpt-4"),
                base_url=getattr(config, "base_url", None),
                api_key=getattr(config, "api_key", None),
            )
        except ImportError:
            pass

        # Intentar usar el cliente directo de la configuración
        if hasattr(config, "llm_client") and config.llm_client is not None:
            return config.llm_client

        return None

    def _call_llm(self, client: Any, message: str, context: dict[str, Any]) -> str:
        """Realiza una llamada al LLM y retorna la respuesta como texto."""
        import httpx

        # Construir messages
        messages = []
        system_prompt = getattr(self.config, "system_prompt", None) or self._get_system_prompt()

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Inyectar contexto de memoria si está disponible
        if self.memory is not None:
            try:
                recent = self.memory.search(message, limit=5)
                if recent:
                    memory_context = "\n".join(f"- {m.get('content', '')}" for m in recent[:5])
                    messages.append(
                        {
                            "role": "system",
                            "content": f"Contexto relevante de memoria:\n{memory_context}",
                        },
                    )
            except Exception as exc:
                logger.debug("Memory lookup failed (non-fatal): %s", exc)

        # Contexto adicional del caller
        if context:
            context_str = "\n".join(f"- {k}: {v}" for k, v in context.items())
            messages.append(
                {
                    "role": "system",
                    "content": f"Contexto adicional:\n{context_str}",
                },
            )

        messages.append({"role": "user", "content": message})

        model = getattr(self.config, "model", "gpt-4")
        base_url = getattr(self.config, "base_url", "http://localhost:1234/v1")
        api_key = getattr(self.config, "api_key", "lm-studio")
        max_tokens = getattr(self.config, "max_tokens", 2048)
        temperature = getattr(self.config, "temperature", 0.7)

        # Si el cliente tiene un método chat, usarlo
        if hasattr(client, "chat"):
            return client.chat(message)

        # Si tiene un método generate, usarlo
        if hasattr(client, "generate"):
            return client.generate(message)

        # Fallback: llamada HTTP directa (estilo OpenAI-compatible)
        try:
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }

            resp = httpx.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
                timeout=60.0,
            )
            resp.raise_for_status()
            data = resp.json()

            # Extraer respuesta
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                return content or "(sin respuesta del modelo)"
            return "(sin respuesta del modelo)"

        except Exception as exc:
            logger.exception("Direct LLM call failed: %s", exc)
            raise

    def _get_system_prompt(self) -> str:
        """Obtiene el system prompt por defecto."""
        try:
            from lilith_core.config import SYSTEM_PROMPT

            return SYSTEM_PROMPT
        except ImportError:
            pass

        return (
            "Eres Lilith, una asistente inteligente y versátil. "
            "Respondes en el idioma del usuario con claridad y precisión."
        )

    # ── Normalización de resultados ───────────────────────────────────────

    def _normalize_result(self, result: dict[str, Any], elapsed_ms: float) -> dict[str, Any]:
        """Asegura que el resultado siempre tenga la estructura esperada."""
        if not isinstance(result, dict):
            result = {"response": str(result)}

        # response
        response = result.get("response", "")
        if response is None:
            response = "(sin respuesta)"
        result["response"] = str(response)

        # usage
        raw_usage = result.get("usage", {})
        if isinstance(raw_usage, EngineUsage):
            result["usage"] = raw_usage.to_dict()
        elif isinstance(raw_usage, dict):
            raw_usage.setdefault("latency_ms", round(elapsed_ms, 2))
            result["usage"] = raw_usage
        else:
            result["usage"] = {"latency_ms": round(elapsed_ms, 2)}

        # tool_call
        result.setdefault("tool_call", None)

        # context (para compatibilidad con lilith-api)
        result.setdefault("context", [])

        return result

    # ── Cache control ───────────────────────────────────────────────────────

    def enable_cache(self) -> None:
        """Enable the response cache (Talon-style LRU+TTL).

        When enabled, identical (model, prompt, params) requests are served
        from cache instead of hitting the LLM provider. Disabled by default.
        """
        self._cache_enabled = True
        logger.info("Response cache enabled (size=%d, ttl=%.0fs)",
                    self._response_cache.max_size,
                    self._response_cache.ttl_seconds)

    def disable_cache(self) -> None:
        """Disable the response cache. Existing entries are preserved."""
        self._cache_enabled = False
        logger.info("Response cache disabled (entries preserved: %d)",
                    self._response_cache.size)

    def clear_cache(self) -> None:
        """Clear all cache entries and reset hit/miss counters."""
        self._response_cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0
        logger.info("Response cache cleared")

    # ── Utilidades ──────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Obtiene estadísticas del engine."""
        avg_latency = self._total_latency_ms / self._request_count if self._request_count > 0 else 0
        total_cache_ops = self._cache_hits + self._cache_misses
        return {
            "total_requests": self._request_count,
            "total_errors": self._error_count,
            "avg_latency_ms": round(avg_latency, 2),
            "swarm_available": self._swarm is not None,
            "memory_available": self.memory is not None,
            "model": getattr(self.config, "model", "unknown"),
            "cache": {
                "enabled": self._cache_enabled,
                "size": self._response_cache.size,
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "hit_rate": (
                    self._cache_hits / total_cache_ops if total_cache_ops > 0 else 0.0
                ),
            },
            "tracing": self._trace_stats(),
        }

    def reset_stats(self) -> None:
        """Reinicia contadores de métricas."""
        self._request_count = 0
        self._error_count = 0
        self._total_latency_ms = 0.0
        self._cache_hits = 0
        self._cache_misses = 0

    # ── Workflow Integration ────────────────────────────────────────────────

    def process_workflow(
        self,
        yaml_string: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a YAML workflow through the orchestrator pipeline.

        Integrates WorkflowEngine with TaskDispatcher for agent-aware step
        execution. Each step is dispatched to the best-matching agent based
        on its intent and required tools.

        Hook lifecycle per step:
            1. pre_llm_call — fired before each step's LLM call
            2. post_llm_call — fired after each step's LLM response

        Args:
            yaml_string: YAML workflow definition.
            context: Initial workflow context (available to all steps).

        Returns:
            Dict with:
                - workflow_result: WorkflowResult.to_dict()
                - success: bool
                - final_output: str (last step's output)
                - steps_completed: int
                - total_duration_ms: float
        """
        from lilith_orchestrator.dispatch import TaskDispatcher
        from lilith_orchestrator.workflow import (
            WorkflowEngine,
            WorkflowResult,
            WorkflowStatus,
        )

        session_id = uuid.uuid4().hex[:12]
        start = time.time()

        # Create workflow engine and parse YAML
        wf_engine = WorkflowEngine()

        try:
            workflow = wf_engine.parse_yaml(yaml_string)
        except ValueError as exc:
            logger.error("Workflow parse failed: %s", exc)
            return {
                "workflow_result": None,
                "success": False,
                "final_output": f"[Lilith] Workflow parse error: {exc}",
                "steps_completed": 0,
                "total_duration_ms": (time.time() - start) * 1000,
                "error": str(exc),
            }

        # Set up TaskDispatcher if agent cards are available
        dispatcher = self._create_dispatcher()

        # Register executors for each intent
        self._register_workflow_executors(wf_engine, dispatcher, session_id)

        # Run the workflow
        wf_result = wf_engine.run(workflow, context=context)

        elapsed_ms = (time.time() - start) * 1000
        self._total_latency_ms += elapsed_ms
        self._request_count += 1

        # Fire on_session_end hook
        end_ctx = HookContext(
            hook_type=HookType.ON_SESSION_END,
            agent_name=getattr(self.config, "model", "lilith"),
            session_id=session_id,
            data={"workflow_result": wf_result.to_dict()},
        )
        self._hooks.fire(end_ctx)

        return {
            "workflow_result": wf_result.to_dict(),
            "success": wf_result.success,
            "final_output": wf_result.final_output,
            "steps_completed": len([
                s for s in wf_result.steps
                if s.status.value in ("passed", "skipped")
            ]),
            "total_duration_ms": round(elapsed_ms, 2),
        }

    def _create_dispatcher(self) -> Any:
        """Create a TaskDispatcher from Vanaheim agent cards if available."""
        try:
            from lilith_skills.agent_cards import AgentCardLoader
            from lilith_skills.agent_registry import AgentRegistry
            from lilith_orchestrator.dispatch import TaskDispatcher

            import os
            ygg_root = os.environ.get("YGGDRASIL_ROOT", "")
            if not ygg_root:
                # Try common locations
                for candidate in [
                    os.path.expanduser("~/Yggdrasil"),
                    os.path.join(os.path.dirname(__file__), "..", "..", ".."),
                ]:
                    if os.path.isdir(os.path.join(candidate, "Vanaheim")):
                        ygg_root = candidate
                        break

            if ygg_root:
                loader = AgentCardLoader.from_vanaheim(ygg_root)
                registry = AgentRegistry(loader)
                return TaskDispatcher(registry)
        except (ImportError, Exception) as exc:
            logger.debug("TaskDispatcher not available: %s", exc)
        return None

    def _register_workflow_executors(
        self,
        wf_engine: Any,
        dispatcher: Any,
        session_id: str,
    ) -> None:
        """Register intent-based executors on the workflow engine.

        Each executor dispatches to the appropriate agent via TaskDispatcher
        and executes the LLM call with hooks.
        """
        intents = ("code", "research", "creative", "debug", "chat")

        for intent in intents:
            def make_executor(intent_name: str) -> Any:
                def executor(step: Any, ctx: dict[str, Any]) -> tuple[str, str]:
                    # Select agent via dispatcher
                    agent_name = f"auto({intent_name})"
                    if dispatcher is not None:
                        card = dispatcher.route(
                            intent=intent_name,
                            required_tools=step.tools or None,
                        )
                        if card:
                            agent_name = card.name

                    # Build prompt from step
                    input_content = ctx.get("last_output", "")
                    if step.input_key:
                        input_content = ctx.get(step.input_key, input_content)

                    parts = []
                    if step.description:
                        parts.append(f"Task: {step.description}")
                    if input_content:
                        parts.append(f"Input: {input_content}")
                    prompt = "\n\n".join(parts) if parts else step.name

                    # Fire pre_llm_call hook
                    pre_ctx = HookContext(
                        hook_type=HookType.PRE_LLM_CALL,
                        agent_name=agent_name,
                        session_id=session_id,
                        data={
                            "message": prompt,
                            "step": step.name,
                            "intent": intent_name,
                        },
                    )
                    pre_result = self._hooks.fire(pre_ctx)
                    if pre_result is None:
                        return f"[Step '{step.name}' aborted by hook]", agent_name

                    effective_prompt = pre_result.data.get("message", prompt)

                    # Execute LLM call (reuse existing fallback path)
                    try:
                        result = self._process_llm_fallback(
                            effective_prompt,
                            {"step": step.name, "intent": intent_name, **ctx},
                            session_id,
                        )
                        output = result.get("response", "")
                    except Exception as exc:
                        logger.warning("Step '%s' LLM call failed: %s", step.name, exc)
                        output = f"[Step '{step.name}' error: {exc}]"

                    # Fire post_llm_call hook
                    post_ctx = HookContext(
                        hook_type=HookType.POST_LLM_CALL,
                        agent_name=agent_name,
                        session_id=session_id,
                        data={
                            "response": output,
                            "step": step.name,
                            "intent": intent_name,
                        },
                    )
                    post_result = self._hooks.fire(post_ctx)
                    if post_result is None:
                        output = f"[Step '{step.name}' response suppressed by hook]"
                    else:
                        output = post_result.data.get("response", output)

                    return output, agent_name

                return executor

            wf_engine.register_executor(intent, make_executor(intent))

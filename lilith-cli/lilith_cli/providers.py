"""Unified LLM provider wrapper for Yggdrasil CLI v6.0.

Uses httpx directly for OpenAI-compatible endpoints (fast, lightweight),
with optional litellm fallback for non-OpenAI providers (Anthropic, etc.).
Streaming, tool-calling, and exponential-backoff retry included.

"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import httpx

from .unicode_safety import sanitize_unicode

if TYPE_CHECKING:
    from .config import YggdrasilConfig


logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────

_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds
# El cliente espera MAS que el adaptador del Fabric (300s) a proposito. Si
# cediera antes, el error que veria el operador seria un timeout opaco del
# cliente en vez del diagnostico del adaptador, que si dice por que fallo el
# proveedor. Quien esta mas cerca del fallo debe ser quien lo reporte.
_REQUEST_TIMEOUT = 330.0  # seconds; adaptador del Fabric = 300s


# ── Pricing (v4.3.1) ────────────────────────────────────────────────
# Cost per 1M tokens (input, output) in USD. Used to estimate per-call
# and total cost in the REPL bottom toolbar. Providers not listed here
# fall back to 0.0 (cost hidden).
# Sources: published provider pricing pages, last refreshed 2026-07-09.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Anthropic (in case litellm is used)
    "claude-sonnet-4":        (3.0, 15.0),
    "claude-opus-4":          (15.0, 75.0),
    "claude-opus-5":          (10.0, 50.0),
    "claude-haiku-4":         (0.8, 4.0),
    # OpenAI (in case litellm is used)
    "gpt-4o":                 (2.5, 10.0),
    "gpt-4o-mini":            (0.15, 0.6),
    "o3":                     (15.0, 60.0),
    # Kimi
    "kimi-for-coding":        (1.0, 3.0),
    "moonshot-v1-128k":       (2.0, 2.0),
    # BytePlus
    "seed-1-6-250915":        (0.84, 1.68),
    "glm-4-7-251222":         (0.7, 0.7),
    # xAI
    "grok-4.20-0309-non-reasoning":  (3.0, 9.0),
    "grok-4":                 (3.0, 9.0),
    "grok-3":                 (3.0, 9.0),
    # Local
    "local-model":            (0.0, 0.0),
}


# ── Context windows (v4.3.1) ───────────────────────────────────────
# Approximate context-window sizes in tokens. Used for the /context
# progress bar. Unknown models fall back to 128K (common default).
_MODEL_CONTEXTS: dict[str, int] = {
    # Anthropic (in case litellm is used)
    "claude-sonnet-4": 200_000,
    "claude-opus-4": 200_000,
    "claude-haiku-4": 200_000,
    # OpenAI (in case litellm is used)
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "o3": 200_000,
    # Kimi
    "k3": 1_048_576,
    "kimi-for-coding": 256_000,
    "moonshot-v1-128k": 128_000,
    # BytePlus
    "seed-1-6-250915": 128_000,
    "glm-4-7-251222": 128_000,
    # xAI
    "grok-4.20-0309-non-reasoning": 131_072,
    "grok-4": 131_072,
    "grok-3": 131_072,
    # Local
    "local-model": 128_000,
}

_DEFAULT_CONTEXT_WINDOW = 128_000


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost for a single LLM call.

    Falls back to 0.0 for unknown models (so unknown providers don't
    crash the cost display). Returns 0.0 for the local model.
    """
    rate = _MODEL_PRICING.get(model)
    if rate is None:
        return 0.0
    input_rate, output_rate = rate
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000


# ── Context windows (v4.3.1) ─────────────────────────────────────────
# Approximate maximum context window per model, in tokens. Used by the
# /context command to show a progress bar of how much of the model's
# context is in use. Defaults to 32k when a model is unknown.
_MODEL_CONTEXTS: dict[str, int] = {
    "claude-sonnet-4": 200_000,
    "claude-opus-4": 200_000,
    "claude-opus-5": 1_000_000,
    "claude-haiku-4": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "o3": 200_000,
    "k3": 1_048_576,
    "kimi-for-coding": 128_000,
    "moonshot-v1-128k": 128_000,
    "seed-1-6-250915": 128_000,
    "glm-4-7-251222": 128_000,
    "grok-4.20-0309-non-reasoning": 128_000,
    "grok-4": 128_000,
    "grok-3": 128_000,
    "local-model": 32_000,
}
_DEFAULT_CONTEXT_WINDOW = 32_000


def estimate_context_window(model: str | None) -> int:
    """Return the approximate max context window (in tokens) for *model*.

    Falls back to a safe default of 32k for unknown models so the
    progress bar in /context doesn't crash.
    """
    if not model:
        return _DEFAULT_CONTEXT_WINDOW
    return _MODEL_CONTEXTS.get(model, _DEFAULT_CONTEXT_WINDOW)


# ── Provider factory ────────────────────────────────────────────────


def create_provider(config: YggdrasilConfig) -> LLMProviderWrapper:
    """Instantiate the appropriate :class:`LLMProviderWrapper`."""
    return LLMProviderWrapper(config)


# ── Tool-call dataclasses ───────────────────────────────────────────


class ToolCall:
    """Represents a single function-call returned by the LLM."""

    __slots__ = ("arguments", "id", "name")

    def __init__(self, id: str, name: str, arguments: dict[str, Any]) -> None:
        self.id = id
        self.name = name
        self.arguments = arguments

    def __repr__(self) -> str:
        return f"ToolCall(id={self.id!r}, name={self.name!r})"


class ToolResult:
    """Result from executing a tool call."""

    __slots__ = ("content", "name", "tool_call_id")

    def __init__(self, tool_call_id: str, name: str, content: str) -> None:
        self.tool_call_id = tool_call_id
        self.name = name
        self.content = content

    def to_openai_message(self) -> dict[str, Any]:
        """Format this tool result as an OpenAI tool message."""
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": self.content,
        }


# ── Main wrapper ────────────────────────────────────────────────────


class LLMProviderWrapper:
    """High-level provider with streaming, tool-calling, and retry.

    Uses httpx directly for OpenAI-compatible endpoints (fast, no deps).
    Falls back to litellm for Anthropic/Ollama/etc. if available.
    """

    def __init__(self, config: YggdrasilConfig) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None
        from .provider_health import ProviderHealthRegistry

        self._health = ProviderHealthRegistry()

    def _profile(self) -> Any:
        return self.config.providers.get(self.config.provider.lower())

    def _ensure_provider_available(self, *, bypass_circuit: bool = False) -> None:
        """Reject disabled or temporarily-open provider profiles early."""
        from .provider_health import ProviderCircuitOpenError

        profile = self._profile()
        name = self.config.provider.lower()
        # Ad-hoc configs used by callers/tests may not define a profile.
        # Circuit policy belongs to an explicit profile, never to a guessed one.
        if profile is None:
            return
        if profile is not None and not getattr(profile, "enabled", True):
            raise ProviderCircuitOpenError(f"provider '{name}' esta deshabilitado")
        if not bypass_circuit and not self._health.allow(name):
            state = self._health.get(name)
            wait = max(0, int(float(state.get("opened_until", 0)) - time.time()))
            raise ProviderCircuitOpenError(
                f"circuito de provider '{name}' abierto; reintento en {wait}s"
            )

    def _record_provider_failure(self, exc: BaseException) -> None:
        profile = self._profile()
        if profile is None:
            return
        status = (
            exc.response.status_code
            if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None
            else None
        )
        self._health.record_failure(
            self.config.provider,
            exc,
            threshold=int(getattr(profile, "circuit_breaker_failures", 2)),
            cooldown_seconds=float(getattr(profile, "circuit_breaker_cooldown", 60.0)),
            permanent=status in {401, 403},
        )

    # ── HTTP client ─────────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            api_key = self._resolve_api_key()
            base_url = self._resolve_base_url() or "https://api.openai.com/v1"

            # ── Anthropic-compat profiles (m2/minimax, kimi-future-*) ──
            # Detected by `/anthropic` suffix in the base_url. We rewrite
            # auth from Bearer → X-Api-Key + anthropic-version. Endpoint
            # dispatch happens in _do_complete via _is_anthropic().
            if "/anthropic" in base_url.lower() and api_key:
                headers["X-Api-Key"] = api_key
                headers["anthropic-version"] = "2023-06-01"
                # Drop Bearer to avoid leaking the key through both schemes.
                headers.pop("Authorization", None)
            elif api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            self._client = httpx.AsyncClient(
                base_url=base_url,
                headers=headers,
                timeout=httpx.Timeout(_REQUEST_TIMEOUT),
            )
        return self._client

    def _is_anthropic(self) -> bool:
        """True when the active base URL targets the Anthropic Messages API."""
        base = self._resolve_base_url() or ""
        return "/anthropic" in base.lower()

    # ── Public helpers ──────────────────────────────────────────────

    def _resolve_base_url(self) -> str | None:
        """Resolve base URL considering per-provider profile overrides."""
        profile = self.config.providers.get(self.config.provider.lower())
        if profile and profile.base_url:
            return profile.base_url
        return self.config.base_url

    def _resolve_api_key(self) -> str | None:
        """Resolve API key considering per-provider profile overrides."""
        profile = self.config.providers.get(self.config.provider.lower())
        if profile and profile.api_key:
            return profile.api_key
        return self.config.api_key

    def _resolve_model(self) -> str:
        """Return the model name considering per-provider profile overrides."""
        profile = self.config.providers.get(self.config.provider.lower())
        if profile and profile.model:
            return profile.model
        return self.config.model

    def _resolve_max_tokens(self, kwargs: dict[str, Any] | None = None) -> int | None:
        """Resolve output-token limit: explicit call > provider > global."""
        if kwargs and kwargs.get("max_tokens") is not None:
            return int(kwargs["max_tokens"])
        profile = self.config.providers.get(self.config.provider.lower())
        if profile and profile.max_tokens is not None:
            return profile.max_tokens
        return self.config.max_tokens


    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send messages and return a standardised response dict.

        Retries up to ``config.retry_max`` times with exponential back-off
        and jitter on transient HTTP failures (429, 5xx, connection
        resets, timeouts). Honours the ``Retry-After`` response header
        when present. Non-transient failures (4xx other than 429) are
        surfaced immediately without burning retries.
        """
        messages = sanitize_unicode(messages)
        tools = sanitize_unicode(tools)
        bypass_circuit = bool(kwargs.pop("bypass_circuit", False))
        if self.config.provider.lower() == "fabric":
            return await self._fabric_complete(
                messages,
                tools=tools,
                response_format=kwargs.get("response_format"),
                preferred_model=model,
            )
        model = model or self._resolve_model()

        async def _attempt() -> dict[str, Any]:
            return await self._do_complete(model, messages, tools=tools, **kwargs)

        return await self._run_with_retry(
            _attempt, op_label="LLM call", bypass_circuit=bypass_circuit
        )

    async def _fabric_complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        response_format: dict[str, Any] | None,
        preferred_model: str | None = None,
    ) -> dict[str, Any]:
        """Route one structured agent turn through the local Fabric.

        The Fabric owns failover, so this boundary deliberately performs one
        HTTP request and never retries. Tool execution remains inside Lilith.
        """
        token_env = self.config.fabric_token_env
        token = os.environ.get(token_env)
        if not token:
            raise RuntimeError(f"Fabric requiere la variable de entorno {token_env}")
        from .provider_health import ProviderCircuitOpenError

        if not self._health.allow("fabric"):
            state = self._health.get("fabric")
            raise ProviderCircuitOpenError(
                "Fabric circuit open until " + str(state.get("opened_until") or "unknown")
            )
        headers = {"Content-Type": "application/json", "x-yggdrasil-token": token}
        payload = {
            "client": "lilith",
            "capability": self.config.fabric_capability,
            "request_id": str(uuid.uuid4()),
            "payload": {
                "input": "",
                "messages": messages,
                "tools": tools,
                "response_format": response_format,
            },
            "estimated_tokens": max(0, sum(len(str(m.get("content", ""))) for m in messages) // 4),
            "tags": ["interactive-agent", "structured-tools"],
        }
        requested_model = preferred_model
        if requested_model is None:
            profile = self._profile()
            if profile is not None and profile.model:
                requested_model = profile.model
            elif "model" in self.config.model_fields_set:
                requested_model = self.config.model
        if requested_model and requested_model.strip().lower() != "router":
            payload["preferred_model"] = requested_model.strip()
        payload["payload"] = {k: v for k, v in payload["payload"].items() if v is not None}
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(_REQUEST_TIMEOUT)) as client:
                response = await client.post(
                    f"{self.config.fabric_url.rstrip('/')}/v1/inference",
                    headers=headers,
                    json=payload,
                )
            response.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            self._health.record_failure("fabric", exc)
            raise
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        body = response.json()
        turn = body.get("response")
        if not isinstance(turn, dict):
            raise RuntimeError("Fabric devolvió una respuesta sin turno estructurado")
        parsed_calls: list[ToolCall] = []
        for raw in turn.get("tool_calls") or []:
            if not isinstance(raw, dict) or not isinstance(raw.get("function"), dict):
                continue
            function = raw["function"]
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Fabric devolvió argumentos de herramienta inválidos") from exc
            if not isinstance(arguments, dict):
                raise RuntimeError("Fabric devolvió argumentos de herramienta no estructurados")
            parsed_calls.append(ToolCall(str(raw.get("id") or uuid.uuid4()), str(function.get("name", "")), arguments))
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        total = int(usage.get("tokens_used", 0) or 0)
        try:
            actual_cost = float(usage.get("cost_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            actual_cost = 0.0
        route_model = body.get("model")
        route_provider = str(body.get("provider") or "unknown")
        account_id = str(body.get("account_id") or "").strip()
        self._health.record_success("fabric", latency_ms=latency_ms)
        if account_id:
            self._health.record_success(
                f"fabric-account:{route_provider}:{account_id}", latency_ms=latency_ms
            )
        return {
            "content": str(turn.get("text", "")),
            "reasoning_content": turn.get("reasoning_content"),
            "tool_calls": parsed_calls,
            "usage": {
                "prompt_tokens": 0, "completion_tokens": total,
                "total_tokens": total, "cost_usd": actual_cost,
            },
            "finish_reason": turn.get("finish_reason") or ("tool_calls" if parsed_calls else "stop"),
            "model": str(route_model) if route_model is not None else "fabric-routed",
            "route": {
                "provider": body.get("provider"), "account_id": body.get("account_id"),
                "runtime": body.get("runtime"), "attempts": usage.get("attempts"),
                "fallback_used": body.get("fallback_used"),
            },
        }

    async def _run_with_retry(
        self,
        attempt_fn,
        *,
        op_label: str = "LLM call",
        bypass_circuit: bool = False,
    ) -> Any:
        """Run ``attempt_fn()`` with retry/backoff on transient HTTP failures.

        ``attempt_fn`` is a zero-arg async callable invoked once per
        attempt. On transient HTTP failures (429, 5xx, connection resets,
        timeouts) the call is retried up to ``config.retry_max`` times
        with exponential back-off + jitter, honouring the ``Retry-After``
        header when present. Non-transient failures (4xx other than 429,
        programming errors) are surfaced immediately. When the budget is
        exhausted, a ``RuntimeError`` carrying the last status code and
        exception detail is raised.

        The log format is the canonical "retry N/M tras HTTP X de <base_url>
        en Xs" line — used by both ``complete()`` and ``stream()`` so an
        operator scanning logs sees a consistent shape regardless of the
        caller.
        """
        self._ensure_provider_available(bypass_circuit=bypass_circuit)
        last_exc: Exception | None = None
        last_response: httpx.Response | None = None

        retry_max = max(0, int(getattr(self.config, "retry_max", _MAX_RETRIES)))
        base = float(getattr(self.config, "retry_backoff_base", _BASE_DELAY))
        backoff_max = float(getattr(self.config, "retry_backoff_max", 30.0))
        jitter = float(getattr(self.config, "retry_jitter", 0.25))
        base_url = self._resolve_base_url() or ""

        for attempt in range(1, retry_max + 2):  # 1 initial + retry_max retries
            try:
                started = time.perf_counter()
                result = await attempt_fn()
                if self._profile() is not None:
                    self._health.record_success(
                        self.config.provider,
                        int((time.perf_counter() - started) * 1000),
                    )
                return result
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                last_response = exc.response
                status = exc.response.status_code
                if not self._is_retryable_status(status):
                    # Deterministic client error (e.g. 400, 401, 403, 404,
                    # 422). Retrying would just burn the budget.
                    logger.warning(
                        "Attempt %d: non-retryable HTTP %d — surfacing immediately",
                        attempt,
                        status,
                    )
                    self._record_provider_failure(exc)
                    raise
                if attempt > retry_max:
                    logger.warning(
                        "Attempt %d: HTTP %d — giving up after %d retries",
                        attempt,
                        status,
                        retry_max,
                    )
                    break
                delay = self._compute_retry_delay(
                    attempt, base, backoff_max, jitter, exc.response
                )
                logger.warning(
                    "%s: retry %d/%d tras HTTP %d de %s en %.2fs",
                    op_label,
                    attempt,
                    retry_max,
                    status,
                    base_url,
                    delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.TimeoutException,
                httpx.NetworkError,
            ) as exc:
                last_exc = exc
                last_response = None
                if attempt > retry_max:
                    logger.warning(
                        "Attempt %d: %s — giving up after %d retries",
                        attempt,
                        type(exc).__name__,
                        retry_max,
                    )
                    break
                delay = self._compute_retry_delay(
                    attempt, base, backoff_max, jitter, None
                )
                logger.warning(
                    "%s: retry %d/%d tras %s de %s en %.2fs",
                    op_label,
                    attempt,
                    retry_max,
                    type(exc).__name__,
                    base_url,
                    delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
            except Exception as exc:
                # Programming errors (TypeError, KeyError, json decode,
                # etc.) and anything else that isn't an HTTP/transport
                # failure: surface immediately, do NOT retry.
                logger.warning(
                    "Attempt %d: non-retryable error %s: %s",
                    attempt,
                    type(exc).__name__,
                    exc,
                )
                self._record_provider_failure(exc)
                raise

        status_part = (
            f" (HTTP {last_response.status_code})" if last_response is not None else ""
        )
        failure = RuntimeError(
            f"{op_label} failed after {retry_max} retries{status_part}: {last_exc}"
        )
        self._record_provider_failure(last_exc or failure)
        raise failure

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        """Decide whether an HTTP status is worth retrying.

        Per the de-facto convention: 429 (rate limit) and 5xx (server
        errors) are transient. Other 4xx codes (400, 401, 403, 404,
        422, …) reflect deterministic client mistakes — retrying
        without changing the request is pointless and just burns
        budget.
        """
        if status_code == 429:
            return True
        if 500 <= status_code < 600:
            return True
        return False

    @staticmethod
    def _compute_retry_delay(
        attempt: int,
        base: float,
        backoff_max: float,
        jitter: float,
        response: httpx.Response | None,
    ) -> float:
        """Compute the sleep before the next retry.

        Honours the ``Retry-After`` header (seconds form; HTTP-date is
        ignored because it's brittle across clocks). When absent,
        applies ``base * 2 ** (attempt-1)`` with optional multiplicative
        jitter and a hard ceiling at ``backoff_max``.
        """
        retry_after = None
        if response is not None:
            ra = response.headers.get("Retry-After") or response.headers.get(
                "retry-after"
            )
            if ra:
                try:
                    retry_after = float(ra)
                except (TypeError, ValueError):
                    # HTTP-date form (e.g. "Wed, 21 Oct 2015 07:28:00 GMT")
                    # is intentionally ignored — clock skew between the
                    # client and provider makes it unreliable.
                    retry_after = None

        if retry_after is not None and retry_after > 0:
            # Honour Retry-After only when it carries real information
            # (> 0). "Retry-After: 0" degenerates to the exponential
            # back-off below. Cap at backoff_max so a malicious or buggy
            # server can't lock us out for hours.
            return min(retry_after, backoff_max)

        # Exponential back-off: base * 2 ** (attempt - 1).
        delay = base * (2 ** max(0, attempt - 1))
        delay = min(delay, backoff_max)
        # Multiplicative jitter in [1 - j, 1 + j].
        if jitter > 0:
            spread = 1.0 + (random.uniform(-jitter, jitter))
            delay = max(0.0, delay * spread)
        return delay

    # ── Core interface: stream ───────────────────────────────────────

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream text chunks from the LLM.

        Yields dicts with keys:
          content (str), finish_reason (str|None), tool_calls (list|None)
        """
        messages = sanitize_unicode(messages)
        tools = sanitize_unicode(tools)
        bypass_circuit = bool(kwargs.pop("bypass_circuit", False))
        if self.config.provider.lower() == "fabric":
            result = await self._fabric_complete(
                messages,
                tools=tools,
                response_format=kwargs.get("response_format"),
                preferred_model=model,
            )
            stream_calls = [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in result["tool_calls"]
            ]
            yield {
                "content": result["content"],
                "reasoning": result.get("reasoning_content"),
                "tool_calls": stream_calls,
                "usage": result["usage"],
                "finish_reason": result["finish_reason"],
                "model": result["model"],
            }
            return
        model = model or self._resolve_model()

        # ── Anthropic-compatible profiles don't speak the
        # OpenAI SSE protocol this method implements; fall back to the
        # non-streaming path and emit the result as a single chunk.
        # The fallback is wrapped in _run_with_retry so a transient 5xx
        # from Anthropic also gets the same backoff that the
        # OpenAI SSE path below enjoys; non-transient errors bubble up.
        if self._is_anthropic():

            async def _fallback_attempt() -> dict[str, Any]:
                return await self._do_complete(model, messages, tools=tools, **kwargs)

            result = await self._run_with_retry(
                _fallback_attempt,
                op_label="stream() (Anthropic fallback)",
                bypass_circuit=bypass_circuit,
            )
            reasoning = result.get("reasoning_content")
            if reasoning:
                yield {
                    "type": "reasoning",
                    "content": reasoning,
                    "finish_reason": None,
                    "tool_calls": None,
                }
            tcs = [
                tc if isinstance(tc, dict) else {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in (result.get("tool_calls") or [])
            ]
            yield {
                "content": result.get("content", ""),
                "finish_reason": result.get("finish_reason", "stop"),
                "tool_calls": tcs or None,
            }
            return

        # — OpenAI SSE path. The HTTP+parsing work lives in the
        # private generator _stream_openai_sse() so stream() can wrap
        # it with retry. We retry only BEFORE the first chunk is
        # yielded: once the caller has consumed anything we cannot
        # resume a half-streamed response safely, so transient
        # failures past that point propagate with a clear message.

        self._ensure_provider_available(bypass_circuit=bypass_circuit)
        client = await self._get_client()

        # ── Kimi quirk: temperature=1 is the only value this model accepts ──
        # Same guard as _do_complete; kimi-for-coding 400s on anything else.
        if "kimi.com" in (self._resolve_base_url() or "").lower():
            kwargs = dict(kwargs)
            kwargs["temperature"] = 1.0

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": kwargs.get("temperature", self.config.temperature),
        }
        max_tokens = self._resolve_max_tokens(kwargs)
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
        profile = self._profile()
        thinking = getattr(profile, "thinking_enabled", None)
        if thinking is not None and "deepseek" in (self._resolve_base_url() or "").lower():
            payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        response_format = kwargs.get("response_format")
        if response_format:
            payload["response_format"] = response_format
        payload = sanitize_unicode(payload)

        # Local attempt counter so retries don't leak across stream()
        # calls (and across concurrent calls) via shared state. The
        # counter resets at zero for every new stream() invocation.
        attempt = 0
        first_chunk_emitted = False
        stream_started = time.perf_counter()
        retry_max = max(0, int(getattr(self.config, "retry_max", _MAX_RETRIES)))
        base_url = self._resolve_base_url() or ""

        while True:
            try:
                async for chunk in self._stream_openai_sse(client, payload):
                    first_chunk_emitted = True
                    yield chunk
                if self._profile() is not None:
                    self._health.record_success(
                        self.config.provider,
                        int((time.perf_counter() - stream_started) * 1000),
                    )
                return  # generator exhausted normally
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if first_chunk_emitted:
                    # Cannot resume a partial SSE stream ── surface
                    # the failure with provider+status context so the
                    # REPL can show a useful message instead of a raw
                    # traceback from response.raise_for_status().
                    self._record_provider_failure(exc)
                    raise RuntimeError(
                        f"stream() from {base_url} aborted mid-stream "
                        f"with HTTP {status}: {exc}"
                    ) from exc
                # Pre-first-chunk: only retry transient statuses.
                if not self._is_retryable_status(status):
                    self._record_provider_failure(exc)
                    raise
                attempt += 1
                if attempt > retry_max:
                    self._record_provider_failure(exc)
                    raise RuntimeError(
                        f"stream() from {base_url} failed after "
                        f"{retry_max} retries (HTTP {status}): {exc}"
                    ) from exc
                delay = self._compute_retry_delay(
                    attempt,
                    float(getattr(self.config, "retry_backoff_base", _BASE_DELAY)),
                    float(getattr(self.config, "retry_backoff_max", 30.0)),
                    float(getattr(self.config, "retry_jitter", 0.25)),
                    exc.response,
                )
                logger.warning(
                    "stream(): retry %d/%d tras HTTP %d de %s en %.2fs",
                    attempt,
                    retry_max,
                    status,
                    base_url,
                    delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                continue
            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.TimeoutException,
                httpx.NetworkError,
            ) as exc:
                if first_chunk_emitted:
                    self._record_provider_failure(exc)
                    raise RuntimeError(
                        f"stream() from {base_url} aborted mid-stream "
                        f"with {type(exc).__name__}: {exc}"
                    ) from exc
                attempt += 1
                if attempt > retry_max:
                    self._record_provider_failure(exc)
                    raise RuntimeError(
                        f"stream() from {base_url} failed after "
                        f"{retry_max} retries ({type(exc).__name__}): {exc}"
                    ) from exc
                delay = self._compute_retry_delay(
                    attempt,
                    float(getattr(self.config, "retry_backoff_base", _BASE_DELAY)),
                    float(getattr(self.config, "retry_backoff_max", 30.0)),
                    float(getattr(self.config, "retry_jitter", 0.25)),
                    None,
                )
                logger.warning(
                    "stream(): retry %d/%d tras %s de %s en %.2fs",
                    attempt,
                    retry_max,
                    type(exc).__name__,
                    base_url,
                    delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                continue

   
    async def _stream_openai_sse(
        self,
        client,
        payload,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield SSE chunks for an OpenAI-compatible /chat/completions call.

        Extracted from the old stream() body so stream() can wrap this
        generator with retry-on-pre-first-chunk logic without disturbing
        the per-chunk parsing. Raises httpx.HTTPStatusError on the status
        line so the wrapper can decide whether to retry; everything past
        raise_for_status() runs only after a 2xx response.
        """
        # Accumulate tool calls across chunks.
        tc_accumulator: dict[int, dict[str, Any]] = {}

        async with client.stream("POST", "/chat/completions", json=payload) as response:
            if response.status_code >= 400:
                # Surface the provider's body so 4xx errors give an
                # actionable message instead of an opaque traceback.
                try:
                    body = await response.aread()
                    body_text = body.decode("utf-8", errors="replace")[:500]
                except Exception:
                    body_text = "<unreadable>"
                raise httpx.HTTPStatusError(
                    f"{response.status_code} from {payload.get('model', '?')}: {body_text}",
                    request=response.request,
                    response=response,
                )
            response.raise_for_status()

            async for raw_line in response.aiter_lines():
                line = raw_line.strip()
                if not line or not line.startswith("data:"):
                    continue

                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    # Flush remaining tool calls.
                    if tc_accumulator:
                        tcs = list(tc_accumulator.values())
                        for tc in tcs:
                            if "arguments" in tc and isinstance(tc["arguments"], str):
                                try:
                                    tc["arguments"] = json.loads(tc["arguments"])
                                except json.JSONDecodeError:
                                    tc["arguments"] = {"raw": tc["arguments"]}
                        yield {
                            "content": "",
                            "finish_reason": "tool_calls",
                            "tool_calls": tcs,
                        }
                    return

                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = chunk.get("choices", [])
                if isinstance(chunk.get("usage"), dict):
                    yield {"type": "usage", "usage": chunk["usage"], "content": ""}
                if not choices:
                    continue

                choice = choices[0]
                delta = choice.get("delta", {})

                # GLM-5.1 sends reasoning_content — yield it as a separate event
                # so the REPL can display thinking panels.
                reasoning = delta.get("reasoning_content")
                if reasoning:
                    yield {
                        "type": "reasoning",
                        "content": reasoning,
                        "finish_reason": None,
                        "tool_calls": None,
                    }

                content = delta.get("content") or ""
                finish_reason = choice.get("finish_reason")

                # Tool calls in stream.
                delta_tcs = delta.get("tool_calls")
                if delta_tcs:
                    for tc_delta in delta_tcs:
                        idx = tc_delta.get("index", 0)
                        if idx not in tc_accumulator:
                            tc_accumulator[idx] = {
                                "id": tc_delta.get("id", ""),
                                "name": "",
                                "arguments": "",
                            }
                        if tc_delta.get("id"):
                            tc_accumulator[idx]["id"] = tc_delta["id"]
                        func = tc_delta.get("function", {})
                        if func.get("name"):
                            tc_accumulator[idx]["name"] = func["name"]
                        if func.get("arguments"):
                            tc_accumulator[idx]["arguments"] += func["arguments"]

                # When tool calls finish, flush them.
                if finish_reason == "tool_calls" or (finish_reason == "stop" and tc_accumulator):
                    tcs = list(tc_accumulator.values())
                    for tc in tcs:
                        if "arguments" in tc and isinstance(tc["arguments"], str):
                            try:
                                tc["arguments"] = json.loads(tc["arguments"])
                            except json.JSONDecodeError:
                                tc["arguments"] = {"raw": tc["arguments"]}
                    yield {
                        "content": content,
                        "finish_reason": finish_reason,
                        "tool_calls": tcs,
                    }
                    tc_accumulator.clear()
                    continue

                yield {
                    "content": content,
                    "finish_reason": finish_reason,
                    "tool_calls": None,
                }
    # ── Internal: HTTP completion ────────────────────────────────────

    async def _do_complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Non-streaming completion; routes to Anthropic Messages API if needed."""
        client = await self._get_client()

        # Anthropic-compat profiles: minimum max_tokens is 16.
        max_tokens = max(self._resolve_max_tokens(kwargs) or 1024, 16)

        if self._is_anthropic():
            # Anthropic Messages API: max_tokens is REQUIRED.
            # Convert OpenAI-style messages and tool history to Anthropic format.
            anthropic_messages: list[dict[str, Any]] = []
            for message in messages:
                role = message.get("role", "user")
                if role == "system":
                    continue
                if role == "assistant" and message.get("tool_calls"):
                    blocks: list[dict[str, Any]] = []
                    if message.get("content"):
                        blocks.append({"type": "text", "text": message["content"]})
                    for tool_call in message.get("tool_calls", []):
                        function = tool_call.get("function", {})
                        raw_arguments = function.get("arguments", "{}")
                        if isinstance(raw_arguments, str):
                            try:
                                arguments = json.loads(raw_arguments)
                            except json.JSONDecodeError:
                                arguments = {"raw": raw_arguments}
                        else:
                            arguments = raw_arguments or {}
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": tool_call.get("id", ""),
                                "name": function.get("name", ""),
                                "input": arguments,
                            }
                        )
                    anthropic_messages.append({"role": "assistant", "content": blocks})
                elif role == "tool":
                    anthropic_messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": message.get("tool_call_id", ""),
                                    "content": message.get("content", ""),
                                }
                            ],
                        }
                    )
                else:
                    anthropic_messages.append(
                        {"role": role, "content": message.get("content", "")}
                    )

            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": anthropic_messages,
                "temperature": kwargs.get("temperature", self.config.temperature),
            }
            if tools:
                anthropic_tools: list[dict[str, Any]] = []
                for tool in tools:
                    function = tool.get("function", tool)
                    anthropic_tools.append(
                        {
                            "name": function.get("name", ""),
                            "description": function.get("description", ""),
                            "input_schema": function.get(
                                "parameters", function.get("input_schema", {"type": "object"})
                            ),
                        }
                    )
                payload["tools"] = anthropic_tools
            # Optional system prepended as top-level system field.
            sys_msg = next((m for m in messages if m.get("role") == "system"), None)
            if sys_msg and sys_msg.get("content"):
                payload["system"] = sys_msg["content"]

            response = await client.post("/v1/messages", json=payload)
            response.raise_for_status()
            return self._normalise_anthropic_response(response.json())

        # ── Kimi quirk: temperature=1 is the only value this model accepts ──
        # Doc 2026-07 says model `kimi-for-coding` rejects any other temperature.
        base = self._resolve_base_url() or ""
        if "kimi.com" in base.lower():
            kwargs = dict(kwargs)
            kwargs["temperature"] = 1.0

        payload = {
            "model": model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.config.temperature),
        }
        max_tokens = self._resolve_max_tokens(kwargs)
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
        response_format = kwargs.get("response_format")
        if response_format:
            payload["response_format"] = response_format

        thinking = getattr(self._profile(), "thinking_enabled", None)
        if thinking is not None and "deepseek" in (self._resolve_base_url() or "").lower():
            payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        response = await client.post("/chat/completions", json=payload)
        response.raise_for_status()

        data = response.json()
        return self._normalise_response(data)

    @staticmethod
    def _normalise_anthropic_response(data: dict[str, Any]) -> dict[str, Any]:
        """Convert an Anthropic Messages API response into our standard dict.

        Anthropic returns:
          {"content": [{"type": "text", "text": "..."} | ...],
           "stop_reason": "end_turn" | "max_tokens" | ...,
           "model": "...",
           "usage": {"input_tokens": N, "output_tokens": M}}
        """
        content_blocks = data.get("content", [])
        text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
        content = "".join(text_parts)
        # Some Anthropic variants surface reasoning under "thinking".
        reasoning = "".join(
            b.get("thinking", "") for b in content_blocks if b.get("type") == "thinking"
        )

        usage = data.get("usage", {})
        prompt_tokens = usage.get("input_tokens", 0)
        completion_tokens = usage.get("output_tokens", 0)

        stop_reason = data.get("stop_reason", "end_turn")
        finish_map = {
            "end_turn": "stop",
            "stop_sequence": "stop",
            "max_tokens": "length",
            "tool_use": "tool_calls",
        }
        finish = finish_map.get(stop_reason, "stop")

        # Tool use blocks surface as tool_calls so the REPL still works.
        tool_calls: list[ToolCall] = []
        for b in content_blocks:
            if b.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=b.get("id", ""),
                        name=b.get("name", ""),
                        arguments=b.get("input", {}),
                    )
                )

        return {
            "content": content,
            "reasoning_content": reasoning,
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "finish_reason": finish,
            "model": data.get("model", ""),
        }

    @staticmethod
    def _normalise_response(data: dict[str, Any]) -> dict[str, Any]:
        """Normalise an OpenAI-format JSON response into our standard dict."""
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"No choices in response: {data}")

        choice = choices[0]
        message = choice.get("message", {})
        content = message.get("content") or ""
        # GLM-5.1: expose reasoning_content so callers can display it.
        reasoning_content = message.get("reasoning_content") or ""

        # Parse tool calls.
        tool_calls: list[ToolCall] = []
        for tc_raw in message.get("tool_calls", []):
            func = tc_raw.get("function", {})
            try:
                args = json.loads(func.get("arguments", "{}")) if func.get("arguments") else {}
            except json.JSONDecodeError:
                args = {"raw": func.get("arguments", "")}
            tool_calls.append(
                ToolCall(
                    id=tc_raw.get("id", ""),
                    name=func.get("name", ""),
                    arguments=args,
                ),
            )

        usage = data.get("usage", {})

        return {
            "content": content,
            "reasoning_content": reasoning_content,
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            "finish_reason": choice.get("finish_reason", "stop"),
            "model": data.get("model", ""),
        }

    # ── Cleanup ─────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the underlying HTTP client. Safe to call multiple times."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def reset_client(self) -> None:
        """Force recreation of the HTTP client on next request.
        Useful after changing provider/model at runtime.
        """
        if self._client and not self._client.is_closed:
            # Sync close is OK — httpx handles it.
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._client.aclose())
            except RuntimeError:
                pass
        self._client = None


# ── Tool schema conversion helpers ──────────────────────────────────


def lilith_tools_to_openai(
    tools_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert Lilith tool descriptions to OpenAI function-calling format.

    Each *tools_data* item should have keys: ``name``, ``description``,
    ``parameters``.
    """
    openai_tools: list[dict[str, Any]] = []
    for tool in tools_data:
        params = tool.get("parameters") or {}
        properties: dict[str, Any] = {}
        required: list[str] = []

        for pname, pconfig in params.items():
            if isinstance(pconfig, dict) and pconfig.get("required"):
                required.append(pname)
            ptype = "string"
            if isinstance(pconfig, dict):
                ptype = pconfig.get("type", "string")
            properties[pname] = {
                "type": ptype,
                "description": pconfig.get("description", "") if isinstance(pconfig, dict) else "",
            }

        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            },
        )
    return openai_tools

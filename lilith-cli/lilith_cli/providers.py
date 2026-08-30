"""DeepSeek, xAI and Sakana provider wrapper for Lilith CLI.

All supported providers expose OpenAI-compatible Chat Completions, so one
small HTTP path covers streaming, tool calling and retry without compatibility
branches for retired backends.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import httpx


if TYPE_CHECKING:
    from .config import YggdrasilConfig


logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────

_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds
_REQUEST_TIMEOUT = 180.0  # seconds (reasoning calls can take >60s)


# ── Pricing (v4.3.1) ────────────────────────────────────────────────
# Cost per 1M tokens (input, output) in USD. Used to estimate per-call
# and total cost in the REPL bottom toolbar. Providers not listed here
# fall back to 0.0 (cost hidden).
# Sources: official DeepSeek, xAI and Sakana pricing pages, refreshed 2026-08-08.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-v4-pro": (0.435, 0.87),
    "grok-4.20-0309-non-reasoning": (1.25, 2.5),
    "grok-4.20-0309-reasoning": (1.25, 2.5),
    "grok-4.20-multi-agent-0309": (1.25, 2.5),
    "grok-4.3": (1.25, 2.5),
    "grok-4.5": (2.0, 6.0),
    "fugu-ultra": (5.0, 30.0),
    "fugu-ultra-v1.0": (5.0, 30.0),
    "fugu-ultra-v1.1": (5.0, 30.0),
}


# ── Context windows (v4.3.1) ───────────────────────────────────────
# Approximate context-window sizes in tokens. Used for the /context
# progress bar. Unknown models fall back to 128K (common default).
_MODEL_CONTEXTS: dict[str, int] = {
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-pro": 1_000_000,
    "grok-4.20-0309-non-reasoning": 1_000_000,
    "grok-4.20-0309-reasoning": 1_000_000,
    "grok-4.20-multi-agent-0309": 1_000_000,
    "grok-4.3": 1_000_000,
    "grok-4.5": 500_000,
    "fugu": 1_000_000,
    "fugu-ultra": 1_000_000,
    "fugu-ultra-v1.0": 1_000_000,
    "fugu-ultra-v1.1": 1_000_000,
}

_DEFAULT_CONTEXT_WINDOW = 128_000


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost for a single LLM call.

    Falls back to 0.0 for unknown models so display code remains defensive.
    """
    rate = _MODEL_PRICING.get(model)
    if rate is None:
        return 0.0
    input_rate, output_rate = rate
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000


def estimate_context_window(model: str | None) -> int:
    """Return the approximate max context window (in tokens) for *model*.

    Falls back to a safe default of 128k for unknown models so the
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

    Uses httpx directly against Lilith's supported OpenAI-compatible
    DeepSeek, xAI and Sakana endpoints.
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
            base_url = self._resolve_base_url()
            if not base_url:
                raise ValueError("El proveedor activo no tiene base_url configurada")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            self._client = httpx.AsyncClient(
                base_url=base_url,
                headers=headers,
                timeout=httpx.Timeout(_REQUEST_TIMEOUT),
            )
        return self._client

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
        """Return the active model; CLI overrides live at the top level."""
        if self.config.model:
            return self.config.model
        profile = self.config.providers.get(self.config.provider.lower())
        return profile.model if profile and profile.model else ""

    def _resolve_max_tokens(self, kwargs: dict[str, Any] | None = None) -> int | None:
        """Resolve output-token limit: explicit call > provider > global."""
        if kwargs and kwargs.get("max_tokens") is not None:
            return int(kwargs["max_tokens"])
        profile = self.config.providers.get(self.config.provider.lower())
        if profile and profile.max_tokens is not None:
            return profile.max_tokens
        return self.config.max_tokens

    def _validate_route(self, model: str) -> None:
        """Reject retired providers and unknown models before any HTTP call."""
        from .config import require_supported_model, require_supported_provider

        provider = require_supported_provider(self.config.provider)
        require_supported_model(provider, model)


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
        bypass_circuit = bool(kwargs.pop("bypass_circuit", False))
        model = model or self._resolve_model()
        self._validate_route(model)

        async def _attempt() -> dict[str, Any]:
            return await self._do_complete(model, messages, tools=tools, **kwargs)

        return await self._run_with_retry(
            _attempt, op_label="LLM call", bypass_circuit=bypass_circuit
        )

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
        if delay > backoff_max:
            delay = backoff_max
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
        bypass_circuit = bool(kwargs.pop("bypass_circuit", False))
        model = model or self._resolve_model()
        self._validate_route(model)

        # OpenAI-compatible SSE path. The HTTP+parsing work lives in the
        # private generator _stream_openai_sse() so stream() can wrap
        # it with retry. We retry only BEFORE the first chunk is
        # yielded: once the caller has consumed anything we cannot
        # resume a half-streamed response safely, so transient
        # failures past that point propagate with a clear message.

        self._ensure_provider_available(bypass_circuit=bypass_circuit)
        client = await self._get_client()

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
        response_format = kwargs.get("response_format")
        if response_format:
            payload["response_format"] = response_format

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
                    return

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
        """Send one OpenAI-compatible Chat Completions request."""
        client = await self._get_client()

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

        response = await client.post("/chat/completions", json=payload)
        response.raise_for_status()

        data = response.json()
        return self._normalise_response(data)

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
                loop.create_task(self._client.aclose())  # noqa: RUF006
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

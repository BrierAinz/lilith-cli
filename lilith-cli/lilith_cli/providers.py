"""Unified LLM provider wrapper for Yggdrasil CLI v6.0.

Uses httpx directly for OpenAI-compatible endpoints (fast, lightweight),
with optional litellm fallback for non-OpenAI providers (Anthropic, etc.).
Streaming, tool-calling, and exponential-backoff retry included.

Sakana Fugu is treated as an **OpenAI-compatible** provider (its
``/v1/chat/completions`` endpoint speaks the OpenAI wire format). The
Sakana-specific Responses API at ``/v1/responses`` is still supported
behind an opt-in ``providers.sakana.use_responses: true`` flag, used
by the original Sakana tool-calling experiments.
"""

from __future__ import annotations

import asyncio
import json
import logging
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
_REQUEST_TIMEOUT = 180.0  # seconds (Fugu Ultra reasoning can take >60s)


# ── Pricing (v4.3.1) ────────────────────────────────────────────────
# Cost per 1M tokens (input, output) in USD. Used to estimate per-call
# and total cost in the REPL bottom toolbar. Providers not listed here
# fall back to 0.0 (cost hidden).
# Sources: published provider pricing pages, last refreshed 2026-07-09.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Sakana
    "fugu-ultra":            (3.0, 9.0),
    "fugu-ultra-20260615":   (3.0, 9.0),
    # Anthropic (in case litellm is used)
    "claude-sonnet-4":        (3.0, 15.0),
    "claude-opus-4":          (15.0, 75.0),
    "claude-haiku-4":         (0.8, 4.0),
    # OpenAI (in case litellm is used)
    "gpt-4o":                 (2.5, 10.0),
    "gpt-4o-mini":            (0.15, 0.6),
    "o3":                     (15.0, 60.0),
    # DeepSeek
    "deepseek-chat":          (0.27, 1.1),
    "deepseek-v4-flash":      (0.07, 0.27),
    "deepseek-reasoner":      (0.55, 2.19),
    # Qwen / Alibaba
    "qwen-max-latest":        (2.4, 9.6),
    "qwen-plus-latest":       (0.4, 1.2),
    "qwen3.7-max":            (2.4, 9.6),
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
    # Sakana
    "fugu-ultra": 262_144,
    "fugu-ultra-20260615": 262_144,
    # Anthropic (in case litellm is used)
    "claude-sonnet-4": 200_000,
    "claude-opus-4": 200_000,
    "claude-haiku-4": 200_000,
    # OpenAI (in case litellm is used)
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "o3": 200_000,
    # DeepSeek
    "deepseek-chat": 64_000,
    "deepseek-v4-flash": 64_000,
    "deepseek-reasoner": 64_000,
    # Qwen / Alibaba
    "qwen-max-latest": 128_000,
    "qwen-plus-latest": 128_000,
    "qwen3.7-max": 128_000,
    # Kimi
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
    "fugu-ultra": 128_000,
    "fugu-ultra-20260615": 128_000,
    "claude-sonnet-4": 200_000,
    "claude-opus-4": 200_000,
    "claude-haiku-4": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "o3": 200_000,
    "deepseek-chat": 64_000,
    "deepseek-v4-flash": 64_000,
    "deepseek-reasoner": 64_000,
    "qwen-max-latest": 32_000,
    "qwen-plus-latest": 128_000,
    "qwen3.7-max": 32_000,
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

    def _is_sakana_responses(self) -> bool:
        """True when the active provider uses Sakana's Responses API
        (/v1/responses with input=str instead of messages=[...]).

        Sakana exposes BOTH an OpenAI-compatible Chat Completions
        endpoint at ``/v1/chat/completions`` AND a Responses API at
        ``/v1/responses``. The default is Chat Completions (matches
        the OpenAI wire format and Lilith's main session default);
        opt into the Responses API by setting
        ``providers.sakana.use_responses: true`` in the YAML.
        """
        if "sakana.ai" not in (self._resolve_base_url() or "").lower():
            return False
        profile = self.config.providers.get(self.config.provider.lower())
        return bool(profile and profile.use_responses)

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

        Retries up to 3 times with exponential back-off on transient errors.
        """
        model = model or self._resolve_model()
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return await self._do_complete(model, messages, tools=tools, **kwargs)
            except Exception as exc:
                last_exc = exc
                logger.warning("Attempt %d/%d failed: %s", attempt, _MAX_RETRIES, exc)
                if attempt < _MAX_RETRIES:
                    delay = _BASE_DELAY * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)

        raise RuntimeError(f"LLM call failed after {_MAX_RETRIES} retries: {last_exc}")

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
        model = model or self._resolve_model()

        # ── Anthropic-compat / Sakana-Responses profiles don't speak the
        # OpenAI SSE protocol this method implements; fall back to the
        # non-streaming path and emit the result as a single chunk.
        if self._is_anthropic() or self._is_sakana_responses():
            result = await self._do_complete(model, messages, tools=tools, **kwargs)
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
        response_format = kwargs.get("response_format")
        if response_format:
            payload["response_format"] = response_format

        # Accumulate tool calls across chunks.
        tc_accumulator: dict[int, dict[str, Any]] = {}

        async with client.stream("POST", "/chat/completions", json=payload) as response:
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

        if self._is_sakana_responses():
            # Concatenate messages into a single string with role prefixes.
            parts: list[str] = []
            for m in messages:
                role = m.get("role", "user")
                content = m.get("content", "")
                if role == "system":
                    parts.append(f"System: {content}")
                elif role == "assistant":
                    parts.append(f"Assistant: {content}")
                else:
                    parts.append(f"User: {content}")

            # ── base_url may already include /v1 (chat completions) or not.
            # Sakana's Responses API lives at /v1/responses; we strip any
            # trailing /v1 from the configured base_url and append the path
            # explicitly so we never end up with /v1/v1/responses.
            base = self._resolve_base_url() or ""
            base_clean = base.rstrip("/")
            if base_clean.endswith("/v1"):
                base_clean = base_clean[:-3]
            responses_path = f"{base_clean}/v1/responses"

            # ── Floor 256 / cap 4096: fugu-ultra burns ~50-120 reasoning
            # tokens before producing any visible text, so a tight cap
            # silently returns status=incomplete with content="". Lift the
            # floor to keep one-shot prompts viable; cap at 4096 to avoid
            # runaway cost when the global max_tokens is configured high.
            sakana_max = max(min(max_tokens, 4096), 256)
            sakana_payload: dict[str, Any] = {
                "model": model,
                "input": "\n".join(parts),
                "max_output_tokens": sakana_max,
            }
            response = await client.post(responses_path, json=sakana_payload)
            response.raise_for_status()
            return self._normalise_sakana_response(response.json())

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
    def _normalise_sakana_response(data: dict[str, Any]) -> dict[str, Any]:
        """Convert a Sakana Responses API payload into the standard dict.

        Verified against live calls to ``https://api.sakana.ai/v1/responses``
        (model ``fugu-ultra``, 2026-07-16). The wire format observed is:

          {
            "id": "resp-...",
            "object": "response",
            "status": "completed" | "incomplete",
            "incomplete_details": {"reason": "max_output_tokens" | ...},
            "output": [
              {"type": "reasoning",
               "id": "rs_...",
               "summary": [{"type": "summary_text", "text": "..."}]},
              {"type": "message",
               "content": [{"type": "output_text", "text": "..."}]},
            ],
            "usage": {"input_tokens": N,
                      "output_tokens": M,
                      "total_tokens": T,
                      "output_tokens_details": {"reasoning_tokens": R}}
          }

        The assistant text lives at
        ``output[*].content[*].text`` where ``type == "output_text"``
        (Sakana mirrors the OpenAI Responses API shape; ``text`` is also
        accepted for forward-compat). Reasoning summaries live at
        ``output[*].summary[*].text`` where ``type == "summary_text"``
        — note this is ``summary``, NOT ``content`` (a common pitfall:
        Sakana's reasoning blocks carry a list of summary chunks, not
        OpenAI-style content chunks).

        We also tolerate the legacy Chat Completions shape (``choices``)
        in case Sakana falls back, and we surface an explicit ``error``
        key when ``status == "incomplete"`` so callers don't mistake an
        empty ``content`` for a successful zero-token reply.
        """
        # Chat Completions-style responses also flow through here when Sakana
        # decides to return them; detect via presence of "choices".
        if "choices" in data:
            return LLMProviderWrapper._normalise_response(data)

        out: list[Any] = data.get("output", []) or data.get("outputs", [])
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in out:
            kind = block.get("type", "")
            if kind == "message":
                # Sakana Responses API: block.content[*].type is "output_text".
                # OpenAI standard: same field would be "text". Accept both.
                for c in block.get("content", []) or []:
                    ctype = c.get("type", "")
                    if ctype in ("output_text", "text"):
                        text_parts.append(c.get("text", ""))
                    elif ctype == "reasoning":
                        reasoning_parts.append(c.get("text", ""))
            elif kind == "reasoning":
                # Reasoning summaries live at ``summary[*].text`` — NOT
                # ``content`` (Sakana diverges from the OpenAI Responses
                # shape here). Accept ``content`` too for safety.
                summary_items = block.get("summary") or block.get("content") or []
                for c in summary_items:
                    ctype = c.get("type", "")
                    if ctype in ("summary_text", "reasoning_text", "text"):
                        reasoning_parts.append(c.get("text", ""))
            elif kind == "tool_use" or kind == "function_call":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=block.get("input") or block.get("arguments") or {},
                    )
                )

        # ── Map Sakana's status to a finish_reason the rest of Lilith ──
        # already understands, plus surface a structured error when the
        # response was cut short (otherwise callers see content="" and
        # have no idea why).
        raw_status = data.get("status", "completed")
        incomplete_reason = (
            (data.get("incomplete_details") or {}).get("reason")
            if raw_status == "incomplete"
            else None
        )
        if raw_status == "incomplete":
            finish_reason = "length"
        else:
            finish_reason = "stop"

        usage = data.get("usage", {}) or {}
        prompt_tokens = usage.get("input_tokens", 0)
        completion_tokens = usage.get("output_tokens", 0)
        total_tokens = usage.get(
            "total_tokens", prompt_tokens + completion_tokens
        )

        result: dict[str, Any] = {
            "content": "\n".join(p for p in text_parts if p),
            "reasoning_content": "\n".join(p for p in reasoning_parts if p),
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            "finish_reason": finish_reason,
            "model": data.get("model", ""),
        }
        # Surface the truncation cause so callers (REPL, doctor, etc.)
        # can show "Sakana: respuesta truncada por max_output_tokens"
        # instead of "respondió en N ms pero sin contenido".
        if raw_status == "incomplete":
            result["error"] = (
                f"Sakana Responses API returned status=incomplete "
                f"(reason={incomplete_reason or 'unknown'})"
            )
        return result

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

"""OpenAI-compatible provider implementation."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import httpx

from llm_code.api.errors import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderModelNotFoundError,
    ProviderOverloadError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from llm_code.api.provider import LLMProvider
from llm_code.api.sse import aparse_sse_events_from_lines, parse_sse_events
from llm_code.api.types import (
    ContentBlock,
    Message,
    MessageRequest,
    MessageResponse,
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    StreamThinkingDelta,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    TextBlock,
    ThinkingBlock,
    TokenUsage,
    ToolDefinition,
    ToolUseBlock,
)


# Wave2-1b: hard cap on provider-reported Retry-After so a misbehaving
# proxy that returns "Retry-After: 86400" does not wedge the runtime
# for a day. Real providers use small values (30s typical on 429).
_logger = logging.getLogger(__name__)

# v15 M3 — the warn-once flag stays on this module (instead of
# moving to conversion.py) so existing test fixtures that reset it
# via ``openai_compat._thinking_drop_warned = False`` between tests
# keep working. ``conversion._warn_thinking_dropped_once`` reads /
# mutates the flag through this module reference.
_thinking_drop_warned = False

_MAX_RETRY_AFTER_SECONDS = 60.0


# v15 M3 — these helpers moved into ``llm_code.api.conversion``
# (single source of truth for cross-provider message conversion).
# Re-exported here as module-level names so existing imports
# (``from llm_code.api.openai_compat import _strip_reasoning_keys``)
# keep working — the actual implementation lives in conversion.py
# and the OpenAI-compat-specific log lines fire under the
# ``llm_code.api.openai_compat`` logger so log scrapers / tests
# asserting on log names see no behavioural change.
from llm_code.api.conversion import (  # noqa: E402  re-export
    _strip_reasoning_keys,
)


# Wave2-1a P2: provider reasoning fields we know how to extract.
_REASONING_FIELD_CANDIDATES: tuple[str, ...] = (
    "reasoning_content",  # DeepSeek-R1 / DeepSeek-reasoner / Qwen QwQ / vLLM
    "reasoning",          # OpenAI o-series (newer SDK)
)


def _parse_retry_after_header(raw: str | None) -> float | None:
    """Parse an HTTP Retry-After value into a seconds float (wave2-1b)."""
    if not raw:
        return None
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return min(value, _MAX_RETRY_AFTER_SECONDS)


def _token_usage_from_dict(usage_data: dict) -> "TokenUsage":
    """Build TokenUsage from a raw provider usage dict (wave2-2)."""
    input_tokens = int(usage_data.get("prompt_tokens") or usage_data.get("input_tokens") or 0)
    output_tokens = int(usage_data.get("completion_tokens") or usage_data.get("output_tokens") or 0)

    cache_read = 0
    details = usage_data.get("prompt_tokens_details")
    if isinstance(details, dict):
        cache_read = int(details.get("cached_tokens") or 0)
    cache_read = int(usage_data.get("cache_read_input_tokens") or cache_read)
    cache_creation = int(usage_data.get("cache_creation_input_tokens") or 0)

    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
    )


def _extract_reasoning_text(source: dict) -> str:
    """Pull reasoning/thinking text from a provider dict (wave2-1a P2)."""
    for field in _REASONING_FIELD_CANDIDATES:
        value = source.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_anthropic_thinking(
    content: object,
) -> tuple["ThinkingBlock", ...]:
    """Extract ThinkingBlocks from an Anthropic-style content list (wave2-1a P2)."""
    if not isinstance(content, list):
        return ()
    blocks: list[ThinkingBlock] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "thinking":
            continue
        text = item.get("thinking")
        if not isinstance(text, str):
            continue
        signature = item.get("signature")
        blocks.append(
            ThinkingBlock(
                content=text,
                signature=signature if isinstance(signature, str) else "",
            )
        )
    return tuple(blocks)


class OpenAICompatProvider(LLMProvider):
    """Provider adapter for OpenAI-compatible APIs (Ollama, vLLM, LM Studio, etc.)."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model_name: str = "",
        max_retries: int = 2,
        timeout: float = 120.0,
        native_tools: bool = True,
        rate_handler: Any = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model_name = model_name
        self._max_retries = max_retries
        self._timeout = timeout
        self._native_tools = native_tools
        # C3b: opt-in shared retry policy. None keeps the legacy
        # ``_post_with_retry`` loop untouched for backward compat.
        self._rate_handler = rate_handler

        # Resolve model profile for capability overrides
        from llm_code.runtime.model_profile import get_profile
        self._profile = get_profile(model_name)

        # v15 M2 — proactive sliding-window rate limiter. Profile opts
        # in via ``proactive_rate_limit_per_minute > 0`` (default 0
        # disables the gate, preserving current behaviour). Optional
        # concurrency cap via ``proactive_rate_limit_concurrency``
        # serialises in-flight requests independently of the rate
        # window — useful when a free-tier endpoint also caps parallel
        # streams.
        from llm_code.api.rate_limiter import SlidingWindowLimiter
        if self._profile.proactive_rate_limit_per_minute > 0:
            self._limiter: SlidingWindowLimiter | None = SlidingWindowLimiter(
                max_requests=self._profile.proactive_rate_limit_per_minute,
                window_seconds=60.0,
                concurrency=self._profile.proactive_rate_limit_concurrency or None,
            )
        else:
            self._limiter = None

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(timeout),
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def send_message(self, request: MessageRequest) -> MessageResponse:
        # v15 M1 — short-circuit trivial calls (quota probe, title
        # generation, prefix detection, suggestion mode, filepath
        # extraction) with a synthetic response. Saves an HTTP
        # round-trip + 50-500 tokens for patterns whose answer is
        # deterministic.
        if self._profile.enable_request_optimizations:
            from llm_code.api.request_optimizations import try_optimize
            hit = try_optimize(request)
            if hit is not None:
                return hit.response
        payload = self._build_payload(request, stream=False)
        response = await self._post_with_retry(payload)
        return self._parse_response(response)

    async def stream_message(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        # v15 M1 — same short-circuit on the streaming path. Synthesise
        # a one-shot StreamMessageStart → text deltas → StreamMessageStop
        # sequence so downstream renderers see a normal stream shape.
        if self._profile.enable_request_optimizations:
            from llm_code.api.request_optimizations import (
                _synthesize_stream_events,
                try_optimize,
            )
            hit = try_optimize(request)
            if hit is not None:
                return _synthesize_stream_events(hit.response)
        payload = self._build_payload(request, stream=True)
        # v2.6.1 M3 — true SSE streaming. The previous implementation
        # called ``_post_with_retry`` which buffered the entire response
        # body before parsing, so user-visible TTFT was full generation
        # time. With ``_stream_with_retry`` the chunks are parsed as
        # they arrive, dropping perceived latency to actual TTFT.
        return self._stream_with_retry(payload)

    def supports_native_tools(self) -> bool:
        return self._native_tools

    def supports_images(self) -> bool:
        return self._profile.supports_images

    def supports_reasoning(self) -> bool:
        return self._profile.supports_reasoning

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        messages: tuple[Message, ...],
        system: str | None = None,
    ) -> list[dict]:
        # v15 M3 — per-message conversion delegates to
        # ``self._convert_message`` (which is itself a thin shim over
        # ``llm_code.api.conversion``) so tests that monkey-patch
        # ``_convert_message`` to inject synthetic state continue to
        # work. The v14 Mechanism B reasoning-content history filter
        # runs alongside the loop. The 49-scenario parity gate
        # (``tests/test_api/parity/test_provider_conversion_parity_v15.py``)
        # verifies byte-identical output against a corpus captured
        # from v2.4.0.
        #
        # v2.5.1 — split bundled ToolResultBlock messages BEFORE per-
        # message conversion. ``conversation.py`` bundles N tool
        # results from one turn into a single ``Message`` with
        # ``len(content) > 1``; ``_convert_message`` then takes the
        # parts-array path which has no place for ToolResultBlocks
        # and silently drops them. Splitting first ensures every
        # bundled result becomes its own ``role: tool`` entry on the
        # wire — the shape OpenAI-compat servers expect. Without this
        # the model receives an empty user message followed by
        # mech-A ``<system-reminder>`` blocks claiming a tool result
        # exists "above", which GLM-5.1 correctly identifies as an
        # injection and refuses to act on (observed in v2.5.0 GA).
        from llm_code.api.conversion import _split_bundled_tool_results

        # v2.9.0 P2 — compress older tool_result payloads on re-feed
        # when the active profile opts in. Saves 60-80% of the prefill
        # token cost on multi-search workflows where iter N+1 would
        # otherwise re-prefill iter 0..N-1's full tool results. The
        # most recent contiguous tool-result batch is preserved intact
        # so the model still has full data for current reasoning.
        # Default ``False`` keeps v2.8.1 byte-parity for every profile
        # that doesn't explicitly opt in.
        if getattr(self._profile, "compress_old_tool_results", False):
            from llm_code.api.conversion import (
                compress_old_tool_results as _compress,
            )
            messages = _compress(messages)

        messages = _split_bundled_tool_results(messages)

        result: list[dict] = []
        if system:
            result.append({"role": "system", "content": system})

        strip_reasoning = self._profile.strip_prior_reasoning
        reasoning_strip_count = 0
        reasoning_strip_bytes = 0

        for msg in messages:
            converted = self._convert_message(msg)
            if (
                strip_reasoning
                and converted.get("role") == "assistant"
            ):
                removed = _strip_reasoning_keys(converted)
                if removed:
                    reasoning_strip_count += 1
                    reasoning_strip_bytes += removed
            result.append(converted)

        if reasoning_strip_count:
            _logger.info(
                "tool_consumption: reasoning_stripped turns=%d total_bytes=%d",
                reasoning_strip_count, reasoning_strip_bytes,
            )

        return result

    def _convert_message(self, msg: Message) -> dict:
        """Single-message OpenAI-compat conversion. Thin shim over
        :func:`llm_code.api.conversion._openai_convert_message`.

        Kept for backward compat with tests that exercise the per-
        message path directly; production callers go through
        ``_build_messages``.
        """
        from llm_code.api.conversion import _openai_convert_message
        return _openai_convert_message(msg)

    def _build_payload(self, request: MessageRequest, *, stream: bool) -> dict:
        payload: dict = {
            "model": request.model or self._model_name,
            "messages": self._build_messages(request.messages, system=request.system),
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "stream": stream,
        }

        if request.tools and self._native_tools:
            payload["tools"] = [
                self._convert_tool(t) for t in request.tools
            ]

        if stream:
            payload["stream_options"] = {"include_usage": True}

        if request.extra_body:
            payload.update(request.extra_body)

        return payload

    def _convert_tool(self, tool: ToolDefinition) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }

    async def _post_with_retry(self, payload: dict) -> httpx.Response:
        # C3b opt-in: when a RateLimitHandler is injected, delegate to
        # the shared retry policy. Otherwise keep the legacy loop below
        # so existing behaviour (and the 5000+ tests that depend on it)
        # stays identical.
        if self._rate_handler is not None:
            return await self._post_via_rate_handler(payload)

        url = f"{self._base_url}/chat/completions"
        last_exc: Exception | None = None

        # 529 Overload: separate long-backoff retry track (30s -> 60s -> 120s, max 3 attempts)
        _OVERLOAD_BACKOFFS = [30, 60, 120]
        _overload_attempt = 0
        attempt = 0

        while attempt <= self._max_retries:
            try:
                response = await self._post_with_proactive_limit(url, payload)
                self._raise_for_status(response)
                return response
            except ProviderOverloadError as exc:
                last_exc = exc
                if _overload_attempt < len(_OVERLOAD_BACKOFFS):
                    backoff = _OVERLOAD_BACKOFFS[_overload_attempt]
                    _overload_attempt += 1
                    await asyncio.sleep(backoff)
                    # Overload retries don't count against normal retry budget
                    continue
                raise
            except ProviderRateLimitError as exc:
                # Wave2-1b: honor Retry-After hint from the provider
                # when set; otherwise fall back to exponential. This
                # is what avoids hammering a rate-limited provider
                # before its reset window expires.
                last_exc = exc
                if attempt < self._max_retries:
                    backoff = exc.retry_after if exc.retry_after is not None else float(2 ** attempt)
                    await asyncio.sleep(backoff)
                    attempt += 1
                    continue
                raise
            except ProviderConnectionError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    attempt += 1
                    continue
                raise
            except (ProviderAuthError, ProviderModelNotFoundError):
                raise
            except httpx.ConnectError as exc:
                last_exc = ProviderConnectionError(str(exc))
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    attempt += 1
                    continue
                raise last_exc from exc
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
                # Wave2-1b: any httpx timeout flavor is now a distinct
                # ProviderTimeoutError (retryable) instead of falling
                # through to a generic Exception in conversation.py
                # that skipped the retry budget entirely.
                last_exc = ProviderTimeoutError(str(exc) or type(exc).__name__)
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    attempt += 1
                    continue
                raise last_exc from exc
            attempt += 1

        raise last_exc  # type: ignore[misc]

    async def _post_via_rate_handler(self, payload: dict) -> httpx.Response:
        """Shared retry policy path (C3b opt-in).

        ``run_with_rate_limit`` classifies every exception against the
        OpenAI-compat taxonomy, backs off per the handler's policy,
        and re-raises whatever the handler refuses to retry. The inner
        call raises ``_raise_for_status`` itself so classification
        sees the strongly-typed provider errors.
        """
        from llm_code.api.rate_limiter import (
            provider_taxonomy_openai_compat,
            run_with_rate_limit,
        )

        url = f"{self._base_url}/chat/completions"

        async def attempt() -> httpx.Response:
            response = await self._post_with_proactive_limit(url, payload)
            self._raise_for_status(response)
            return response

        return await run_with_rate_limit(
            attempt,
            self._rate_handler,
            taxonomy=provider_taxonomy_openai_compat(),
        )

    async def _post_with_proactive_limit(
        self, url: str, payload: dict,
    ) -> httpx.Response:
        """Wrap the actual HTTP POST in the proactive sliding-window
        limiter (v15 M2) when the profile opts in.

        Centralises the gate so both the legacy retry loop and the
        shared ``_post_via_rate_handler`` path see identical
        proactive-throttle behaviour. Profiles without
        ``proactive_rate_limit_per_minute > 0`` go straight to the
        post — zero overhead.
        """
        if self._limiter is not None:
            async with self._limiter:
                return await self._client.post(url, json=payload)
        return await self._client.post(url, json=payload)

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code == 200:
            return
        try:
            body = response.json()
            msg = body.get("error", {}).get("message", response.text)
        except Exception:
            msg = response.text

        if response.status_code == 401:
            raise ProviderAuthError(msg)
        if response.status_code == 404:
            raise ProviderModelNotFoundError(msg)
        if response.status_code == 429:
            # Wave2-1b: honor the provider's Retry-After hint when
            # present so the backoff respects its own rate-limit
            # reset window instead of guessing with 2**attempt.
            retry_after = _parse_retry_after_header(
                response.headers.get("Retry-After")
            )
            raise ProviderRateLimitError(msg, retry_after=retry_after)
        if response.status_code == 529:
            raise ProviderOverloadError(msg)
        # Native tool calling not supported by this server: don't
        # retry. Without this fast-fail, the 3-strike retry loop
        # in _post_with_retry burns ~30s on exponential backoff
        # before the outer fallback sees the error and switches to
        # XML tag mode. With the fast-fail, the outer fallback fires
        # on the first attempt. Detected by string match on the two
        # known error messages vLLM and OpenAI-compat servers emit
        # when tools=[...] is sent but not supported.
        # Observed 2026-04-09 in a Qwen3.5 field report: first
        # iteration burned 34s on this retry storm before the
        # fallback fired. Raised as ProviderError(is_retryable=False)
        # so the outer try/except in conversation.py's stream_message
        # caller still pattern-matches the message and switches to
        # XML mode, but _post_with_retry doesn't re-attempt.
        _msg_lower = msg.lower() if isinstance(msg, str) else ""
        if "tool-call-parser" in _msg_lower or "tool choice" in _msg_lower:
            raise ProviderError(msg, is_retryable=False)
        if response.status_code >= 500:
            raise ProviderConnectionError(msg)
        # Other 4xx — treat as connection error
        raise ProviderConnectionError(f"HTTP {response.status_code}: {msg}")

    def _parse_response(self, response: httpx.Response) -> MessageResponse:
        data = response.json()
        choices = data.get("choices")
        if not choices:
            raise ProviderConnectionError(f"No choices in API response: {str(data)[:200]}")
        choice = choices[0]
        message = choice.get("message")
        if not message:
            raise ProviderConnectionError(f"No message in API choice: {str(choice)[:200]}")
        finish_reason = choice.get("finish_reason") or "stop"

        content_blocks: list[ContentBlock] = []

        # Wave2-1a P2: extract thinking from the message payload before
        # anything else. Two shapes are supported:
        #   1. OpenAI-compat scalar field: message["reasoning_content"]
        #      or message["reasoning"] is a plain string.
        #   2. Anthropic-style structured: message["content"] is a list
        #      of blocks, one of which may be {"type": "thinking", ...}.
        # Both may appear on proxy servers; we accept either or both.
        thinking_blocks: list[ThinkingBlock] = []
        reasoning_text = _extract_reasoning_text(message)
        if reasoning_text:
            thinking_blocks.append(ThinkingBlock(content=reasoning_text))
        thinking_blocks.extend(_extract_anthropic_thinking(message.get("content")))

        tool_calls = message.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                fn = tc["function"]
                try:
                    args = json.loads(fn["arguments"])
                except (json.JSONDecodeError, KeyError):
                    args = {}
                content_blocks.append(
                    ToolUseBlock(id=tc["id"], name=fn["name"], input=args)
                )
        else:
            raw_content = message.get("content")
            # Anthropic-style structured content: pick text blocks out
            # of the list. Thinking was already harvested above.
            if isinstance(raw_content, list):
                for item in raw_content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        content_blocks.append(TextBlock(text=item.get("text") or ""))
            else:
                content_blocks.append(TextBlock(text=raw_content or ""))

        usage_data = data.get("usage", {})
        usage = _token_usage_from_dict(usage_data)

        return MessageResponse(
            content=tuple(content_blocks),
            usage=usage,
            stop_reason=finish_reason,
            thinking=tuple(thinking_blocks),
        )

    def _iter_stream_events(self, raw: str) -> _StreamIterator:
        """Return async iterator over parsed SSE stream events.

        Legacy synchronous-feed path kept for callers that already
        have the full response body in memory (tests using
        ``respx.mock(text=sse_body)``). v2.6.1 M3 introduced the
        streaming counterpart :meth:`_stream_with_retry` which
        delivers events as soon as each block parses.
        """
        return _StreamIterator(raw)

    async def _stream_with_retry(
        self, payload: dict,
    ) -> AsyncIterator[StreamEvent]:
        """v2.6.1 M3 — true SSE streaming with retry on connect-time errors.

        Opens an httpx streaming POST, validates the response status
        BEFORE yielding any event, and forwards parsed SSE chunks
        downstream as they arrive. Retries are honored only when the
        failure happens before the first event yields — once data has
        been emitted to the consumer there is no safe way to retry
        without duplicating events, so mid-stream errors propagate.
        """
        url = f"{self._base_url}/chat/completions"
        last_exc: Exception | None = None

        # Match the non-streaming retry budget so configured providers see
        # uniform behaviour across both code paths.
        _OVERLOAD_BACKOFFS = [30, 60, 120]
        _overload_attempt = 0
        attempt = 0

        while attempt <= self._max_retries:
            try:
                async for event in self._stream_one_attempt(url, payload):
                    yield event
                return
            except ProviderOverloadError as exc:
                last_exc = exc
                if _overload_attempt < len(_OVERLOAD_BACKOFFS):
                    backoff = _OVERLOAD_BACKOFFS[_overload_attempt]
                    _overload_attempt += 1
                    await asyncio.sleep(backoff)
                    # Overload retries don't count against normal retry budget
                    continue
                raise
            except ProviderRateLimitError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    backoff = (
                        exc.retry_after if exc.retry_after is not None
                        else float(2 ** attempt)
                    )
                    await asyncio.sleep(backoff)
                    attempt += 1
                    continue
                raise
            except ProviderConnectionError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    attempt += 1
                    continue
                raise
            except (ProviderAuthError, ProviderModelNotFoundError):
                raise
            except httpx.ConnectError as exc:
                last_exc = ProviderConnectionError(str(exc))
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    attempt += 1
                    continue
                raise last_exc from exc
            except (
                httpx.ReadTimeout,
                httpx.ConnectTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
            ) as exc:
                last_exc = ProviderTimeoutError(
                    str(exc) or type(exc).__name__
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    attempt += 1
                    continue
                raise last_exc from exc
            attempt += 1

        if last_exc is not None:
            raise last_exc

    async def _stream_one_attempt(
        self, url: str, payload: dict,
    ) -> AsyncIterator[StreamEvent]:
        """One streaming attempt — opens the POST, parses incrementally.

        Status check runs BEFORE any event is yielded so a non-200
        response surfaces as a typed provider error that the
        ``_stream_with_retry`` outer loop can classify and retry.
        """
        if self._limiter is not None:
            async with self._limiter:
                async for event in self._aiter_stream_events_from_http(url, payload):
                    yield event
        else:
            async for event in self._aiter_stream_events_from_http(url, payload):
                yield event

    async def _aiter_stream_events_from_http(
        self, url: str, payload: dict,
    ) -> AsyncIterator[StreamEvent]:
        """Open the streaming POST and translate SSE events into StreamEvents.

        Uses ``httpx.AsyncClient.stream`` so the response body is read
        lazily via ``aiter_lines``. Each parsed SSE chunk is fed to
        ``_AsyncStreamIterator`` which produces one or more
        ``StreamEvent`` instances per SSE chunk.
        """
        async with self._client.stream("POST", url, json=payload) as response:
            if response.status_code != 200:
                # Read the body so error parsers see the full error
                # payload, then raise a typed provider exception.
                await response.aread()
                self._raise_for_status(response)

            sse_iter = aparse_sse_events_from_lines(response.aiter_lines())
            iterator = _AsyncStreamIterator(sse_iter)
            async for event in iterator:
                yield event


class _StreamIterator:
    """Async iterator that wraps synchronous SSE parsing."""

    def __init__(self, raw: str) -> None:
        self._events = list(parse_sse_events(raw))
        self._index = 0
        self._pending_tool_calls: dict[int, dict] = {}
        self._processed: list[StreamEvent] = []
        self._done = False
        self._build_events()

    def _build_events(self) -> None:
        events: list[StreamEvent] = []
        pending_tools: dict[int, dict] = {}
        _stop_emitted = False
        _last_usage: dict = {}

        for chunk in self._events:
            # Some providers (vLLM, Ollama) send usage in a standalone
            # final chunk with no choices.  Capture it regardless.
            chunk_usage = chunk.get("usage")
            if chunk_usage:
                _last_usage = chunk_usage

            choices = chunk.get("choices", [])
            for choice in choices:
                delta = choice.get("delta", {})
                finish_reason = choice.get("finish_reason")

                # Wave2-1a P2: thinking delta from provider-side reasoning
                # field. DeepSeek-R1, DeepSeek-reasoner, Qwen QwQ (vLLM),
                # and OpenAI o-series all emit this as a sibling to
                # ``content``. Emit a StreamThinkingDelta so the TUI's
                # existing thinking-buffer flush logic picks it up and
                # P3 assembly can accumulate it into a ThinkingBlock.
                reasoning_chunk = _extract_reasoning_text(delta)
                if reasoning_chunk:
                    events.append(StreamThinkingDelta(text=reasoning_chunk))

                # Text content delta
                text = delta.get("content")
                if text and isinstance(text, str):
                    events.append(StreamTextDelta(text=text))

                # Tool call deltas
                tool_calls = delta.get("tool_calls", [])
                for tc in tool_calls:
                    idx = tc.get("index", 0)
                    if idx not in pending_tools:
                        pending_tools[idx] = {
                            "id": tc.get("id", ""),
                            "name": tc.get("function", {}).get("name", ""),
                            "args": "",
                        }
                        if pending_tools[idx]["name"]:
                            events.append(
                                StreamToolUseStart(
                                    id=pending_tools[idx]["id"],
                                    name=pending_tools[idx]["name"],
                                )
                            )
                    # Accumulate argument fragments
                    args_fragment = tc.get("function", {}).get("arguments", "")
                    if args_fragment:
                        pending_tools[idx]["args"] += args_fragment
                        events.append(
                            StreamToolUseInputDelta(
                                id=pending_tools[idx]["id"],
                                partial_json=args_fragment,
                            )
                        )

                # Stop event — emitted exactly once at the end
                if finish_reason and not _stop_emitted:
                    _stop_emitted = True
                    usage_data = chunk_usage or _last_usage or {}
                    usage = _token_usage_from_dict(usage_data)
                    events.append(
                        StreamMessageStop(usage=usage, stop_reason=finish_reason)
                    )

        # If usage arrived in a trailing chunk after finish_reason, patch it
        if _stop_emitted and _last_usage:
            for i in range(len(events) - 1, -1, -1):
                if isinstance(events[i], StreamMessageStop):
                    existing = events[i]
                    if existing.usage.input_tokens == 0 and existing.usage.output_tokens == 0:
                        events[i] = StreamMessageStop(
                            usage=_token_usage_from_dict(_last_usage),
                            stop_reason=existing.stop_reason,
                        )
                    break

        self._processed = events

    def __aiter__(self):
        return self

    async def __anext__(self) -> StreamEvent:
        if self._index >= len(self._processed):
            raise StopAsyncIteration
        event = self._processed[self._index]
        self._index += 1
        return event


class _AsyncStreamIterator:
    """v2.6.1 M3 — async iterator over a *streaming* SSE source.

    Mirrors :class:`_StreamIterator`'s semantics (delta accumulation,
    pending tool-call assembly, single ``StreamMessageStop`` emission,
    trailing-usage patch) but consumes the SSE chunks LAZILY so each
    parsed event is forwarded as soon as the upstream chunk arrives.

    The buffered list pattern stays for the trailing-usage rewrite
    edge case: vLLM / Ollama send a final usage-only chunk AFTER the
    chunk that carries ``finish_reason``. Today that is rare in
    practice — the more common shape has usage on the same chunk —
    so the iterator yields the regular events as they arrive, holds
    only the most recent ``StreamMessageStop`` for usage patching,
    and emits it (or its patched replacement) once the upstream
    stream completes. This keeps TTFT the only metric that actually
    sees the streaming benefit; downstream renderers see the same
    final shape they did before.
    """

    def __init__(self, sse_iter: AsyncIterator[dict]) -> None:
        self._sse_iter = sse_iter
        self._pending_tools: dict[int, dict] = {}
        self._stop_emitted = False
        self._last_usage: dict = {}
        # Holds StreamEvents that have been parsed but not yet yielded
        # — needed because a single SSE chunk can produce multiple
        # StreamEvents (thinking + content + tool-call deltas).
        self._buffer: list[StreamEvent] = []
        # The pending stop event waits for end-of-stream so the
        # trailing-usage patch can rewrite it before the consumer
        # observes the final event.
        self._pending_stop: StreamMessageStop | None = None
        self._upstream_done = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> StreamEvent:
        # Drain the buffer first
        if self._buffer:
            return self._buffer.pop(0)

        # Pull more chunks from the SSE iterator until we either
        # produce events or exhaust upstream.
        while not self._buffer and not self._upstream_done:
            try:
                chunk = await self._sse_iter.__anext__()
            except StopAsyncIteration:
                self._upstream_done = True
                break
            self._process_chunk(chunk)

        if self._buffer:
            return self._buffer.pop(0)

        # Upstream fully drained — emit the held stop event (if any),
        # patched with any trailing usage seen after finish_reason.
        if self._pending_stop is not None:
            stop = self._pending_stop
            self._pending_stop = None
            if (
                self._last_usage
                and stop.usage.input_tokens == 0
                and stop.usage.output_tokens == 0
            ):
                stop = StreamMessageStop(
                    usage=_token_usage_from_dict(self._last_usage),
                    stop_reason=stop.stop_reason,
                )
            return stop

        raise StopAsyncIteration

    def _process_chunk(self, chunk: dict) -> None:
        """Translate one SSE chunk dict into queued StreamEvents.

        Mirrors ``_StreamIterator._build_events`` but feeds the
        buffer instead of accumulating into a list.
        """
        chunk_usage = chunk.get("usage")
        if chunk_usage:
            self._last_usage = chunk_usage

        choices = chunk.get("choices", [])
        for choice in choices:
            delta = choice.get("delta", {})
            finish_reason = choice.get("finish_reason")

            reasoning_chunk = _extract_reasoning_text(delta)
            if reasoning_chunk:
                self._buffer.append(StreamThinkingDelta(text=reasoning_chunk))

            text = delta.get("content")
            if text and isinstance(text, str):
                self._buffer.append(StreamTextDelta(text=text))

            tool_calls = delta.get("tool_calls", [])
            for tc in tool_calls:
                idx = tc.get("index", 0)
                if idx not in self._pending_tools:
                    self._pending_tools[idx] = {
                        "id": tc.get("id", ""),
                        "name": tc.get("function", {}).get("name", ""),
                        "args": "",
                    }
                    if self._pending_tools[idx]["name"]:
                        self._buffer.append(
                            StreamToolUseStart(
                                id=self._pending_tools[idx]["id"],
                                name=self._pending_tools[idx]["name"],
                            )
                        )
                args_fragment = tc.get("function", {}).get("arguments", "")
                if args_fragment:
                    self._pending_tools[idx]["args"] += args_fragment
                    self._buffer.append(
                        StreamToolUseInputDelta(
                            id=self._pending_tools[idx]["id"],
                            partial_json=args_fragment,
                        )
                    )

            if finish_reason and not self._stop_emitted:
                self._stop_emitted = True
                usage_data = chunk_usage or self._last_usage or {}
                usage = _token_usage_from_dict(usage_data)
                # Hold back the stop event until upstream drains so
                # any trailing usage chunk can patch its tokens.
                self._pending_stop = StreamMessageStop(
                    usage=usage, stop_reason=finish_reason,
                )

"""Async streaming facade + RateLimiter.acquire_async (M5 — Task 5.5)."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from llm_code.api.client import stream, stream_async
from llm_code.api.provider import LLMProvider
from llm_code.api.rate_limiter import (
    RateLimitHandler,
    RequestKind,
)
from llm_code.api.types import (
    Message,
    MessageRequest,
    MessageResponse,
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    TokenUsage,
)


# ---------------------------------------------------------------------------
# Stub provider used to drive the stream facade without network I/O
# ---------------------------------------------------------------------------


class _StubProvider(LLMProvider):
    def __init__(self, events: list[StreamEvent]):
        self._events = events

    async def send_message(self, request: MessageRequest) -> MessageResponse:  # pragma: no cover
        raise NotImplementedError

    async def stream_message(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        async def _iter():
            for e in self._events:
                await asyncio.sleep(0)
                yield e

        return _iter()

    def supports_native_tools(self) -> bool:
        return False

    def supports_images(self) -> bool:
        return False


def _make_request() -> MessageRequest:
    return MessageRequest(
        model="stub",
        messages=[Message(role="user", content="hi")],
        max_tokens=100,
    )


def _sample_events() -> list[StreamEvent]:
    return [
        StreamTextDelta(text="hello "),
        StreamTextDelta(text="world"),
        StreamMessageStop(
            usage=TokenUsage(input_tokens=0, output_tokens=0),
            stop_reason="end_turn",
        ),
    ]


# ---------------------------------------------------------------------------
# stream_async
# ---------------------------------------------------------------------------


class TestStreamAsync:
    async def test_yields_events_in_order(self):
        provider = _StubProvider(_sample_events())
        collected: list[StreamEvent] = []
        async for ev in stream_async(provider, _make_request()):
            collected.append(ev)
        assert len(collected) == 3
        assert isinstance(collected[0], StreamTextDelta)
        assert collected[0].text == "hello "
        assert collected[1].text == "world"

    async def test_empty_stream(self):
        provider = _StubProvider([])
        collected = [ev async for ev in stream_async(provider, _make_request())]
        assert collected == []

    async def test_propagates_provider_errors(self):
        class _BadProvider(_StubProvider):
            async def stream_message(self, request):  # type: ignore[override]
                async def _iter():
                    yield StreamTextDelta(text="partial")
                    raise RuntimeError("bang")

                return _iter()

        provider = _BadProvider([])
        collected: list[StreamEvent] = []
        with pytest.raises(RuntimeError, match="bang"):
            async for ev in stream_async(provider, _make_request()):
                collected.append(ev)
        assert len(collected) == 1


# ---------------------------------------------------------------------------
# Sync bridge — must REFUSE inside a running loop
# ---------------------------------------------------------------------------


class TestSyncStream:
    async def test_sync_stream_from_running_loop_raises(self):
        provider = _StubProvider(_sample_events())
        with pytest.raises(RuntimeError, match="running event loop"):
            list(stream(provider, _make_request()))

    def test_sync_stream_works_from_sync_context(self):
        provider = _StubProvider(_sample_events())
        items = list(stream(provider, _make_request()))
        assert len(items) == 3
        assert isinstance(items[0], StreamTextDelta)


# ---------------------------------------------------------------------------
# RateLimitHandler.acquire_async
# ---------------------------------------------------------------------------


class TestRateLimiterAcquireAsync:
    async def test_returns_immediately_when_no_overload_recorded(self):
        h = RateLimitHandler(request_kind=RequestKind.FOREGROUND)
        sleeps: list[float] = []

        async def _sleep(s):
            sleeps.append(s)

        await h.acquire_async(sleep=_sleep)
        assert sleeps == []

    async def test_sleeps_per_overload_schedule(self):
        h = RateLimitHandler(request_kind=RequestKind.FOREGROUND)
        h.overload_attempt = 1
        sleeps: list[float] = []

        async def _sleep(s):
            sleeps.append(s)

        await h.acquire_async(sleep=_sleep)
        assert len(sleeps) == 1
        assert sleeps[0] >= 30.0

    async def test_clamped_by_persistent_max(self):
        h = RateLimitHandler(request_kind=RequestKind.FOREGROUND)
        h.overload_attempt = 100  # beyond schedule
        sleeps: list[float] = []

        async def _sleep(s):
            sleeps.append(s)

        await h.acquire_async(sleep=_sleep)
        assert sleeps[0] <= 300.0

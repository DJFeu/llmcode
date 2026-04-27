"""v2.6.1 M3 — true SSE streaming for the OpenAI-compat provider.

Before v2.6.1 the streaming path called ``_post_with_retry`` which
buffered the entire response body BEFORE returning a Response,
turning ``_iter_stream_events(response.text)`` into an offline
parser. User-visible TTFT was full generation time.

These tests pin down the v2.6.1 behaviour:

* ``stream_message`` opens an httpx streaming POST (``client.stream``)
  rather than a buffered ``client.post``.
* Events arrive incrementally as chunks land — no whole-body wait.
* Status check still runs BEFORE the first yield so error responses
  surface as typed provider exceptions.
* Retry semantics on connect-time errors still apply.
* The async incremental SSE parser
  (``aparse_sse_events_from_lines``) is correct for split, partial
  and out-of-order line shapes.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from llm_code.api.openai_compat import OpenAICompatProvider
from llm_code.api.sse import aparse_sse_events_from_lines, parse_sse_events
from llm_code.api.types import (
    Message,
    MessageRequest,
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    TextBlock,
)


BASE_URL = "http://test-llm-stream.local"


def _make_provider(model: str = "qwen3", **kwargs) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        base_url=BASE_URL,
        api_key="test-key",
        model_name=model,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Async SSE parser correctness (aparse_sse_events_from_lines)
# ---------------------------------------------------------------------------


async def _alist(aiter):
    out = []
    async for item in aiter:
        out.append(item)
    return out


async def _line_iter_from_sse(sse: str):
    """Mimic ``httpx.Response.aiter_lines`` semantics: yields one line per
    iteration, no trailing newlines, and yields "" for blank separator lines.
    """
    # SSE blocks separated by blank lines. httpx.aiter_lines yields each
    # line without trailing CRLF, including the blank line.
    lines = sse.replace("\r\n", "\n").split("\n")
    for line in lines:
        await asyncio.sleep(0)
        yield line


class TestAparseSSEEventsFromLines:
    async def test_yields_single_event(self) -> None:
        body = (
            'data: {"a": 1}\n'
            '\n'
        )
        events = await _alist(aparse_sse_events_from_lines(_line_iter_from_sse(body)))
        assert events == [{"a": 1}]

    async def test_yields_multiple_events(self) -> None:
        body = (
            'data: {"i": 1}\n'
            '\n'
            'data: {"i": 2}\n'
            '\n'
            'data: {"i": 3}\n'
            '\n'
        )
        events = await _alist(aparse_sse_events_from_lines(_line_iter_from_sse(body)))
        assert events == [{"i": 1}, {"i": 2}, {"i": 3}]

    async def test_done_marker_stops(self) -> None:
        body = (
            'data: {"i": 1}\n'
            '\n'
            'data: [DONE]\n'
            '\n'
            'data: {"never_seen": true}\n'
            '\n'
        )
        events = await _alist(aparse_sse_events_from_lines(_line_iter_from_sse(body)))
        assert events == [{"i": 1}]

    async def test_comments_skipped(self) -> None:
        body = (
            ': heartbeat comment\n'
            'data: {"x": 1}\n'
            '\n'
        )
        events = await _alist(aparse_sse_events_from_lines(_line_iter_from_sse(body)))
        assert events == [{"x": 1}]

    async def test_malformed_json_skipped(self) -> None:
        body = (
            'data: {not json}\n'
            '\n'
            'data: {"y": 2}\n'
            '\n'
        )
        events = await _alist(aparse_sse_events_from_lines(_line_iter_from_sse(body)))
        assert events == [{"y": 2}]

    async def test_data_with_optional_space_stripped(self) -> None:
        body_no_space = 'data:{"a": 1}\n\n'
        body_with_space = 'data: {"a": 1}\n\n'
        e1 = await _alist(aparse_sse_events_from_lines(_line_iter_from_sse(body_no_space)))
        e2 = await _alist(aparse_sse_events_from_lines(_line_iter_from_sse(body_with_space)))
        assert e1 == e2 == [{"a": 1}]

    async def test_event_id_retry_fields_ignored(self) -> None:
        body = (
            'event: my_event\n'
            'id: 1234\n'
            'retry: 5000\n'
            'data: {"z": 3}\n'
            '\n'
        )
        events = await _alist(aparse_sse_events_from_lines(_line_iter_from_sse(body)))
        assert events == [{"z": 3}]

    async def test_trailing_block_without_blank_line_flushed(self) -> None:
        body = (
            'data: {"first": true}\n'
            '\n'
            'data: {"trailing": true}\n'
        )
        events = await _alist(aparse_sse_events_from_lines(_line_iter_from_sse(body)))
        assert events == [{"first": True}, {"trailing": True}]

    async def test_matches_sync_parser(self) -> None:
        """Both parsers must produce the same dicts on the same input."""
        body = (
            'data: {"a": 1}\n\n'
            'data: {"b": 2, "c": [1,2,3]}\n\n'
            'data: [DONE]\n\n'
        )
        sync_out = list(parse_sse_events(body))
        async_out = await _alist(aparse_sse_events_from_lines(_line_iter_from_sse(body)))
        assert sync_out == async_out


# ---------------------------------------------------------------------------
# stream_message — true streaming integration
# ---------------------------------------------------------------------------


class TestStreamMessageTrueStreaming:
    @respx.mock
    async def test_yields_text_delta_via_stream_path(self) -> None:
        """Smoke: the new streaming code path produces correct events."""
        sse_body = (
            'data: {"id":"1","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
            'data: {"id":"1","choices":[{"index":0,"delta":{"content":" there"},"finish_reason":null}]}\n\n'
            'data: {"id":"1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":3,"completion_tokens":4,"total_tokens":7}}\n\n'
            "data: [DONE]\n\n"
        )
        respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, text=sse_body,
                headers={"content-type": "text/event-stream"},
            )
        )
        provider = _make_provider()
        try:
            req = MessageRequest(
                model="qwen3",
                messages=(Message(role="user", content=(TextBlock(text="hi"),)),),
                stream=True,
            )
            events = []
            async for ev in await provider.stream_message(req):
                events.append(ev)

            text_events = [e for e in events if isinstance(e, StreamTextDelta)]
            stop_events = [e for e in events if isinstance(e, StreamMessageStop)]
            assert [e.text for e in text_events] == ["Hi", " there"]
            assert len(stop_events) == 1
            assert stop_events[0].stop_reason == "stop"
            # Usage tokens propagated
            assert stop_events[0].usage.input_tokens == 3
            assert stop_events[0].usage.output_tokens == 4
        finally:
            await provider.close()

    @respx.mock
    async def test_tool_call_streaming_assembled_correctly(self) -> None:
        sse_body = (
            'data: {"id":"2","choices":[{"index":0,"delta":'
            '{"tool_calls":[{"index":0,"id":"call_x","type":"function",'
            '"function":{"name":"read_file","arguments":""}}]},'
            '"finish_reason":null}]}\n\n'
            'data: {"id":"2","choices":[{"index":0,"delta":'
            '{"tool_calls":[{"index":0,"function":{"arguments":"{\\"p\\":\\"/a\\"}"}}]},'
            '"finish_reason":null}]}\n\n'
            'data: {"id":"2","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],'
            '"usage":{"prompt_tokens":5,"completion_tokens":7,"total_tokens":12}}\n\n'
            "data: [DONE]\n\n"
        )
        respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, text=sse_body,
                headers={"content-type": "text/event-stream"},
            )
        )
        provider = _make_provider()
        try:
            req = MessageRequest(
                model="qwen3",
                messages=(Message(role="user", content=(TextBlock(text="x"),)),),
                stream=True,
            )
            events = []
            async for ev in await provider.stream_message(req):
                events.append(ev)
            starts = [e for e in events if isinstance(e, StreamToolUseStart)]
            deltas = [e for e in events if isinstance(e, StreamToolUseInputDelta)]
            stops = [e for e in events if isinstance(e, StreamMessageStop)]
            assert len(starts) == 1
            assert starts[0].name == "read_file"
            assert len(deltas) == 1
            assert deltas[0].partial_json == '{"p":"/a"}'
            assert len(stops) == 1
            assert stops[0].stop_reason == "tool_calls"
        finally:
            await provider.close()

    @respx.mock
    async def test_status_error_raises_before_first_event(self) -> None:
        """Non-200 response surfaces as ProviderAuthError before any yield."""
        from llm_code.api.errors import ProviderAuthError

        respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                401, json={"error": {"message": "Invalid API key"}},
            )
        )
        provider = _make_provider()
        try:
            req = MessageRequest(
                model="qwen3",
                messages=(Message(role="user", content=(TextBlock(text="x"),)),),
                stream=True,
            )
            with pytest.raises(ProviderAuthError):
                events = []
                async for ev in await provider.stream_message(req):
                    events.append(ev)
                # No event should have been yielded
                assert events == []
        finally:
            await provider.close()

    @respx.mock
    async def test_streaming_uses_stream_method_not_post(self) -> None:
        """Verify the production path opens a streaming context.

        ``respx`` records every request regardless of method/streaming so
        we cannot directly assert ``client.stream`` vs ``client.post``;
        however we CAN observe that the body is consumed via the async
        iter path. As an indirect probe, monkeypatch the AsyncClient's
        ``stream`` method and assert it's called.
        """
        import contextlib
        import unittest.mock

        sse_body = (
            'data: {"id":"3","choices":[{"index":0,"delta":{"content":"x"},'
            '"finish_reason":null}]}\n\n'
            'data: {"id":"3","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200, text=sse_body,
                headers={"content-type": "text/event-stream"},
            )
        )
        provider = _make_provider()
        try:
            real_stream = provider._client.stream
            calls: list[tuple] = []

            @contextlib.asynccontextmanager
            async def _spy_stream(*args, **kwargs):
                calls.append((args, kwargs))
                async with real_stream(*args, **kwargs) as r:
                    yield r

            with unittest.mock.patch.object(
                provider._client, "stream", side_effect=_spy_stream,
            ):
                req = MessageRequest(
                    model="qwen3",
                    messages=(Message(role="user", content=(TextBlock(text="x"),)),),
                    stream=True,
                )
                events = []
                async for ev in await provider.stream_message(req):
                    events.append(ev)

            # The new code path MUST go through client.stream
            assert len(calls) >= 1
        finally:
            await provider.close()

    async def test_chunks_arrive_incrementally_via_mock_transport(self) -> None:
        """Verify each SSE chunk produces an event as it arrives, not
        only after the full body. Uses a MockTransport whose stream is
        an httpx.AsyncByteStream that yields chunks with explicit
        awaits between them — every chunk MUST translate to a
        downstream StreamEvent before the next chunk lands.
        """
        chunks_payload = [
            b'data: {"id":"4","choices":[{"index":0,"delta":{"content":"alpha"},"finish_reason":null}]}\n\n',
            b'data: {"id":"4","choices":[{"index":0,"delta":{"content":"beta"},"finish_reason":null}]}\n\n',
            b'data: {"id":"4","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
            b'data: [DONE]\n\n',
        ]

        # Track when each chunk is requested by the parser so we can
        # assert that chunk N produced an event before chunk N+1 was
        # pulled from the transport.
        chunk_pull_order: list[int] = []
        events_seen_at_pull: list[list[str]] = []
        events_observed: list[StreamEvent] = []

        class _ByChunkStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for i, c in enumerate(chunks_payload):
                    chunk_pull_order.append(i)
                    events_seen_at_pull.append(
                        [type(e).__name__ for e in events_observed]
                    )
                    await asyncio.sleep(0)
                    yield c

            async def aclose(self) -> None:
                return None

        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                stream=_ByChunkStream(),
                headers={"content-type": "text/event-stream"},
            )

        transport = httpx.MockTransport(_handler)
        client = httpx.AsyncClient(transport=transport)

        provider = _make_provider()
        old_client = provider._client
        provider._client = client
        try:
            req = MessageRequest(
                model="qwen3",
                messages=(Message(role="user", content=(TextBlock(text="x"),)),),
                stream=True,
            )
            async for ev in await provider.stream_message(req):
                events_observed.append(ev)

            # Functional check: events arrived in the right order.
            text_events = [e for e in events_observed if isinstance(e, StreamTextDelta)]
            stop_events = [e for e in events_observed if isinstance(e, StreamMessageStop)]
            assert [e.text for e in text_events] == ["alpha", "beta"]
            assert len(stop_events) == 1

            # Streaming check: by the time chunk index 1 was pulled
            # (the 'beta' content chunk), the consumer should already
            # have observed the 'alpha' StreamTextDelta from chunk 0.
            assert chunk_pull_order == [0, 1, 2, 3]
            assert "StreamTextDelta" in events_seen_at_pull[1], (
                f"streaming did not deliver chunk 0 events before "
                f"chunk 1 was pulled — observed: {events_seen_at_pull}"
            )
        finally:
            await client.aclose()
            await old_client.aclose()


# ---------------------------------------------------------------------------
# Non-streaming path (send_message) is unchanged
# ---------------------------------------------------------------------------


class TestNonStreamingPathUnchanged:
    @respx.mock
    async def test_send_message_still_uses_post(self) -> None:
        """send_message buffers — must NOT regress to streaming path."""
        respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )
        )
        provider = _make_provider()
        try:
            req = MessageRequest(
                model="qwen3",
                messages=(Message(role="user", content=(TextBlock(text="hi"),)),),
            )
            resp = await provider.send_message(req)
            assert resp.stop_reason == "stop"
        finally:
            await provider.close()


# ---------------------------------------------------------------------------
# Retry semantics still work for connect-time failures
# ---------------------------------------------------------------------------


class TestStreamingRetrySemantics:
    @respx.mock
    async def test_retry_on_500_then_success(self) -> None:
        """Connect-time 500 retries; eventual success streams normally."""
        sse_body = (
            'data: {"id":"5","choices":[{"index":0,"delta":{"content":"ok"},'
            '"finish_reason":null}]}\n\n'
            'data: {"id":"5","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        route = respx.post(f"{BASE_URL}/chat/completions")
        route.side_effect = [
            httpx.Response(500, text="boom"),
            httpx.Response(
                200, text=sse_body,
                headers={"content-type": "text/event-stream"},
            ),
        ]
        provider = _make_provider(max_retries=3)
        try:
            req = MessageRequest(
                model="qwen3",
                messages=(Message(role="user", content=(TextBlock(text="x"),)),),
                stream=True,
            )
            text_chunks = []
            async for ev in await provider.stream_message(req):
                if isinstance(ev, StreamTextDelta):
                    text_chunks.append(ev.text)
            assert text_chunks == ["ok"]
            # Confirm retry happened: respx route was called twice
            assert route.call_count == 2
        finally:
            await provider.close()

    @respx.mock
    async def test_non_retryable_propagates_immediately(self) -> None:
        from llm_code.api.errors import ProviderAuthError

        route = respx.post(f"{BASE_URL}/chat/completions")
        route.return_value = httpx.Response(401, text="bad key")
        provider = _make_provider(max_retries=3)
        try:
            req = MessageRequest(
                model="qwen3",
                messages=(Message(role="user", content=(TextBlock(text="x"),)),),
                stream=True,
            )
            with pytest.raises(ProviderAuthError):
                async for _ in await provider.stream_message(req):
                    pass
            # Auth errors are non-retryable: only one call
            assert route.call_count == 1
        finally:
            await provider.close()

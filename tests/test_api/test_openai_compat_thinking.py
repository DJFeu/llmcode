"""Wave2-1a P2: inbound parsing of provider thinking content.

Covers the five provider shapes identified in the spec:

    - DeepSeek-R1 / DeepSeek-reasoner / Qwen QwQ / vLLM: ``reasoning_content``
    - OpenAI o-series: ``reasoning``
    - Anthropic-native (via proxy): list of structured blocks with
      ``type == "thinking"`` and opaque ``signature``

All handled by ``llm_code.api.openai_compat``. After P2 the parser
emits ``StreamThinkingDelta`` for streaming responses and populates
``MessageResponse.thinking`` for non-streaming responses. Nothing
downstream consumes those yet — assembly into ``Message.content``
happens in P3.
"""
from __future__ import annotations

import json

import httpx
import pytest

from llm_code.api.openai_compat import (
    OpenAICompatProvider,
    _extract_anthropic_thinking,
    _extract_reasoning_text,
)
from llm_code.api.types import (
    StreamTextDelta,
    StreamThinkingDelta,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)


# ---------- Unit: _extract_reasoning_text ----------

def test_extract_reasoning_prefers_reasoning_content_field() -> None:
    """DeepSeek / Qwen shape wins when both candidate fields are present
    — we declare the lookup order in the module and pin it here so a
    future reorder doesn't silently change behavior."""
    assert (
        _extract_reasoning_text({"reasoning_content": "A", "reasoning": "B"})
        == "A"
    )


def test_extract_reasoning_falls_back_to_reasoning_field() -> None:
    """OpenAI o-series shape is the fallback when reasoning_content is
    absent or empty."""
    assert _extract_reasoning_text({"reasoning": "o-series thinking"}) == "o-series thinking"


def test_extract_reasoning_returns_empty_when_no_field_present() -> None:
    assert _extract_reasoning_text({"content": "regular text"}) == ""


def test_extract_reasoning_ignores_non_string_fields() -> None:
    """A proxy might send a null / object under the field; we must not
    crash and must not fabricate content."""
    assert _extract_reasoning_text({"reasoning_content": None}) == ""
    assert _extract_reasoning_text({"reasoning_content": {"nested": "x"}}) == ""
    assert _extract_reasoning_text({"reasoning_content": 42}) == ""


def test_extract_reasoning_empty_string_is_treated_as_absent() -> None:
    """An empty string must not produce a zero-length ThinkingBlock —
    otherwise every turn without thinking would still emit one."""
    assert _extract_reasoning_text({"reasoning_content": ""}) == ""


# ---------- Unit: _extract_anthropic_thinking ----------

def test_extract_anthropic_thinking_picks_thinking_blocks_only() -> None:
    """Walk a structured content list, keep thinking entries, ignore
    text / tool_use / unknown types."""
    content = [
        {"type": "thinking", "thinking": "first thought", "signature": "sig-1"},
        {"type": "text", "text": "visible reply"},
        {"type": "thinking", "thinking": "second thought", "signature": "sig-2"},
        {"type": "tool_use", "id": "t1", "name": "search", "input": {}},
    ]
    blocks = _extract_anthropic_thinking(content)
    assert len(blocks) == 2
    assert blocks[0] == ThinkingBlock(content="first thought", signature="sig-1")
    assert blocks[1] == ThinkingBlock(content="second thought", signature="sig-2")


def test_extract_anthropic_thinking_preserves_signature_opaquely() -> None:
    """Signature bytes must not be normalized / trimmed / decoded.
    Anthropic signs blocks with base64 that can contain unicode and
    trailing whitespace — any mutation breaks round-trip verification."""
    tricky_sig = "abc+/==\n  \u00e9\u00a0trailing"
    content = [{"type": "thinking", "thinking": "x", "signature": tricky_sig}]
    blocks = _extract_anthropic_thinking(content)
    assert blocks[0].signature == tricky_sig
    assert len(blocks[0].signature) == len(tricky_sig)


def test_extract_anthropic_thinking_defaults_signature_to_empty() -> None:
    """A provider that sends thinking without signature must still
    produce a valid ThinkingBlock, with signature defaulted to ''."""
    content = [{"type": "thinking", "thinking": "unsigned"}]
    blocks = _extract_anthropic_thinking(content)
    assert blocks == (ThinkingBlock(content="unsigned", signature=""),)


def test_extract_anthropic_thinking_rejects_non_list_input() -> None:
    """Scalar ``content`` (the typical OpenAI-compat shape) must not
    crash the helper — it just returns an empty tuple."""
    assert _extract_anthropic_thinking("just a string") == ()
    assert _extract_anthropic_thinking(None) == ()
    assert _extract_anthropic_thinking({}) == ()


def test_extract_anthropic_thinking_skips_malformed_entries() -> None:
    """List may contain non-dict or dict-without-thinking entries —
    skip them gracefully instead of raising."""
    content = [
        "scalar",
        {"type": "thinking"},  # missing thinking field
        {"type": "thinking", "thinking": 42},  # wrong type
        {"type": "thinking", "thinking": "valid"},
    ]
    blocks = _extract_anthropic_thinking(content)
    assert blocks == (ThinkingBlock(content="valid", signature=""),)


# ---------- Integration: non-streaming _parse_response ----------

def _make_provider() -> OpenAICompatProvider:
    return OpenAICompatProvider(base_url="http://localhost:0", api_key="")


def _fake_response(body: dict) -> httpx.Response:
    """Build an httpx.Response suitable for feeding to _parse_response."""
    return httpx.Response(
        status_code=200,
        content=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


def test_parse_response_extracts_deepseek_reasoning_content() -> None:
    provider = _make_provider()
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "visible answer",
                    "reasoning_content": "step 1: think about X\nstep 2: conclude Y",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }
    result = provider._parse_response(_fake_response(body))
    assert len(result.thinking) == 1
    assert "step 1" in result.thinking[0].content
    assert result.thinking[0].signature == ""
    # Visible content is unchanged
    assert result.content == (TextBlock(text="visible answer"),)


def test_parse_response_extracts_openai_o_series_reasoning() -> None:
    provider = _make_provider()
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "final answer",
                    "reasoning": "o-series internal reasoning trace",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }
    result = provider._parse_response(_fake_response(body))
    assert result.thinking == (
        ThinkingBlock(content="o-series internal reasoning trace", signature=""),
    )


def test_parse_response_handles_anthropic_structured_content() -> None:
    """Anthropic proxies may forward responses with ``content`` as a
    list of blocks. Thinking blocks become MessageResponse.thinking,
    text blocks become MessageResponse.content TextBlocks, and
    signatures survive intact."""
    provider = _make_provider()
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "let me think", "signature": "opaque-xyz"},
                        {"type": "text", "text": "visible answer"},
                    ],
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }
    result = provider._parse_response(_fake_response(body))
    assert result.thinking == (
        ThinkingBlock(content="let me think", signature="opaque-xyz"),
    )
    assert result.content == (TextBlock(text="visible answer"),)


def test_parse_response_without_reasoning_leaves_thinking_empty() -> None:
    """Non-reasoning providers (gpt-4o, sonnet, etc.) produce no
    thinking — MessageResponse.thinking must default to empty tuple
    so downstream consumers can rely on len(thinking)==0 as "absent"."""
    provider = _make_provider()
    body = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "just text"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    result = provider._parse_response(_fake_response(body))
    assert result.thinking == ()


def test_parse_response_reasoning_with_tool_call_keeps_both() -> None:
    """Reasoning providers emit reasoning_content alongside tool_calls
    (the visible content may be empty). Both must land on the
    MessageResponse."""
    provider = _make_provider()
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "I should call the search tool",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "search",
                                "arguments": '{"q": "wave2"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }
    result = provider._parse_response(_fake_response(body))
    assert result.thinking == (
        ThinkingBlock(content="I should call the search tool", signature=""),
    )
    assert len(result.content) == 1
    assert isinstance(result.content[0], ToolUseBlock)
    assert result.content[0].name == "search"


# ---------- Integration: streaming iterator ----------

def _sse(chunks: list[dict]) -> str:
    """Build an SSE payload from a list of chunk dicts."""
    lines = []
    for chunk in chunks:
        lines.append(f"data: {json.dumps(chunk)}")
        lines.append("")  # blank line between events
    lines.append("data: [DONE]")
    lines.append("")
    return "\n".join(lines)


async def _collect(iterator) -> list:
    out = []
    async for event in iterator:
        out.append(event)
    return out


@pytest.mark.asyncio
async def test_stream_emits_thinking_delta_from_reasoning_content() -> None:
    """DeepSeek-R1 streams reasoning_content chunks. The parser must
    emit a StreamThinkingDelta for each chunk so the TUI's existing
    thinking flush logic picks them up."""
    raw = _sse([
        {"choices": [{"delta": {"reasoning_content": "let me "}, "finish_reason": None}]},
        {"choices": [{"delta": {"reasoning_content": "think..."}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "the answer is 42"}, "finish_reason": None}]},
        {
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        },
    ])
    provider = _make_provider()
    events = await _collect(provider._iter_stream_events(raw))
    thinking_events = [e for e in events if isinstance(e, StreamThinkingDelta)]
    text_events = [e for e in events if isinstance(e, StreamTextDelta)]
    assert len(thinking_events) == 2
    assert thinking_events[0].text == "let me "
    assert thinking_events[1].text == "think..."
    assert len(text_events) == 1
    assert text_events[0].text == "the answer is 42"


@pytest.mark.asyncio
async def test_stream_emits_thinking_delta_from_openai_reasoning_field() -> None:
    """OpenAI o-series newer SDK uses ``reasoning`` instead of
    ``reasoning_content``. Both must work."""
    raw = _sse([
        {"choices": [{"delta": {"reasoning": "internal trace"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "answer"}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    ])
    provider = _make_provider()
    events = await _collect(provider._iter_stream_events(raw))
    thinking = [e for e in events if isinstance(e, StreamThinkingDelta)]
    assert thinking == [StreamThinkingDelta(text="internal trace")]


@pytest.mark.asyncio
async def test_stream_ignores_empty_reasoning_chunks() -> None:
    """Providers sometimes send empty-string reasoning deltas between
    content chunks; we must not emit a zero-length StreamThinkingDelta."""
    raw = _sse([
        {"choices": [{"delta": {"reasoning_content": ""}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
    ])
    provider = _make_provider()
    events = await _collect(provider._iter_stream_events(raw))
    assert not any(isinstance(e, StreamThinkingDelta) for e in events)


@pytest.mark.asyncio
async def test_stream_reasoning_independent_of_content_delta() -> None:
    """Thinking and content can interleave in a single chunk. Both
    event types must be emitted for that chunk, in a stable order:
    thinking first (so the TUI flushes it before the text deltas)."""
    raw = _sse([
        {"choices": [{"delta": {
            "reasoning_content": "reasoning chunk",
            "content": "text chunk",
        }, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
    ])
    provider = _make_provider()
    events = await _collect(provider._iter_stream_events(raw))
    # Filter out StreamMessageStop and tool events
    relevant = [e for e in events if isinstance(e, (StreamThinkingDelta, StreamTextDelta))]
    assert len(relevant) == 2
    assert isinstance(relevant[0], StreamThinkingDelta)
    assert isinstance(relevant[1], StreamTextDelta)

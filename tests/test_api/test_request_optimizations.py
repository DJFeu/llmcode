"""Tests for v15 M1 — request optimizations (trivial-call interception).

Coverage:

* Per-detector positive case (5 detectors fire on canonical input).
* Per-detector negative case (slightly off-pattern → returns None).
* Co-occurring signals — first matching detector wins.
* Registry order assertion.
* Profile flag OFF disables interception in both providers.
* ``_synthesize_stream_events`` event sequence.
* Provider integration: optimizable request → no HTTP call;
  non-optimizable → HTTP call happens.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from llm_code.api.anthropic_provider import AnthropicProvider
from llm_code.api.openai_compat import OpenAICompatProvider
from llm_code.api.request_optimizations import (
    OptimizationHit,
    _DETECTORS,
    _filepath_mock,
    _prefix_detection,
    _quota_mock,
    _suggestion_skip,
    _synthesize_stream_events,
    _title_skip,
    try_optimize,
)
from llm_code.api.types import (
    Message,
    MessageRequest,
    MessageResponse,
    StreamMessageStart,
    StreamMessageStop,
    StreamTextDelta,
    TextBlock,
    TokenUsage,
    ToolDefinition,
)


def _user(text: str) -> Message:
    """Shorthand for ``Message(role="user", content=(TextBlock(text),))``."""
    return Message(role="user", content=(TextBlock(text=text),))


def _request(
    *,
    messages: tuple[Message, ...] = (),
    system: str | None = None,
    max_tokens: int = 4096,
    tools: tuple[ToolDefinition, ...] = (),
) -> MessageRequest:
    return MessageRequest(
        model="test-model",
        messages=messages,
        system=system,
        max_tokens=max_tokens,
        tools=tools,
    )


# ── Detector: quota_mock ──────────────────────────────────────────────


class TestQuotaMock:
    def test_canonical_quota_probe_matches(self) -> None:
        req = _request(
            messages=(_user("Are you within quota?"),),
            max_tokens=1,
        )
        out = _quota_mock(req)
        assert out is not None
        assert isinstance(out.content[0], TextBlock)
        assert out.content[0].text == "Quota check passed."
        assert out.usage.output_tokens == 5

    def test_max_tokens_not_one_returns_none(self) -> None:
        req = _request(
            messages=(_user("Are you within quota?"),),
            max_tokens=4096,
        )
        assert _quota_mock(req) is None

    def test_no_quota_substring_returns_none(self) -> None:
        req = _request(messages=(_user("Hello!"),), max_tokens=1)
        assert _quota_mock(req) is None

    def test_multiple_messages_returns_none(self) -> None:
        # Quota probes are single-shot; reject conversations.
        req = _request(
            messages=(_user("quota?"), _user("again?")),
            max_tokens=1,
        )
        assert _quota_mock(req) is None


# ── Detector: prefix_detection ────────────────────────────────────────


class TestPrefixDetection:
    def test_canonical_prefix_request_matches(self) -> None:
        req = _request(
            messages=(_user(
                "<policy_spec>...</policy_spec>\n"
                "Command: git commit -m 'fix'"
            ),),
        )
        out = _prefix_detection(req)
        assert out is not None
        assert out.content[0].text == "git commit"

    def test_two_word_command_with_flag_subcommand(self) -> None:
        req = _request(
            messages=(_user(
                "<policy_spec>x</policy_spec>\nCommand: npm --silent install"
            ),),
        )
        out = _prefix_detection(req)
        assert out is not None
        # ``--silent`` is a flag, not a subcommand → fall back to
        # single-word prefix.
        assert out.content[0].text == "npm"

    def test_command_injection_rejected(self) -> None:
        req = _request(
            messages=(_user(
                "<policy_spec>x</policy_spec>\nCommand: ls `whoami`"
            ),),
        )
        out = _prefix_detection(req)
        assert out is not None
        assert out.content[0].text == "command_injection_detected"

    def test_no_policy_spec_returns_none(self) -> None:
        req = _request(messages=(_user("Command: git status"),))
        assert _prefix_detection(req) is None

    def test_no_command_marker_returns_none(self) -> None:
        req = _request(
            messages=(_user("<policy_spec>x</policy_spec>"),),
        )
        assert _prefix_detection(req) is None


# ── Detector: title_skip ──────────────────────────────────────────────


class TestTitleSkip:
    def test_sentence_case_title_matches(self) -> None:
        req = _request(
            messages=(_user("..."),),
            system=(
                "Generate a short, sentence-case title for this "
                "conversation. Return JSON with a single 'title' field."
            ),
        )
        out = _title_skip(req)
        assert out is not None
        assert out.content[0].text == "Conversation"

    def test_compound_phrasing_matches(self) -> None:
        req = _request(
            messages=(_user("..."),),
            system=(
                "Return JSON with a 'title' field summarizing this "
                "coding session."
            ),
        )
        out = _title_skip(req)
        assert out is not None
        assert out.content[0].text == "Conversation"

    def test_no_system_returns_none(self) -> None:
        req = _request(messages=(_user("title?"),))
        assert _title_skip(req) is None

    def test_with_tools_returns_none(self) -> None:
        # Title-generation prompts never include tools — bail out.
        req = _request(
            messages=(_user("..."),),
            system="Generate a sentence-case title for this session.",
            tools=(
                ToolDefinition(
                    name="x", description="", input_schema={}
                ),
            ),
        )
        assert _title_skip(req) is None

    def test_unrelated_system_returns_none(self) -> None:
        req = _request(
            messages=(_user("..."),),
            system="You are a helpful assistant.",
        )
        assert _title_skip(req) is None


# ── Detector: suggestion_skip ─────────────────────────────────────────


class TestSuggestionSkip:
    def test_canonical_suggestion_marker_matches(self) -> None:
        req = _request(
            messages=(_user("[SUGGESTION MODE: completion]"),),
        )
        out = _suggestion_skip(req)
        assert out is not None
        assert out.content[0].text == ""
        assert out.usage.output_tokens == 1

    def test_marker_anywhere_in_conversation_matches(self) -> None:
        req = _request(messages=(
            _user("hello"),
            Message(role="assistant", content=(TextBlock(text="hi"),)),
            _user("[SUGGESTION MODE: x] please continue"),
        ))
        assert _suggestion_skip(req) is not None

    def test_lowercase_marker_returns_none(self) -> None:
        # Marker is case-sensitive — Claude Code uses uppercase.
        req = _request(messages=(_user("[suggestion mode: x]"),))
        assert _suggestion_skip(req) is None


# ── Detector: filepath_mock ───────────────────────────────────────────


class TestFilepathMock:
    def test_user_filepaths_keyword_matches(self) -> None:
        req = _request(
            messages=(_user(
                "Extract filepaths.\n"
                "Command: cat src/main.py src/util.py\n"
                "Output: hello\n\nworld"
            ),),
        )
        out = _filepath_mock(req)
        assert out is not None
        assert "<filepaths>" in out.content[0].text
        assert "src/main.py" in out.content[0].text
        assert "src/util.py" in out.content[0].text

    def test_grep_with_pattern_skips_first_positional(self) -> None:
        req = _request(
            messages=(_user(
                "filepaths\n"
                "Command: grep TODO src/foo.py src/bar.py\n"
                "Output: matches"
            ),),
        )
        out = _filepath_mock(req)
        assert out is not None
        # First positional ("TODO") is the pattern; remaining are files.
        text = out.content[0].text
        assert "src/foo.py" in text
        assert "src/bar.py" in text
        assert "TODO" not in text

    def test_grep_with_e_flag_keeps_all_positionals(self) -> None:
        req = _request(
            messages=(_user(
                "filepaths\n"
                "Command: grep -e TODO src/foo.py src/bar.py\n"
                "Output: x"
            ),),
        )
        out = _filepath_mock(req)
        assert out is not None
        # ``-e TODO`` provides the pattern via flag — all positional
        # args are paths.
        assert "src/foo.py" in out.content[0].text
        assert "src/bar.py" in out.content[0].text

    def test_listing_command_returns_empty_block(self) -> None:
        req = _request(
            messages=(_user(
                "filepaths\nCommand: ls -la /tmp\nOutput: a.txt"
            ),),
        )
        out = _filepath_mock(req)
        assert out is not None
        assert out.content[0].text == "<filepaths>\n</filepaths>"

    def test_no_command_returns_none(self) -> None:
        req = _request(messages=(_user("filepaths only"),))
        assert _filepath_mock(req) is None

    def test_with_tools_returns_none(self) -> None:
        req = _request(
            messages=(_user(
                "filepaths\nCommand: cat x\nOutput: y"
            ),),
            tools=(
                ToolDefinition(
                    name="x", description="", input_schema={}
                ),
            ),
        )
        assert _filepath_mock(req) is None


# ── Registry walker ───────────────────────────────────────────────────


class TestTryOptimize:
    def test_quota_wins_over_other_detectors(self) -> None:
        # Quota probe is the first in the registry; verify it short-
        # circuits before later detectors get a chance.
        req = _request(
            messages=(_user("quota check"),),
            max_tokens=1,
        )
        hit = try_optimize(req)
        assert hit is not None
        assert hit.name == "quota_mock"

    def test_no_match_returns_none(self) -> None:
        req = _request(messages=(_user("just a normal question"),))
        assert try_optimize(req) is None

    def test_returns_optimization_hit_with_name(self) -> None:
        req = _request(
            messages=(_user("[SUGGESTION MODE: x]"),),
        )
        hit = try_optimize(req)
        assert isinstance(hit, OptimizationHit)
        assert hit.name == "suggestion_skip"

    def test_registry_declaration_order(self) -> None:
        names = tuple(name for name, _ in _DETECTORS)
        assert names == (
            "quota_mock",
            "prefix_detection",
            "title_skip",
            "suggestion_skip",
            "filepath_mock",
        )


# ── _synthesize_stream_events ─────────────────────────────────────────


class TestSynthesizeStreamEvents:
    @pytest.mark.asyncio
    async def test_emits_three_events_for_text_response(self) -> None:
        response = MessageResponse(
            content=(TextBlock(text="Conversation"),),
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )
        events = []
        async for ev in _synthesize_stream_events(response):
            events.append(ev)

        assert len(events) == 3
        assert isinstance(events[0], StreamMessageStart)
        assert isinstance(events[1], StreamTextDelta)
        assert events[1].text == "Conversation"
        assert isinstance(events[2], StreamMessageStop)
        assert events[2].usage.output_tokens == 5

    @pytest.mark.asyncio
    async def test_empty_text_skips_delta(self) -> None:
        response = MessageResponse(
            content=(TextBlock(text=""),),
            usage=TokenUsage(input_tokens=10, output_tokens=1),
            stop_reason="end_turn",
        )
        events = []
        async for ev in _synthesize_stream_events(response):
            events.append(ev)
        # MessageStart + MessageStop only — no delta for empty text.
        assert len(events) == 2
        assert isinstance(events[0], StreamMessageStart)
        assert isinstance(events[1], StreamMessageStop)

    @pytest.mark.asyncio
    async def test_multi_block_emits_one_delta_per_block(self) -> None:
        response = MessageResponse(
            content=(
                TextBlock(text="hello "),
                TextBlock(text="world"),
            ),
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        )
        events = []
        async for ev in _synthesize_stream_events(response):
            events.append(ev)
        # MessageStart + 2 deltas + MessageStop.
        assert len(events) == 4
        assert isinstance(events[1], StreamTextDelta)
        assert events[1].text == "hello "
        assert isinstance(events[2], StreamTextDelta)
        assert events[2].text == "world"


# ── Provider integration ──────────────────────────────────────────────


class TestProviderIntegrationOpenAICompat:
    """End-to-end: optimizable request → zero HTTP calls."""

    @pytest.mark.asyncio
    async def test_quota_request_short_circuits(self) -> None:
        provider = OpenAICompatProvider(
            base_url="http://example.com",
            api_key="x",
            model_name="default",
        )
        try:
            with patch.object(
                provider._client, "post",
                new=AsyncMock(return_value=httpx.Response(200, json={})),
            ) as mock_post:
                req = _request(
                    messages=(_user("Quota?"),),
                    max_tokens=1,
                )
                resp = await provider.send_message(req)
                # Synthetic response — no HTTP.
                assert mock_post.call_count == 0
                assert resp.content[0].text == "Quota check passed."
        finally:
            await provider.close()

    @pytest.mark.asyncio
    async def test_non_optimizable_request_hits_http(self) -> None:
        """A normal question routes through the real HTTP path."""

        provider = OpenAICompatProvider(
            base_url="http://example.com",
            api_key="x",
            model_name="default",
        )
        try:
            mock_response = httpx.Response(
                200,
                json={
                    "choices": [{
                        "message": {"content": "hello"},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )
            with patch.object(
                provider._client, "post",
                new=AsyncMock(return_value=mock_response),
            ) as mock_post:
                req = _request(
                    messages=(_user("What is the capital of France?"),),
                )
                await provider.send_message(req)
                assert mock_post.call_count == 1
        finally:
            await provider.close()

    @pytest.mark.asyncio
    async def test_profile_flag_off_disables_interception(self) -> None:
        from dataclasses import replace

        provider = OpenAICompatProvider(
            base_url="http://example.com",
            api_key="x",
            model_name="default",
        )
        # Force the flag off — every call must hit HTTP.
        provider._profile = replace(
            provider._profile, enable_request_optimizations=False,
        )
        try:
            mock_response = httpx.Response(
                200,
                json={
                    "choices": [{
                        "message": {"content": "ok"},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )
            with patch.object(
                provider._client, "post",
                new=AsyncMock(return_value=mock_response),
            ) as mock_post:
                req = _request(
                    messages=(_user("Quota?"),),
                    max_tokens=1,
                )
                await provider.send_message(req)
                # Flag off — interception bypassed, real HTTP fires.
                assert mock_post.call_count == 1
        finally:
            await provider.close()


class TestProviderIntegrationAnthropic:
    @pytest.mark.asyncio
    async def test_title_request_short_circuits(self) -> None:
        provider = AnthropicProvider(
            api_key="x",
            model_name="claude-sonnet-4-6",
        )
        try:
            with patch.object(
                provider._client, "post",
                new=AsyncMock(return_value=httpx.Response(200, json={})),
            ) as mock_post:
                req = _request(
                    messages=(_user("..."),),
                    system=(
                        "Return JSON with a 'title' field summarizing "
                        "this coding session."
                    ),
                )
                resp = await provider.send_message(req)
                assert mock_post.call_count == 0
                assert resp.content[0].text == "Conversation"
        finally:
            await provider.close()

    @pytest.mark.asyncio
    async def test_streaming_short_circuit_emits_stream_events(self) -> None:
        provider = AnthropicProvider(
            api_key="x",
            model_name="claude-sonnet-4-6",
        )
        try:
            req = _request(
                messages=(_user("..."),),
                system=(
                    "Generate a sentence-case title for this session. "
                    "Return JSON with a 'title' field."
                ),
            )
            stream = await provider.stream_message(req)
            events = [ev async for ev in stream]
            assert len(events) == 3
            assert isinstance(events[0], StreamMessageStart)
            assert isinstance(events[1], StreamTextDelta)
            assert events[1].text == "Conversation"
            assert isinstance(events[2], StreamMessageStop)
        finally:
            await provider.close()

    @pytest.mark.asyncio
    async def test_profile_flag_off_disables_interception(self) -> None:
        from dataclasses import replace

        provider = AnthropicProvider(
            api_key="x",
            model_name="claude-sonnet-4-6",
        )
        provider._profile = replace(
            provider._profile, enable_request_optimizations=False,
        )
        try:
            mock_response = httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": "ok"}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "stop_reason": "end_turn",
                },
            )
            with patch.object(
                provider._client, "post",
                new=AsyncMock(return_value=mock_response),
            ) as mock_post:
                req = _request(
                    messages=(_user("..."),),
                    system="Return JSON with a 'title' field.",
                    max_tokens=4096,
                )
                # Title-skip would normally fire — flag off lets the
                # real HTTP call through.
                await provider.send_message(req)
                assert mock_post.call_count == 1
        finally:
            await provider.close()

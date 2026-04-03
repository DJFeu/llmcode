"""Tests for the 7-layer recovery mechanism.

Covers:
- Layer: 529 Overload handling in OpenAICompatProvider._post_with_retry
- Layer: Token limit auto-upgrade in ConversationRuntime
- Layer: Model fallback after 3 consecutive provider errors
"""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from llm_code.api.errors import ProviderConnectionError, ProviderOverloadError
from llm_code.api.openai_compat import OpenAICompatProvider
from llm_code.api.types import (
    MessageRequest,
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    TokenUsage,
)
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.session import Session
from llm_code.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_context(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        cwd=tmp_path,
        is_git_repo=False,
        git_status="",
        instructions="",
    )


def _make_text_stream(stop_reason: str = "end_turn") -> AsyncIterator[StreamEvent]:
    """Async generator that yields a simple text stream with given stop reason."""
    async def _gen():
        yield StreamTextDelta(text="Hello")
        yield StreamMessageStop(usage=TokenUsage(10, 5), stop_reason=stop_reason)
    return _gen()


def _make_runtime(
    tmp_path: Path,
    provider,
    model: str = "",
    fallback_model: str = "",
    max_tokens: int = 4096,
) -> ConversationRuntime:
    class _Thinking:
        mode = "disabled"
        budget_tokens = 0

    class _ModelRouting:
        sub_agent = ""
        compaction = ""
        fallback = fallback_model

    class _Config:
        max_turn_iterations = 5
        temperature = 0.7
        native_tools = True
        compact_after_tokens = 80000
        hida = None
        thinking = _Thinking()
        model_routing = _ModelRouting()

    cfg = _Config()
    cfg.model = model
    cfg.max_tokens = max_tokens

    class _NoOpHooks:
        async def pre_tool_use(self, name, args):
            return args

        async def post_tool_use(self, name, args, result):
            pass

    return ConversationRuntime(
        provider=provider,
        tool_registry=ToolRegistry(),
        permission_policy=PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT),
        hook_runner=_NoOpHooks(),
        prompt_builder=SystemPromptBuilder(),
        config=cfg,
        session=Session.create(tmp_path),
        context=_make_context(tmp_path),
    )


# ---------------------------------------------------------------------------
# Layer: 529 Overload recovery in OpenAICompatProvider
# ---------------------------------------------------------------------------

class TestOverload529Recovery:
    """HTTP 529 triggers long-backoff retry (30s->60s->120s), max 3 attempts."""

    def _make_provider(self) -> OpenAICompatProvider:
        return OpenAICompatProvider(
            base_url="http://localhost:8000",
            model_name="test-model",
            max_retries=2,
        )

    def _make_529_response(self) -> httpx.Response:
        return httpx.Response(
            status_code=529,
            content=b'{"error": {"message": "Service overloaded"}}',
            headers={"Content-Type": "application/json"},
        )

    def _make_200_response(self) -> httpx.Response:
        body = b'{"choices":[{"message":{"content":"ok","tool_calls":null},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":3}}'
        return httpx.Response(
            status_code=200,
            content=body,
            headers={"Content-Type": "application/json"},
        )

    @pytest.mark.asyncio
    async def test_529_raises_provider_overload_error(self) -> None:
        """A 529 response should raise ProviderOverloadError."""
        provider = self._make_provider()
        resp = self._make_529_response()
        with pytest.raises(ProviderOverloadError):
            provider._raise_for_status(resp)

    @pytest.mark.asyncio
    async def test_529_retried_with_long_backoff_then_succeeds(self) -> None:
        """Two 529s followed by success: retries with backoff, returns response."""
        provider = self._make_provider()
        call_count = 0

        async def fake_post(url, json):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return self._make_529_response()
            return self._make_200_response()

        with patch.object(provider._client, "post", side_effect=fake_post):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                payload = {"model": "test", "messages": [], "stream": False}
                result = await provider._post_with_retry(payload)

        assert result.status_code == 200
        assert call_count == 3
        # Backoff values: 30s on first 529, 60s on second 529
        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_calls == [30, 60]

    @pytest.mark.asyncio
    async def test_529_exhausts_all_3_attempts_then_raises(self) -> None:
        """After 3 overload retries, ProviderOverloadError is raised."""
        provider = self._make_provider()
        call_count = 0

        async def always_529(url, json):
            nonlocal call_count
            call_count += 1
            return self._make_529_response()

        with patch.object(provider._client, "post", side_effect=always_529):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                payload = {"model": "test", "messages": [], "stream": False}
                with pytest.raises(ProviderOverloadError):
                    await provider._post_with_retry(payload)

        # 1 initial + 3 overload retries = 4 total calls
        assert call_count == 4
        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_calls == [30, 60, 120]

    @pytest.mark.asyncio
    async def test_529_overload_retries_independent_of_normal_retries(self) -> None:
        """Overload retries do not consume normal retry budget."""
        provider = self._make_provider()
        call_count = 0

        async def fake_post(url, json):
            nonlocal call_count
            call_count += 1
            # 529 twice, then connection error twice, then success
            if call_count <= 2:
                return self._make_529_response()
            if call_count <= 4:
                return httpx.Response(status_code=503, content=b"error")
            return self._make_200_response()

        with patch.object(provider._client, "post", side_effect=fake_post):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                payload = {"model": "test", "messages": [], "stream": False}
                result = await provider._post_with_retry(payload)

        assert result.status_code == 200


# ---------------------------------------------------------------------------
# Layer: Token limit auto-upgrade in ConversationRuntime
# ---------------------------------------------------------------------------

class TestTokenLimitAutoUpgrade:
    """After max_tokens stop, doubles limit and retries; caps at 65536."""

    @pytest.mark.asyncio
    async def test_doubles_max_tokens_on_max_tokens_stop(self, tmp_path: Path) -> None:
        """First stream stops with max_tokens; second call uses doubled max_tokens."""
        recorded_max_tokens = []

        class _TrackingProvider:
            def supports_native_tools(self): return True
            def supports_images(self): return False
            _call = 0

            async def stream_message(self, request: MessageRequest):
                self._call += 1
                recorded_max_tokens.append(request.max_tokens)
                if self._call == 1:
                    return _make_text_stream(stop_reason="max_tokens")
                return _make_text_stream(stop_reason="end_turn")

        provider = _TrackingProvider()
        runtime = _make_runtime(tmp_path, provider, max_tokens=4096)

        events = []
        async for event in runtime.run_turn("write a long essay"):
            events.append(event)

        assert len(recorded_max_tokens) == 2
        assert recorded_max_tokens[0] == 4096
        assert recorded_max_tokens[1] == 8192

    @pytest.mark.asyncio
    async def test_doubles_on_finish_reason_length(self, tmp_path: Path) -> None:
        """finish_reason='length' also triggers token upgrade."""
        recorded_max_tokens = []

        class _TrackingProvider:
            def supports_native_tools(self): return True
            def supports_images(self): return False
            _call = 0

            async def stream_message(self, request: MessageRequest):
                self._call += 1
                recorded_max_tokens.append(request.max_tokens)
                if self._call == 1:
                    return _make_text_stream(stop_reason="length")
                return _make_text_stream(stop_reason="end_turn")

        provider = _TrackingProvider()
        runtime = _make_runtime(tmp_path, provider, max_tokens=4096)

        async for _ in runtime.run_turn("write code"):
            pass

        assert recorded_max_tokens[1] == 8192

    @pytest.mark.asyncio
    async def test_caps_token_upgrade_at_65536(self, tmp_path: Path) -> None:
        """Token upgrade is capped at 65536 and stops retrying when at cap."""
        recorded_max_tokens = []

        class _TrackingProvider:
            def supports_native_tools(self): return True
            def supports_images(self): return False
            _call = 0

            async def stream_message(self, request: MessageRequest):
                self._call += 1
                recorded_max_tokens.append(request.max_tokens)
                # Always return max_tokens stop — should stop upgrading at 65536
                if request.max_tokens < 65536:
                    return _make_text_stream(stop_reason="max_tokens")
                return _make_text_stream(stop_reason="end_turn")

        provider = _TrackingProvider()
        runtime = _make_runtime(tmp_path, provider, max_tokens=32768)

        async for _ in runtime.run_turn("write code"):
            pass

        # 32768 -> 65536 -> capped
        assert 65536 in recorded_max_tokens
        assert max(recorded_max_tokens) == 65536

    @pytest.mark.asyncio
    async def test_no_upgrade_on_normal_stop(self, tmp_path: Path) -> None:
        """Normal end_turn stop should not trigger token upgrade."""
        call_count = 0

        class _CountingProvider:
            def supports_native_tools(self): return True
            def supports_images(self): return False

            async def stream_message(self, request: MessageRequest):
                nonlocal call_count
                call_count += 1
                return _make_text_stream(stop_reason="end_turn")

        provider = _CountingProvider()
        runtime = _make_runtime(tmp_path, provider, max_tokens=4096)

        async for _ in runtime.run_turn("hello"):
            pass

        assert call_count == 1


# ---------------------------------------------------------------------------
# Layer: Model fallback after 3 consecutive provider errors
# ---------------------------------------------------------------------------

class TestModelFallback:
    """After 3 consecutive provider errors, switch to fallback model."""

    @pytest.mark.asyncio
    async def test_switches_to_fallback_after_3_failures(self, tmp_path: Path) -> None:
        """3 consecutive provider errors triggers switch to fallback model."""
        recorded_models = []

        class _FailingProvider:
            def supports_native_tools(self): return True
            def supports_images(self): return False
            _call = 0

            async def stream_message(self, request: MessageRequest):
                self._call += 1
                recorded_models.append(request.model)
                if self._call <= 3:
                    raise ProviderConnectionError("server error")
                return _make_text_stream(stop_reason="end_turn")

        provider = _FailingProvider()
        runtime = _make_runtime(
            tmp_path, provider,
            model="primary-model",
            fallback_model="fallback-model",
        )

        async for _ in runtime.run_turn("hello"):
            pass

        # First 3 calls on primary, then fallback
        primary_calls = [m for m in recorded_models if m == "primary-model"]
        fallback_calls = [m for m in recorded_models if m == "fallback-model"]
        assert len(primary_calls) == 3
        assert len(fallback_calls) >= 1

    @pytest.mark.asyncio
    async def test_active_model_switches_to_fallback(self, tmp_path: Path) -> None:
        """_active_model attribute is updated to fallback after switch."""
        class _FailingProvider:
            def supports_native_tools(self): return True
            def supports_images(self): return False
            _call = 0

            async def stream_message(self, request: MessageRequest):
                self._call += 1
                if self._call <= 3:
                    raise ProviderConnectionError("server error")
                return _make_text_stream(stop_reason="end_turn")

        provider = _FailingProvider()
        runtime = _make_runtime(
            tmp_path, provider,
            model="primary-model",
            fallback_model="fallback-model",
        )

        async for _ in runtime.run_turn("hello"):
            pass

        assert runtime._active_model == "fallback-model"

    @pytest.mark.asyncio
    async def test_resets_failure_counter_on_success(self, tmp_path: Path) -> None:
        """Successful stream resets _consecutive_failures to 0."""
        class _SuccessProvider:
            def supports_native_tools(self): return True
            def supports_images(self): return False

            async def stream_message(self, request: MessageRequest):
                return _make_text_stream(stop_reason="end_turn")

        provider = _SuccessProvider()
        runtime = _make_runtime(tmp_path, provider)
        runtime._consecutive_failures = 5  # pre-seed failures

        async for _ in runtime.run_turn("hello"):
            pass

        assert runtime._consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_no_fallback_configured_raises(self, tmp_path: Path) -> None:
        """Without fallback model configured, provider error propagates."""
        class _AlwaysFailProvider:
            def supports_native_tools(self): return True
            def supports_images(self): return False

            async def stream_message(self, request: MessageRequest):
                raise ProviderConnectionError("server error")

        provider = _AlwaysFailProvider()
        runtime = _make_runtime(
            tmp_path, provider,
            model="primary-model",
            fallback_model="",  # no fallback
        )

        with pytest.raises(ProviderConnectionError):
            async for _ in runtime.run_turn("hello"):
                pass

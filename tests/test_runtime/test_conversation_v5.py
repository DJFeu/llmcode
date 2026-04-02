"""Tests for Task 5: Reactive Compact (413 / prompt too long error retry)."""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest

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
# Helpers
# ---------------------------------------------------------------------------

def _make_context(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        cwd=tmp_path,
        is_git_repo=False,
        git_status="",
        instructions="",
    )


async def _text_stream(text: str = "OK") -> AsyncIterator[StreamEvent]:
    yield StreamTextDelta(text=text)
    yield StreamMessageStop(usage=TokenUsage(5, 3), stop_reason="end_turn")


class _Config:
    max_turn_iterations = 5
    max_tokens = 4096
    temperature = 0.7
    native_tools = True
    compact_after_tokens = 80000


class _NoOpHooks:
    async def pre_tool_use(self, tool_name: str, args: dict) -> dict:
        return args

    async def post_tool_use(self, tool_name: str, args: dict, result) -> None:
        pass


def _make_runtime(tmp_path: Path, provider) -> ConversationRuntime:
    return ConversationRuntime(
        provider=provider,
        tool_registry=ToolRegistry(),
        permission_policy=PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT),
        hook_runner=_NoOpHooks(),
        prompt_builder=SystemPromptBuilder(),
        config=_Config(),
        session=Session.create(tmp_path),
        context=_make_context(tmp_path),
    )


# ---------------------------------------------------------------------------
# Test: reactive compact on 413-like provider error
# ---------------------------------------------------------------------------

class TestReactiveCompact:
    @pytest.mark.asyncio
    async def test_retries_after_413_error(self, tmp_path: Path) -> None:
        """Provider raises a 413 error on first call → runtime retries after compressing."""
        call_count = 0

        async def _failing_then_ok_stream() -> AsyncIterator[StreamEvent]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Request failed with status 413: payload too large")
            # Second call succeeds
            yield StreamTextDelta(text="Recovered")
            yield StreamMessageStop(usage=TokenUsage(5, 3), stop_reason="end_turn")

        class _FailOnceThenOkProvider:
            def __init__(self) -> None:
                self.call_count = 0

            async def stream_message(self, request: MessageRequest):
                self.call_count += 1
                if self.call_count == 1:
                    raise RuntimeError("Request failed with status 413: payload too large")
                return _text_stream("Recovered")

            def supports_native_tools(self) -> bool:
                return True

            def supports_images(self) -> bool:
                return False

        provider = _FailOnceThenOkProvider()
        runtime = _make_runtime(tmp_path, provider)

        events = []
        async for event in runtime.run_turn("hello"):
            events.append(event)

        # Provider was called twice (once failing, once succeeding)
        assert provider.call_count == 2
        text_events = [e for e in events if isinstance(e, StreamTextDelta)]
        assert any("Recovered" in e.text for e in text_events)

    @pytest.mark.asyncio
    async def test_retries_after_prompt_too_long_error(self, tmp_path: Path) -> None:
        """Provider raises 'prompt too long' error → same retry path."""

        class _PromptTooLongProvider:
            def __init__(self) -> None:
                self.call_count = 0

            async def stream_message(self, request: MessageRequest):
                self.call_count += 1
                if self.call_count == 1:
                    raise ValueError("prompt too long for this model")
                return _text_stream("OK after compact")

            def supports_native_tools(self) -> bool:
                return True

            def supports_images(self) -> bool:
                return False

        provider = _PromptTooLongProvider()
        runtime = _make_runtime(tmp_path, provider)

        events = []
        async for event in runtime.run_turn("hello"):
            events.append(event)

        assert provider.call_count == 2
        text_events = [e for e in events if isinstance(e, StreamTextDelta)]
        assert any("OK after compact" in e.text for e in text_events)

    @pytest.mark.asyncio
    async def test_no_retry_loop_on_persistent_413(self, tmp_path: Path) -> None:
        """If 413 persists after retry, the error is re-raised (no infinite loop)."""

        class _AlwaysFailProvider:
            def __init__(self) -> None:
                self.call_count = 0

            async def stream_message(self, request: MessageRequest):
                self.call_count += 1
                raise RuntimeError("413 always fails")

            def supports_native_tools(self) -> bool:
                return True

            def supports_images(self) -> bool:
                return False

        provider = _AlwaysFailProvider()
        runtime = _make_runtime(tmp_path, provider)

        with pytest.raises(RuntimeError, match="413"):
            async for _ in runtime.run_turn("hello"):
                pass

        # Should have been called at most twice (first fail + one retry)
        assert provider.call_count <= 2

    @pytest.mark.asyncio
    async def test_non_413_error_propagates_immediately(self, tmp_path: Path) -> None:
        """Non-size errors are not caught and propagate immediately without retry."""

        class _NetworkErrorProvider:
            def __init__(self) -> None:
                self.call_count = 0

            async def stream_message(self, request: MessageRequest):
                self.call_count += 1
                raise ConnectionError("network down")

            def supports_native_tools(self) -> bool:
                return True

            def supports_images(self) -> bool:
                return False

        provider = _NetworkErrorProvider()
        runtime = _make_runtime(tmp_path, provider)

        with pytest.raises(ConnectionError, match="network down"):
            async for _ in runtime.run_turn("hello"):
                pass

        # No retry — called exactly once
        assert provider.call_count == 1

    @pytest.mark.asyncio
    async def test_session_compressed_before_retry(self, tmp_path: Path) -> None:
        """After 413 error, the session is compressed before the retry."""
        from llm_code.api.types import Message, ToolResultBlock

        class _FailOnceSaveRequestProvider:
            def __init__(self) -> None:
                self.call_count = 0
                self.requests: list[MessageRequest] = []

            async def stream_message(self, request: MessageRequest):
                self.call_count += 1
                self.requests.append(request)
                if self.call_count == 1:
                    raise RuntimeError("413 request too large")
                return _text_stream("OK")

            def supports_native_tools(self) -> bool:
                return True

            def supports_images(self) -> bool:
                return False

        provider = _FailOnceSaveRequestProvider()
        runtime = _make_runtime(tmp_path, provider)

        # Pre-load session with a large tool result to ensure compression changes something
        big_content = "x" * 10000
        tool_result_msg = Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t1", content=big_content),),
        )
        runtime.session = runtime.session.add_message(tool_result_msg)
        original_tokens = runtime.session.estimated_tokens()

        async for _ in runtime.run_turn("hello"):
            pass

        # Session should have been compressed (fewer tokens than before)
        assert runtime.session.estimated_tokens() < original_tokens + 50  # rough check
        # Both requests were made
        assert provider.call_count == 2

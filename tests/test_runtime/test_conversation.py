"""Tests for ConversationRuntime agentic loop."""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_code.api.types import (
    Message,
    MessageRequest,
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    ToolUseBlock,
)
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.conversation import ConversationRuntime, TurnSummary
from llm_code.runtime.permissions import PermissionMode, PermissionOutcome, PermissionPolicy
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.session import Session
from llm_code.tools.base import PermissionLevel, ToolResult
from llm_code.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_context(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        cwd=tmp_path,
        is_git_repo=False,
        git_status="",
        instructions="",
    )


async def _simple_text_stream() -> AsyncIterator[StreamEvent]:
    yield StreamTextDelta(text="Hello")
    yield StreamTextDelta(text=" world")
    yield StreamMessageStop(usage=TokenUsage(10, 5), stop_reason="end_turn")


async def _tool_stream() -> AsyncIterator[StreamEvent]:
    """Stream that requests a read_file tool call via native tool events."""
    yield StreamToolUseStart(id="call1", name="read_file")
    yield StreamToolUseInputDelta(id="call1", partial_json='{"path":"/tmp/f"}')
    yield StreamMessageStop(usage=TokenUsage(20, 10), stop_reason="tool_use")


async def _final_text_stream() -> AsyncIterator[StreamEvent]:
    yield StreamTextDelta(text="Done")
    yield StreamMessageStop(usage=TokenUsage(5, 3), stop_reason="end_turn")


class MockProvider:
    """Mock LLM provider with configurable responses per call."""

    def __init__(self, response_streams: list) -> None:
        self._streams = iter(response_streams)
        self._call_count = 0

    async def stream_message(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        self._call_count += 1
        return next(self._streams)

    def supports_native_tools(self) -> bool:
        return True

    def supports_images(self) -> bool:
        return False


def _make_runtime(
    tmp_path: Path,
    provider: MockProvider,
    registry: ToolRegistry | None = None,
    permission_policy: PermissionPolicy | None = None,
) -> ConversationRuntime:
    if registry is None:
        registry = ToolRegistry()
    if permission_policy is None:
        permission_policy = PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT)

    # Minimal hook runner stub
    class _NoOpHooks:
        async def pre_tool_use(self, tool_name: str, args: dict) -> dict:
            return args

        async def post_tool_use(self, tool_name: str, result) -> None:
            pass

    # Minimal config stub
    class _Config:
        max_turn_iterations = 5
        max_tokens = 4096
        temperature = 0.7
        native_tools = True

    session = Session.create(tmp_path)
    context = _make_context(tmp_path)

    return ConversationRuntime(
        provider=provider,
        tool_registry=registry,
        permission_policy=permission_policy,
        hook_runner=_NoOpHooks(),
        prompt_builder=SystemPromptBuilder(),
        config=_Config(),
        session=session,
        context=context,
    )


# ---------------------------------------------------------------------------
# Test: simple text response (no tool calls)
# ---------------------------------------------------------------------------

class TestSimpleTextResponse:
    @pytest.mark.asyncio
    async def test_yields_text_delta_events(self, tmp_path: Path) -> None:
        provider = MockProvider([_simple_text_stream()])
        runtime = _make_runtime(tmp_path, provider)

        events = []
        async for event in runtime.run_turn("Say hello"):
            events.append(event)

        text_events = [e for e in events if isinstance(e, StreamTextDelta)]
        assert len(text_events) == 2
        assert text_events[0].text == "Hello"
        assert text_events[1].text == " world"

    @pytest.mark.asyncio
    async def test_provider_called_once(self, tmp_path: Path) -> None:
        provider = MockProvider([_simple_text_stream()])
        runtime = _make_runtime(tmp_path, provider)

        async for _ in runtime.run_turn("hello"):
            pass

        assert provider._call_count == 1

    @pytest.mark.asyncio
    async def test_session_updated_with_messages(self, tmp_path: Path) -> None:
        provider = MockProvider([_simple_text_stream()])
        runtime = _make_runtime(tmp_path, provider)

        async for _ in runtime.run_turn("hi"):
            pass

        # User + assistant messages added
        assert len(runtime.session.messages) == 2
        assert runtime.session.messages[0].role == "user"
        assert runtime.session.messages[1].role == "assistant"

    @pytest.mark.asyncio
    async def test_stop_event_yielded(self, tmp_path: Path) -> None:
        provider = MockProvider([_simple_text_stream()])
        runtime = _make_runtime(tmp_path, provider)

        events = []
        async for event in runtime.run_turn("hi"):
            events.append(event)

        stop_events = [e for e in events if isinstance(e, StreamMessageStop)]
        assert len(stop_events) == 1


# ---------------------------------------------------------------------------
# Test: tool use then final response
# ---------------------------------------------------------------------------

class TestToolUseThenResponse:
    def _make_tool_registry(self) -> ToolRegistry:
        from llm_code.tools.base import Tool

        class FakeReadTool(Tool):
            @property
            def name(self) -> str:
                return "read_file"

            @property
            def description(self) -> str:
                return "Read a file"

            @property
            def input_schema(self) -> dict:
                return {"type": "object", "properties": {"path": {"type": "string"}}}

            @property
            def required_permission(self) -> PermissionLevel:
                return PermissionLevel.READ_ONLY

            def execute(self, args: dict) -> ToolResult:
                return ToolResult(output="file contents here")

        registry = ToolRegistry()
        registry.register(FakeReadTool())
        return registry

    @pytest.mark.asyncio
    async def test_two_provider_calls(self, tmp_path: Path) -> None:
        registry = self._make_tool_registry()
        provider = MockProvider([_tool_stream(), _final_text_stream()])
        runtime = _make_runtime(tmp_path, provider, registry=registry)

        async for _ in runtime.run_turn("read the file"):
            pass

        assert provider._call_count == 2

    @pytest.mark.asyncio
    async def test_final_text_yielded(self, tmp_path: Path) -> None:
        registry = self._make_tool_registry()
        provider = MockProvider([_tool_stream(), _final_text_stream()])
        runtime = _make_runtime(tmp_path, provider, registry=registry)

        events = []
        async for event in runtime.run_turn("read the file"):
            events.append(event)

        text_events = [e for e in events if isinstance(e, StreamTextDelta)]
        assert any(e.text == "Done" for e in text_events)

    @pytest.mark.asyncio
    async def test_session_has_tool_result(self, tmp_path: Path) -> None:
        registry = self._make_tool_registry()
        provider = MockProvider([_tool_stream(), _final_text_stream()])
        runtime = _make_runtime(tmp_path, provider, registry=registry)

        async for _ in runtime.run_turn("read the file"):
            pass

        # Messages: user, assistant (tool_use), user (tool_result), assistant (text)
        all_blocks = [b for m in runtime.session.messages for b in m.content]
        tool_result_blocks = [b for b in all_blocks if isinstance(b, ToolResultBlock)]
        assert len(tool_result_blocks) >= 1
        assert "file contents here" in tool_result_blocks[0].content

    @pytest.mark.asyncio
    async def test_denied_tool_yields_error_result(self, tmp_path: Path) -> None:
        registry = self._make_tool_registry()
        deny_policy = PermissionPolicy(
            mode=PermissionMode.PROMPT,
            deny_tools=frozenset({"read_file"}),
        )
        provider = MockProvider([_tool_stream(), _final_text_stream()])
        runtime = _make_runtime(tmp_path, provider, registry=registry, permission_policy=deny_policy)

        async for _ in runtime.run_turn("read the file"):
            pass

        all_blocks = [b for m in runtime.session.messages for b in m.content]
        tool_result_blocks = [b for b in all_blocks if isinstance(b, ToolResultBlock)]
        assert len(tool_result_blocks) >= 1
        assert tool_result_blocks[0].is_error is True

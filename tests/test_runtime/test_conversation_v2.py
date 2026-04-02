"""Tests for ConversationRuntime v2: validate→safety→permission→progress pipeline."""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest

from llm_code.api.types import (
    MessageRequest,
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    StreamToolProgress,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    TokenUsage,
    ToolResultBlock,
)
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.session import Session
from llm_code.tools.base import PermissionLevel, Tool, ToolProgress, ToolResult
from llm_code.tools.registry import ToolRegistry

from pydantic import BaseModel


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

    class _NoOpHooks:
        async def pre_tool_use(self, tool_name: str, args: dict) -> dict:
            return args

        async def post_tool_use(self, tool_name: str, args: dict, result) -> None:
            pass

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
# Tool fixtures
# ---------------------------------------------------------------------------

class BashInputModel(BaseModel):
    command: str
    timeout: int = 30


class FakeBashTool(Tool):
    """Fake bash tool with is_read_only for ls, Pydantic input validation."""

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return "Execute bash commands"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "default": 30},
            },
            "required": ["command"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.FULL_ACCESS

    @property
    def input_model(self) -> type[BashInputModel]:
        return BashInputModel

    def is_read_only(self, args: dict) -> bool:
        return args.get("command", "").startswith("ls") or args.get("command", "").startswith("echo")

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(output=f"output: {args['command']}")

    def execute_with_progress(self, args: dict, on_progress) -> ToolResult:
        on_progress(ToolProgress(tool_name=self.name, message="running", percent=50.0))
        return ToolResult(output=f"output: {args['command']}")


def _bash_stream(command: str) -> AsyncIterator[StreamEvent]:
    """Build a stream that calls bash with the given command."""
    import json

    async def _stream():
        yield StreamToolUseStart(id="call1", name="bash")
        yield StreamToolUseInputDelta(id="call1", partial_json=json.dumps({"command": command}))
        yield StreamMessageStop(usage=TokenUsage(20, 10), stop_reason="tool_use")

    return _stream()


def _missing_command_stream() -> AsyncIterator[StreamEvent]:
    """Build a stream for a bash call with no 'command' field (validation fail)."""
    import json

    async def _stream():
        yield StreamToolUseStart(id="call1", name="bash")
        yield StreamToolUseInputDelta(id="call1", partial_json=json.dumps({}))
        yield StreamMessageStop(usage=TokenUsage(20, 10), stop_reason="tool_use")

    return _stream()


# ---------------------------------------------------------------------------
# Test: validation failure
# ---------------------------------------------------------------------------

class TestValidationFailure:
    @pytest.mark.asyncio
    async def test_validation_failure_returns_error(self, tmp_path: Path) -> None:
        """bash with missing required 'command' → ValidationError → error ToolResultBlock."""
        registry = ToolRegistry()
        registry.register(FakeBashTool())
        provider = MockProvider([_missing_command_stream(), _final_text_stream()])
        runtime = _make_runtime(tmp_path, provider, registry=registry)

        async for _ in runtime.run_turn("run bad bash"):
            pass

        all_blocks = [b for m in runtime.session.messages for b in m.content]
        tool_result_blocks = [b for b in all_blocks if isinstance(b, ToolResultBlock)]
        assert len(tool_result_blocks) >= 1
        assert tool_result_blocks[0].is_error is True
        # Should mention validation or the field name
        content = tool_result_blocks[0].content.lower()
        assert "command" in content or "validation" in content or "invalid" in content


# ---------------------------------------------------------------------------
# Test: read-only safety → auto-approved in PROMPT mode
# ---------------------------------------------------------------------------

class TestReadOnlySafetyBypass:
    @pytest.mark.asyncio
    async def test_read_only_bash_auto_approved_in_prompt_mode(self, tmp_path: Path) -> None:
        """bash 'ls' in PROMPT mode: is_read_only → effective=READ_ONLY → ALLOW (not NEED_PROMPT)."""
        registry = ToolRegistry()
        registry.register(FakeBashTool())
        policy = PermissionPolicy(mode=PermissionMode.PROMPT)
        provider = MockProvider([_bash_stream("ls -la"), _final_text_stream()])
        runtime = _make_runtime(tmp_path, provider, registry=registry, permission_policy=policy)

        async for _ in runtime.run_turn("list files"):
            pass

        all_blocks = [b for m in runtime.session.messages for b in m.content]
        tool_result_blocks = [b for b in all_blocks if isinstance(b, ToolResultBlock)]
        assert len(tool_result_blocks) >= 1
        # Should NOT be a permission denied error
        assert tool_result_blocks[0].is_error is False


# ---------------------------------------------------------------------------
# Test: progress events yielded
# ---------------------------------------------------------------------------

class TestProgressEvents:
    @pytest.mark.asyncio
    async def test_progress_events_yielded(self, tmp_path: Path) -> None:
        """bash 'echo hi' → pipeline runs; StreamToolProgress events are yielded."""
        registry = ToolRegistry()
        registry.register(FakeBashTool())
        provider = MockProvider([_bash_stream("echo hi"), _final_text_stream()])
        runtime = _make_runtime(tmp_path, provider, registry=registry)

        events = []
        async for event in runtime.run_turn("say hi"):
            events.append(event)

        progress_events = [e for e in events if isinstance(e, StreamToolProgress)]
        assert len(progress_events) >= 1
        assert progress_events[0].tool_name == "bash"
        assert progress_events[0].message == "running"

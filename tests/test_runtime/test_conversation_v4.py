"""Tests for v4 ConversationRuntime: checkpoint integration + parallel agent execution."""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest

from llm_code.api.types import (
    MessageRequest,
    StreamEvent,
    StreamMessageStop,
    StreamTextDelta,
    StreamToolUseInputDelta,
    StreamToolUseStart,
    TokenUsage,
)
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.session import Session
from llm_code.tools.base import PermissionLevel, ToolResult
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
    compact_after_tokens = 80000


class MockProvider:
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

    def supports_reasoning(self) -> bool:
        return False


def _make_runtime(
    tmp_path: Path,
    provider: MockProvider,
    registry: ToolRegistry | None = None,
    permission_policy: PermissionPolicy | None = None,
    checkpoint_manager=None,
) -> ConversationRuntime:
    if registry is None:
        registry = ToolRegistry()
    if permission_policy is None:
        permission_policy = PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT)

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
        checkpoint_manager=checkpoint_manager,
    )


# ---------------------------------------------------------------------------
# Minimal mock write tool
# ---------------------------------------------------------------------------

class WriteTool:
    """A non-read-only tool so checkpoint should fire."""

    @property
    def name(self) -> str:
        return "write_something"

    @property
    def description(self) -> str:
        return "Writes something"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"data": {"type": "string"}},
            "required": ["data"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.WORKSPACE_WRITE

    def is_read_only(self, args: dict) -> bool:
        return False

    def is_destructive(self, args: dict) -> bool:
        return False

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def validate_input(self, args: dict) -> dict:
        return args

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(output="written")

    def execute_with_progress(self, args, on_progress) -> ToolResult:
        return self.execute(args)

    def to_definition(self):
        from llm_code.api.types import ToolDefinition
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )


class ReadTool:
    """A read-only tool — checkpoint should NOT fire."""

    @property
    def name(self) -> str:
        return "read_something"

    @property
    def description(self) -> str:
        return "Reads something"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def is_read_only(self, args: dict) -> bool:
        return True

    def is_destructive(self, args: dict) -> bool:
        return False

    def is_concurrency_safe(self, args: dict) -> bool:
        return True

    def validate_input(self, args: dict) -> dict:
        return args

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(output="contents")

    def execute_with_progress(self, args, on_progress) -> ToolResult:
        return self.execute(args)

    def to_definition(self):
        from llm_code.api.types import ToolDefinition
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )


# ---------------------------------------------------------------------------
# Stream helpers
# ---------------------------------------------------------------------------

async def _write_tool_stream() -> AsyncIterator[StreamEvent]:
    yield StreamToolUseStart(id="call1", name="write_something")
    yield StreamToolUseInputDelta(id="call1", partial_json='{"data":"hello"}')
    yield StreamMessageStop(usage=TokenUsage(20, 10), stop_reason="tool_use")


async def _read_tool_stream() -> AsyncIterator[StreamEvent]:
    yield StreamToolUseStart(id="call1", name="read_something")
    yield StreamToolUseInputDelta(id="call1", partial_json='{"path":"/tmp/x"}')
    yield StreamMessageStop(usage=TokenUsage(20, 10), stop_reason="tool_use")


async def _final_text_stream() -> AsyncIterator[StreamEvent]:
    yield StreamTextDelta(text="Done")
    yield StreamMessageStop(usage=TokenUsage(5, 3), stop_reason="end_turn")


async def _two_agent_calls_stream() -> AsyncIterator[StreamEvent]:
    """Simulates LLM requesting two agent tool calls."""
    yield StreamToolUseStart(id="a1", name="agent")
    yield StreamToolUseInputDelta(id="a1", partial_json='{"task":"task one"}')
    yield StreamToolUseStart(id="a2", name="agent")
    yield StreamToolUseInputDelta(id="a2", partial_json='{"task":"task two"}')
    yield StreamMessageStop(usage=TokenUsage(30, 20), stop_reason="tool_use")


async def _one_agent_call_stream() -> AsyncIterator[StreamEvent]:
    yield StreamToolUseStart(id="a1", name="agent")
    yield StreamToolUseInputDelta(id="a1", partial_json='{"task":"single task"}')
    yield StreamMessageStop(usage=TokenUsage(20, 10), stop_reason="tool_use")


# ---------------------------------------------------------------------------
# Task 5: Checkpoint integration
# ---------------------------------------------------------------------------

class MockCheckpointManager:
    """Records create() calls for assertion."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.fail_on_create = False

    def create(self, tool_name: str, tool_args: dict):
        if self.fail_on_create:
            raise RuntimeError("checkpoint failure")
        self.calls.append((tool_name, tool_args))


class TestCheckpointIntegration:
    @pytest.mark.asyncio
    async def test_checkpoint_created_before_write_tool(self, tmp_path: Path) -> None:
        """Checkpoint.create() called once for a write-operation tool."""
        checkpoint_mgr = MockCheckpointManager()
        registry = ToolRegistry()
        registry.register(WriteTool())

        provider = MockProvider([_write_tool_stream(), _final_text_stream()])
        runtime = _make_runtime(
            tmp_path, provider, registry=registry, checkpoint_manager=checkpoint_mgr
        )

        async for _ in runtime.run_turn("write something"):
            pass

        assert len(checkpoint_mgr.calls) == 1
        assert checkpoint_mgr.calls[0][0] == "write_something"

    @pytest.mark.asyncio
    async def test_no_checkpoint_for_read_only_tool(self, tmp_path: Path) -> None:
        """Checkpoint.create() NOT called for read-only tools."""
        checkpoint_mgr = MockCheckpointManager()
        registry = ToolRegistry()
        registry.register(ReadTool())

        provider = MockProvider([_read_tool_stream(), _final_text_stream()])
        runtime = _make_runtime(
            tmp_path, provider, registry=registry, checkpoint_manager=checkpoint_mgr
        )

        async for _ in runtime.run_turn("read something"):
            pass

        assert len(checkpoint_mgr.calls) == 0

    @pytest.mark.asyncio
    async def test_no_checkpoint_manager_does_not_break(self, tmp_path: Path) -> None:
        """When checkpoint_manager is None, execution proceeds normally."""
        registry = ToolRegistry()
        registry.register(WriteTool())

        provider = MockProvider([_write_tool_stream(), _final_text_stream()])
        runtime = _make_runtime(tmp_path, provider, registry=registry, checkpoint_manager=None)

        events = []
        async for event in runtime.run_turn("write something"):
            events.append(event)

        # Should complete without error
        assert any(isinstance(e, StreamTextDelta) for e in events)

    @pytest.mark.asyncio
    async def test_checkpoint_failure_does_not_block_execution(self, tmp_path: Path) -> None:
        """If checkpoint.create() raises, tool execution still proceeds."""
        checkpoint_mgr = MockCheckpointManager()
        checkpoint_mgr.fail_on_create = True

        registry = ToolRegistry()
        registry.register(WriteTool())

        provider = MockProvider([_write_tool_stream(), _final_text_stream()])
        runtime = _make_runtime(
            tmp_path, provider, registry=registry, checkpoint_manager=checkpoint_mgr
        )

        events = []
        async for event in runtime.run_turn("write something"):
            events.append(event)

        # Execution completed despite checkpoint failure
        assert any(isinstance(e, StreamTextDelta) for e in events)


# ---------------------------------------------------------------------------
# Task 6: Parallel agent execution
# ---------------------------------------------------------------------------

class _AgentSubRuntime:
    """Sub-runtime that records which task it was given and returns a result."""

    def __init__(self, task_log: list, result_text: str = "result"):
        self._task_log = task_log
        self._result_text = result_text

    async def run_turn(self, user_input: str):
        self._task_log.append(user_input)
        yield StreamTextDelta(text=self._result_text)
        yield StreamMessageStop(
            usage=TokenUsage(1, 1),
            stop_reason="end_turn",
        )


class TestParallelAgentExecution:
    @pytest.mark.asyncio
    async def test_two_agent_calls_both_execute(self, tmp_path: Path) -> None:
        """Both agent sub-tasks execute when two agent calls are requested."""
        from llm_code.tools.agent import AgentTool

        task_log: list[str] = []

        def factory(model):
            return _AgentSubRuntime(task_log, result_text="done")

        agent_tool = AgentTool(runtime_factory=factory)

        registry = ToolRegistry()
        registry.register(agent_tool)

        provider = MockProvider([_two_agent_calls_stream(), _final_text_stream()])
        runtime = _make_runtime(tmp_path, provider, registry=registry)

        events = []
        async for event in runtime.run_turn("do two tasks in parallel"):
            events.append(event)

        # Both agent sub-tasks should have been executed
        assert "task one" in task_log
        assert "task two" in task_log

    @pytest.mark.asyncio
    async def test_single_agent_call_executes(self, tmp_path: Path) -> None:
        """Single agent call still executes correctly."""
        from llm_code.tools.agent import AgentTool

        task_log: list[str] = []

        def factory(model):
            return _AgentSubRuntime(task_log, result_text="single done")

        agent_tool = AgentTool(runtime_factory=factory)

        registry = ToolRegistry()
        registry.register(agent_tool)

        provider = MockProvider([_one_agent_call_stream(), _final_text_stream()])
        runtime = _make_runtime(tmp_path, provider, registry=registry)

        events = []
        async for event in runtime.run_turn("do one task"):
            events.append(event)

        assert "single task" in task_log

    @pytest.mark.asyncio
    async def test_parallel_results_included_in_tool_results(self, tmp_path: Path) -> None:
        """ToolResultBlocks from both parallel agent calls are added to session."""
        from llm_code.tools.agent import AgentTool

        task_log: list[str] = []

        def factory(model):
            return _AgentSubRuntime(task_log, result_text="parallel-result")

        agent_tool = AgentTool(runtime_factory=factory)

        registry = ToolRegistry()
        registry.register(agent_tool)

        provider = MockProvider([_two_agent_calls_stream(), _final_text_stream()])
        runtime = _make_runtime(tmp_path, provider, registry=registry)

        async for _ in runtime.run_turn("parallel tasks"):
            pass

        # Session should have tool result messages — check via message count
        # user msg + assistant (tool calls) + user (tool results) + assistant (final)
        messages = runtime.session.messages
        assert len(messages) >= 3

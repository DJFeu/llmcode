"""Verify ConversationRuntime wires IterationBudget into the turn loop.

Scope:
    * Attribute ``_iteration_budget`` initialised (None at construct,
      an :class:`IterationBudget` once ``run_turn`` begins).
    * Budget ``tick`` fires per iteration.
    * When the budget exhausts naturally (no early break) the runtime
      yields a ``StreamTextDelta`` containing the max-steps reminder,
      so the user can see why the turn ended silently.
"""
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
from llm_code.runtime.auto_compact import IterationBudget
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.conversation import ConversationRuntime
from llm_code.runtime.permissions import PermissionMode, PermissionPolicy
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.session import Session
from llm_code.tools.registry import ToolRegistry


class MockProvider:
    """Streams a predetermined sequence of responses per stream_message call."""

    def __init__(self, response_streams: list) -> None:
        self._streams = iter(response_streams)

    async def stream_message(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        return next(self._streams)

    def supports_native_tools(self) -> bool:
        return True

    def supports_images(self) -> bool:
        return False

    def supports_reasoning(self) -> bool:
        return False


async def _tool_call_stream(tool_name: str = "read_file") -> AsyncIterator[StreamEvent]:
    yield StreamToolUseStart(id=f"call-{tool_name}", name=tool_name)
    yield StreamToolUseInputDelta(
        id=f"call-{tool_name}", partial_json='{"path":"/tmp/ignored"}',
    )
    yield StreamMessageStop(usage=TokenUsage(10, 5), stop_reason="tool_use")


async def _text_only_stream() -> AsyncIterator[StreamEvent]:
    yield StreamTextDelta(text="ok")
    yield StreamMessageStop(usage=TokenUsage(5, 2), stop_reason="end_turn")


def _build_runtime(
    tmp_path: Path, provider: MockProvider, *, max_iterations: int,
) -> ConversationRuntime:
    class _Config:
        max_turn_iterations = max_iterations
        max_tokens = 4096
        temperature = 0.7
        native_tools = True
        compact_after_tokens = 80_000

    class _NoOpHooks:
        async def pre_tool_use(self, tool_name: str, args: dict) -> dict:
            return args

        async def post_tool_use(self, tool_name: str, args: dict, result) -> None:
            pass

    return ConversationRuntime(
        provider=provider,
        tool_registry=ToolRegistry(),
        permission_policy=PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT),
        hook_runner=_NoOpHooks(),
        prompt_builder=SystemPromptBuilder(),
        config=_Config(),
        session=Session.create(tmp_path),
        context=ProjectContext(
            cwd=tmp_path, is_git_repo=False, git_status="", instructions="",
        ),
    )


class TestBudgetAttribute:
    def test_starts_unset_at_construct_time(self, tmp_path: Path) -> None:
        runtime = _build_runtime(
            tmp_path, MockProvider([_text_only_stream()]), max_iterations=3,
        )
        # Attribute exists but is None until run_turn initialises it.
        assert hasattr(runtime, "_iteration_budget")
        assert runtime._iteration_budget is None


class TestBudgetTicksWithinTurn:
    @pytest.mark.asyncio
    async def test_text_only_turn_ticks_once(self, tmp_path: Path) -> None:
        provider = MockProvider([_text_only_stream()])
        runtime = _build_runtime(tmp_path, provider, max_iterations=3)

        async for _ in runtime.run_turn("hi"):
            pass

        assert isinstance(runtime._iteration_budget, IterationBudget)
        assert runtime._iteration_budget.used == 1
        assert runtime._iteration_budget.max_iterations == 3


class TestReminderOnExhaustion:
    @pytest.mark.asyncio
    async def test_max_iterations_exhausted_yields_reminder(
        self, tmp_path: Path,
    ) -> None:
        """Two iterations, each requesting a tool the registry doesn't
        know about — the second tool call exhausts the budget, so the
        runtime should yield a max-steps reminder text block."""
        provider = MockProvider([
            _tool_call_stream(),
            _tool_call_stream(),
        ])
        runtime = _build_runtime(tmp_path, provider, max_iterations=2)

        texts: list[str] = []
        async for event in runtime.run_turn("loop please"):
            if isinstance(event, StreamTextDelta):
                texts.append(event.text)

        blob = "\n".join(texts)
        assert runtime._iteration_budget.exceeded
        assert "MAXIMUM STEPS REACHED" in blob

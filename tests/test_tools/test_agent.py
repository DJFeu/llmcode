"""Tests for AgentTool — TDD (RED first)."""
from __future__ import annotations

import asyncio
import pytest

from llm_code.api.types import StreamMessageStop, StreamTextDelta, TokenUsage
from llm_code.tools.base import PermissionLevel
from llm_code.tools.agent import AgentTool


# ---------------------------------------------------------------------------
# Minimal mock runtime
# ---------------------------------------------------------------------------

class MockRuntime:
    """Yields a single StreamTextDelta then a stop event."""

    async def run_turn(self, user_input: str):
        yield StreamTextDelta(text="Sub-agent result")
        yield StreamMessageStop(
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


class EmptyRuntime:
    """Yields only a stop event (no text)."""

    async def run_turn(self, user_input: str):
        yield StreamMessageStop(
            usage=TokenUsage(input_tokens=0, output_tokens=0),
            stop_reason="end_turn",
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_name():
    tool = AgentTool(runtime_factory=lambda m: None)
    assert tool.name == "agent"


def test_description():
    tool = AgentTool(runtime_factory=lambda m: None)
    assert tool.description  # non-empty


def test_permission():
    tool = AgentTool(runtime_factory=lambda m: None)
    assert tool.required_permission == PermissionLevel.FULL_ACCESS


def test_is_concurrency_safe():
    tool = AgentTool(runtime_factory=lambda m: None)
    assert tool.is_concurrency_safe({}) is True


def test_input_schema_has_task_required():
    tool = AgentTool(runtime_factory=lambda m: None)
    schema = tool.input_schema
    assert "task" in schema["required"]
    assert "task" in schema["properties"]


def test_input_schema_has_optional_model():
    tool = AgentTool(runtime_factory=lambda m: None)
    schema = tool.input_schema
    assert "model" in schema["properties"]
    assert "model" not in schema["required"]


def test_depth_limit():
    tool = AgentTool(runtime_factory=lambda m: None, max_depth=2, current_depth=2)
    result = tool.execute({"task": "test"})
    assert result.is_error is True
    assert "depth" in result.output.lower()


def test_depth_limit_not_reached():
    """Should not error when current_depth < max_depth."""
    tool = AgentTool(runtime_factory=lambda m: MockRuntime(), max_depth=3, current_depth=2)
    result = tool.execute({"task": "do something"})
    assert result.is_error is False


def test_execute_collects_text():
    tool = AgentTool(runtime_factory=lambda m: MockRuntime())
    result = tool.execute({"task": "do something"})
    assert "Sub-agent result" in result.output
    assert result.is_error is False


def test_execute_empty_output_placeholder():
    tool = AgentTool(runtime_factory=lambda m: EmptyRuntime())
    result = tool.execute({"task": "quiet task"})
    assert result.output  # non-empty placeholder
    assert result.is_error is False


def test_model_passed_to_factory():
    """runtime_factory receives the model argument."""
    received_models: list = []

    def factory(model):
        received_models.append(model)
        return MockRuntime()

    tool = AgentTool(runtime_factory=factory)
    tool.execute({"task": "hello", "model": "special-model"})
    assert received_models == ["special-model"]


def test_no_model_passes_none_to_factory():
    received_models: list = []

    def factory(model):
        received_models.append(model)
        return MockRuntime()

    tool = AgentTool(runtime_factory=factory)
    tool.execute({"task": "hello"})
    assert received_models == [None]


def test_default_depth_values():
    tool = AgentTool(runtime_factory=lambda m: None)
    assert tool._max_depth == 3
    assert tool._current_depth == 0

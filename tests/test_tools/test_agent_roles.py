"""Tests for AgentRole definitions and AgentTool role integration — TDD (RED first)."""
from __future__ import annotations

import pytest

from llm_code.api.types import StreamMessageStop, StreamTextDelta, TokenUsage
from llm_code.tools.agent import AgentTool
from llm_code.tools.agent_roles import (
    BUILT_IN_ROLES,
    EXPLORE_ROLE,
    PLAN_ROLE,
    VERIFICATION_ROLE,
    AgentRole,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WRITE_TOOLS = {"write_file", "edit_file"}


class MockRuntime:
    """Yields a single StreamTextDelta then a stop event."""

    def __init__(self, model_override=None, role=None):
        self.model_override = model_override
        self.role = role

    async def run_turn(self, user_input: str):
        yield StreamTextDelta(text="Sub-agent result")
        yield StreamMessageStop(
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


# ---------------------------------------------------------------------------
# Task 1: AgentRole definitions
# ---------------------------------------------------------------------------


class TestAgentRoleDataclass:
    def test_is_frozen(self):
        with pytest.raises((AttributeError, TypeError)):
            EXPLORE_ROLE.name = "mutated"  # type: ignore[misc]

    def test_all_fields_present(self):
        for role in BUILT_IN_ROLES.values():
            assert role.name
            assert role.description
            assert role.system_prompt_prefix
            assert role.allowed_tools
            assert role.model_key

    def test_allowed_tools_is_frozenset(self):
        for role in BUILT_IN_ROLES.values():
            assert isinstance(role.allowed_tools, frozenset)


class TestExploreRole:
    def test_name(self):
        assert EXPLORE_ROLE.name == "explore"

    def test_description_non_empty(self):
        assert len(EXPLORE_ROLE.description) > 0

    def test_system_prompt_non_empty(self):
        assert len(EXPLORE_ROLE.system_prompt_prefix) > 0

    def test_no_write_tools(self):
        assert not (EXPLORE_ROLE.allowed_tools & _WRITE_TOOLS), (
            "EXPLORE_ROLE must not include write tools"
        )

    def test_no_bash(self):
        assert "bash" not in EXPLORE_ROLE.allowed_tools

    def test_has_read_file(self):
        assert "read_file" in EXPLORE_ROLE.allowed_tools

    def test_has_glob_and_grep(self):
        assert "glob_search" in EXPLORE_ROLE.allowed_tools
        assert "grep_search" in EXPLORE_ROLE.allowed_tools


class TestPlanRole:
    def test_name(self):
        assert PLAN_ROLE.name == "plan"

    def test_description_non_empty(self):
        assert len(PLAN_ROLE.description) > 0

    def test_system_prompt_non_empty(self):
        assert len(PLAN_ROLE.system_prompt_prefix) > 0

    def test_no_write_tools(self):
        assert not (PLAN_ROLE.allowed_tools & _WRITE_TOOLS), (
            "PLAN_ROLE must not include write tools"
        )

    def test_no_bash(self):
        assert "bash" not in PLAN_ROLE.allowed_tools

    def test_has_read_file(self):
        assert "read_file" in PLAN_ROLE.allowed_tools


class TestVerificationRole:
    def test_name(self):
        assert VERIFICATION_ROLE.name == "verify"

    def test_description_non_empty(self):
        assert len(VERIFICATION_ROLE.description) > 0

    def test_system_prompt_non_empty(self):
        assert len(VERIFICATION_ROLE.system_prompt_prefix) > 0

    def test_has_bash(self):
        assert "bash" in VERIFICATION_ROLE.allowed_tools

    def test_no_write_file(self):
        assert "write_file" not in VERIFICATION_ROLE.allowed_tools

    def test_no_edit_file(self):
        assert "edit_file" not in VERIFICATION_ROLE.allowed_tools


class TestBuiltInRoles:
    def test_has_all_three(self):
        assert "explore" in BUILT_IN_ROLES
        assert "plan" in BUILT_IN_ROLES
        assert "verify" in BUILT_IN_ROLES

    def test_values_are_agent_roles(self):
        for role in BUILT_IN_ROLES.values():
            assert isinstance(role, AgentRole)

    def test_names_match_keys(self):
        for key, role in BUILT_IN_ROLES.items():
            assert role.name == key


# ---------------------------------------------------------------------------
# Task 2: AgentTool role integration
# ---------------------------------------------------------------------------


class TestAgentToolInputSchema:
    def test_schema_has_role_field(self):
        tool = AgentTool(runtime_factory=lambda m, **kw: None)
        schema = tool.input_schema
        assert "role" in schema["properties"]

    def test_role_enum_values(self):
        tool = AgentTool(runtime_factory=lambda m, **kw: None)
        schema = tool.input_schema
        role_schema = schema["properties"]["role"]
        assert set(role_schema.get("enum", [])) == {"explore", "plan", "verify"}

    def test_role_not_required(self):
        tool = AgentTool(runtime_factory=lambda m, **kw: None)
        schema = tool.input_schema
        assert "role" not in schema.get("required", [])


class TestAgentToolWithRole:
    def _make_tool(self):
        received: list[dict] = []

        def factory(model_override=None, role=None):
            received.append({"model": model_override, "role": role})
            return MockRuntime(model_override=model_override, role=role)

        tool = AgentTool(runtime_factory=factory, max_depth=3, current_depth=0)
        return tool, received

    def test_explore_role_passed_to_factory(self):
        tool, received = self._make_tool()
        result = tool.execute({"task": "explore the code", "role": "explore"})
        assert result.is_error is False
        assert received[0]["role"] is not None
        assert received[0]["role"].name == "explore"

    def test_plan_role_passed_to_factory(self):
        tool, received = self._make_tool()
        result = tool.execute({"task": "plan the changes", "role": "plan"})
        assert result.is_error is False
        assert received[0]["role"].name == "plan"

    def test_verify_role_passed_to_factory(self):
        tool, received = self._make_tool()
        result = tool.execute({"task": "verify the changes", "role": "verify"})
        assert result.is_error is False
        assert received[0]["role"].name == "verify"

    def test_invalid_role_returns_error(self):
        tool, received = self._make_tool()
        result = tool.execute({"task": "do something", "role": "nonexistent"})
        assert result.is_error is True
        assert "role" in result.output.lower() or "unknown" in result.output.lower()

    def test_no_role_passes_none_to_factory(self):
        tool, received = self._make_tool()
        result = tool.execute({"task": "do something"})
        assert result.is_error is False
        assert received[0]["role"] is None

    def test_empty_role_passes_none_to_factory(self):
        tool, received = self._make_tool()
        result = tool.execute({"task": "do something", "role": ""})
        assert result.is_error is False
        assert received[0]["role"] is None

    def test_existing_behavior_unchanged_without_role(self):
        """Without role, factory still gets model arg positionally."""
        received_models: list = []

        def factory(model_override=None, role=None):
            received_models.append(model_override)
            return MockRuntime()

        tool = AgentTool(runtime_factory=factory)
        tool.execute({"task": "hello", "model": "gpt-4"})
        assert received_models == ["gpt-4"]

    def test_depth_limit_still_works_with_role(self):
        tool = AgentTool(
            runtime_factory=lambda m, **kw: MockRuntime(),
            max_depth=2,
            current_depth=2,
        )
        result = tool.execute({"task": "test", "role": "explore"})
        assert result.is_error is True
        assert "depth" in result.output.lower()

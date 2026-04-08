"""Tests for the extended agent role registry (BUILD_ROLE, GENERAL_ROLE)."""
from __future__ import annotations

import pytest

from llm_code.tools.agent_roles import (
    BUILD_ROLE,
    BUILT_IN_ROLES,
    EXPLORE_ROLE,
    GENERAL_ROLE,
    PLAN_ROLE,
    VERIFICATION_ROLE,
    is_tool_allowed_for_role,
)


def test_built_in_roles_contains_all_five() -> None:
    expected = {"build", "plan", "explore", "verify", "general"}
    assert set(BUILT_IN_ROLES.keys()) == expected


def test_build_role_has_empty_whitelist_meaning_unrestricted() -> None:
    assert BUILD_ROLE.allowed_tools == frozenset()
    assert BUILD_ROLE.name == "build"


def test_general_role_denies_todowrite() -> None:
    assert "todowrite" not in GENERAL_ROLE.allowed_tools
    assert "read_file" in GENERAL_ROLE.allowed_tools
    assert "write_file" in GENERAL_ROLE.allowed_tools
    assert "bash" in GENERAL_ROLE.allowed_tools


def test_explore_role_blocks_writes() -> None:
    assert "write_file" not in EXPLORE_ROLE.allowed_tools
    assert "edit_file" not in EXPLORE_ROLE.allowed_tools
    assert "bash" not in EXPLORE_ROLE.allowed_tools
    assert "read_file" in EXPLORE_ROLE.allowed_tools


def test_plan_role_blocks_writes_and_exec() -> None:
    assert "write_file" not in PLAN_ROLE.allowed_tools
    assert "bash" not in PLAN_ROLE.allowed_tools


def test_is_tool_allowed_returns_true_for_build_role_unrestricted() -> None:
    assert is_tool_allowed_for_role(BUILD_ROLE, "write_file") is True
    assert is_tool_allowed_for_role(BUILD_ROLE, "any_tool_at_all") is True


def test_is_tool_allowed_enforces_whitelist_for_explore() -> None:
    assert is_tool_allowed_for_role(EXPLORE_ROLE, "read_file") is True
    assert is_tool_allowed_for_role(EXPLORE_ROLE, "write_file") is False


def test_is_tool_allowed_enforces_whitelist_for_general() -> None:
    assert is_tool_allowed_for_role(GENERAL_ROLE, "bash") is True
    assert is_tool_allowed_for_role(GENERAL_ROLE, "todowrite") is False


def test_is_tool_allowed_with_none_role_is_unrestricted() -> None:
    assert is_tool_allowed_for_role(None, "anything") is True


@pytest.mark.parametrize("role_name", ["build", "plan", "explore", "verify", "general"])
def test_every_role_has_a_system_prompt_prefix(role_name: str) -> None:
    role = BUILT_IN_ROLES[role_name]
    assert role.system_prompt_prefix
    assert isinstance(role.system_prompt_prefix, str)

"""Tests for PermissionMode.PLAN and PermissionOutcome.NEED_PLAN."""
from __future__ import annotations

import pytest

from llm_code.tools.base import PermissionLevel
from llm_code.runtime.permissions import PermissionMode, PermissionOutcome, PermissionPolicy


class TestPlanModeEnum:
    def test_plan_mode_exists(self) -> None:
        assert PermissionMode.PLAN.value == "plan"

    def test_need_plan_outcome_exists(self) -> None:
        assert PermissionOutcome.NEED_PLAN.value == "need_plan"


class TestPlanModeAuthorization:
    def _policy(
        self,
        allow_tools: frozenset[str] = frozenset(),
        deny_tools: frozenset[str] = frozenset(),
        deny_patterns: tuple[str, ...] = (),
    ) -> PermissionPolicy:
        return PermissionPolicy(
            mode=PermissionMode.PLAN,
            allow_tools=allow_tools,
            deny_tools=deny_tools,
            deny_patterns=deny_patterns,
        )

    def test_read_only_tool_is_allowed(self) -> None:
        policy = self._policy()
        result = policy.authorize("read_file", PermissionLevel.READ_ONLY)
        assert result == PermissionOutcome.ALLOW

    def test_workspace_write_returns_need_plan(self) -> None:
        policy = self._policy()
        result = policy.authorize("write_file", PermissionLevel.WORKSPACE_WRITE)
        assert result == PermissionOutcome.NEED_PLAN

    def test_full_access_returns_need_plan(self) -> None:
        policy = self._policy()
        result = policy.authorize("bash", PermissionLevel.FULL_ACCESS)
        assert result == PermissionOutcome.NEED_PLAN

    def test_deny_list_takes_precedence_in_plan_mode(self) -> None:
        policy = self._policy(deny_tools=frozenset({"read_file"}))
        result = policy.authorize("read_file", PermissionLevel.READ_ONLY)
        assert result == PermissionOutcome.DENY

    def test_deny_pattern_takes_precedence_in_plan_mode(self) -> None:
        policy = self._policy(deny_patterns=("write_*",))
        result = policy.authorize("write_file", PermissionLevel.WORKSPACE_WRITE)
        assert result == PermissionOutcome.DENY

    def test_allow_list_overrides_to_allow_in_plan_mode(self) -> None:
        policy = self._policy(allow_tools=frozenset({"bash"}))
        result = policy.authorize("bash", PermissionLevel.FULL_ACCESS)
        assert result == PermissionOutcome.ALLOW

    def test_allow_list_for_read_only_tool_still_allows(self) -> None:
        policy = self._policy(allow_tools=frozenset({"read_file"}))
        result = policy.authorize("read_file", PermissionLevel.READ_ONLY)
        assert result == PermissionOutcome.ALLOW

    def test_deny_beats_allow_in_plan_mode(self) -> None:
        """deny_tools takes precedence over allow_tools (existing behavior)."""
        policy = self._policy(
            allow_tools=frozenset({"bash"}),
            deny_tools=frozenset({"bash"}),
        )
        result = policy.authorize("bash", PermissionLevel.FULL_ACCESS)
        assert result == PermissionOutcome.DENY

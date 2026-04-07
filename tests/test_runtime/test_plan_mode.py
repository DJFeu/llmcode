"""Plan mode permission gating — read-only tool allow-list by name."""
from __future__ import annotations

import pytest

from llm_code.runtime.permissions import (
    PLAN_MODE_DENY_MESSAGE,
    PLAN_MODE_READ_ONLY_TOOLS,
    PermissionMode,
    PermissionOutcome,
    PermissionPolicy,
    is_read_only_tool,
)
from llm_code.tools.base import PermissionLevel


READ_TOOLS = [
    "read_file",
    "glob_search",
    "grep_search",
    "web_search",
    "web_fetch",
    "notebook_read",
    "task_get",
    "task_list",
    "lsp_diagnose",
    "lsp_hover",
]

WRITE_TOOLS = [
    "write_file",
    "edit_file",
    "multi_edit",
    "bash",
    "notebook_edit",
]


class TestIsReadOnlyTool:
    @pytest.mark.parametrize("name", READ_TOOLS)
    def test_read_only_names_recognized(self, name: str) -> None:
        assert is_read_only_tool(name) is True

    @pytest.mark.parametrize("name", WRITE_TOOLS)
    def test_write_tools_not_read_only(self, name: str) -> None:
        assert is_read_only_tool(name) is False

    def test_read_only_set_is_frozen(self) -> None:
        assert isinstance(PLAN_MODE_READ_ONLY_TOOLS, frozenset)


class TestPlanModeGating:
    def _policy(self) -> PermissionPolicy:
        return PermissionPolicy(mode=PermissionMode.PLAN)

    @pytest.mark.parametrize("name", READ_TOOLS)
    def test_read_tools_allowed(self, name: str) -> None:
        # Even when declared at a higher level, the name-based check allows it.
        result = self._policy().authorize(name, PermissionLevel.WORKSPACE_WRITE)
        assert result == PermissionOutcome.ALLOW

    @pytest.mark.parametrize("name", WRITE_TOOLS)
    def test_write_tools_gated(self, name: str) -> None:
        result = self._policy().authorize(name, PermissionLevel.WORKSPACE_WRITE)
        assert result == PermissionOutcome.NEED_PLAN

    def test_mode_switch_allows_execution(self) -> None:
        policy = PermissionPolicy(mode=PermissionMode.WORKSPACE_WRITE)
        result = policy.authorize("write_file", PermissionLevel.WORKSPACE_WRITE)
        assert result == PermissionOutcome.ALLOW

    def test_deny_message_exists(self) -> None:
        assert "plan mode" in PLAN_MODE_DENY_MESSAGE.lower()
        assert "shift+tab" in PLAN_MODE_DENY_MESSAGE.lower()

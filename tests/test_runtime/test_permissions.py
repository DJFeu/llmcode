"""Tests for the permission policy system."""
from __future__ import annotations


from llm_code.tools.base import PermissionLevel
from llm_code.runtime.permissions import (
    PermissionMode,
    PermissionOutcome,
    PermissionPolicy,
)


class TestPermissionMode:
    def test_all_modes_exist(self):
        assert PermissionMode.READ_ONLY
        assert PermissionMode.WORKSPACE_WRITE
        assert PermissionMode.FULL_ACCESS
        assert PermissionMode.PROMPT
        assert PermissionMode.AUTO_ACCEPT


class TestPermissionOutcome:
    def test_outcomes_exist(self):
        assert PermissionOutcome.ALLOW
        assert PermissionOutcome.DENY
        assert PermissionOutcome.NEED_PROMPT


class TestAutoAccept:
    def test_auto_accept_allows_read(self):
        policy = PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT)
        assert policy.authorize("read_file", PermissionLevel.READ_ONLY) == PermissionOutcome.ALLOW

    def test_auto_accept_allows_write(self):
        policy = PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT)
        assert policy.authorize("write_file", PermissionLevel.WORKSPACE_WRITE) == PermissionOutcome.ALLOW

    def test_auto_accept_allows_full(self):
        policy = PermissionPolicy(mode=PermissionMode.AUTO_ACCEPT)
        assert policy.authorize("bash", PermissionLevel.FULL_ACCESS) == PermissionOutcome.ALLOW


class TestReadOnlyMode:
    def test_read_only_allows_read(self):
        policy = PermissionPolicy(mode=PermissionMode.READ_ONLY)
        assert policy.authorize("read_file", PermissionLevel.READ_ONLY) == PermissionOutcome.ALLOW

    def test_read_only_blocks_write(self):
        policy = PermissionPolicy(mode=PermissionMode.READ_ONLY)
        assert policy.authorize("write_file", PermissionLevel.WORKSPACE_WRITE) == PermissionOutcome.DENY

    def test_read_only_blocks_full(self):
        policy = PermissionPolicy(mode=PermissionMode.READ_ONLY)
        assert policy.authorize("bash", PermissionLevel.FULL_ACCESS) == PermissionOutcome.DENY


class TestWorkspaceWriteMode:
    def test_workspace_allows_read(self):
        policy = PermissionPolicy(mode=PermissionMode.WORKSPACE_WRITE)
        assert policy.authorize("read_file", PermissionLevel.READ_ONLY) == PermissionOutcome.ALLOW

    def test_workspace_allows_write(self):
        policy = PermissionPolicy(mode=PermissionMode.WORKSPACE_WRITE)
        assert policy.authorize("write_file", PermissionLevel.WORKSPACE_WRITE) == PermissionOutcome.ALLOW

    def test_workspace_blocks_full(self):
        policy = PermissionPolicy(mode=PermissionMode.WORKSPACE_WRITE)
        assert policy.authorize("bash", PermissionLevel.FULL_ACCESS) == PermissionOutcome.DENY


class TestFullAccessMode:
    def test_full_allows_read(self):
        policy = PermissionPolicy(mode=PermissionMode.FULL_ACCESS)
        assert policy.authorize("read_file", PermissionLevel.READ_ONLY) == PermissionOutcome.ALLOW

    def test_full_allows_write(self):
        policy = PermissionPolicy(mode=PermissionMode.FULL_ACCESS)
        assert policy.authorize("write_file", PermissionLevel.WORKSPACE_WRITE) == PermissionOutcome.ALLOW

    def test_full_allows_full(self):
        policy = PermissionPolicy(mode=PermissionMode.FULL_ACCESS)
        assert policy.authorize("bash", PermissionLevel.FULL_ACCESS) == PermissionOutcome.ALLOW


class TestPromptMode:
    def test_prompt_allows_read(self):
        policy = PermissionPolicy(mode=PermissionMode.PROMPT)
        assert policy.authorize("read_file", PermissionLevel.READ_ONLY) == PermissionOutcome.ALLOW

    def test_prompt_needs_prompt_for_write(self):
        policy = PermissionPolicy(mode=PermissionMode.PROMPT)
        assert policy.authorize("write_file", PermissionLevel.WORKSPACE_WRITE) == PermissionOutcome.NEED_PROMPT

    def test_prompt_needs_prompt_for_full(self):
        policy = PermissionPolicy(mode=PermissionMode.PROMPT)
        assert policy.authorize("bash", PermissionLevel.FULL_ACCESS) == PermissionOutcome.NEED_PROMPT


class TestDenyList:
    def test_denied_tool_returns_deny(self):
        policy = PermissionPolicy(
            mode=PermissionMode.AUTO_ACCEPT,
            deny_tools=frozenset({"bash"}),
        )
        assert policy.authorize("bash", PermissionLevel.READ_ONLY) == PermissionOutcome.DENY

    def test_denied_tool_overrides_allow_list(self):
        policy = PermissionPolicy(
            mode=PermissionMode.AUTO_ACCEPT,
            allow_tools=frozenset({"bash"}),
            deny_tools=frozenset({"bash"}),
        )
        assert policy.authorize("bash", PermissionLevel.READ_ONLY) == PermissionOutcome.DENY

    def test_non_denied_tool_unaffected(self):
        policy = PermissionPolicy(
            mode=PermissionMode.AUTO_ACCEPT,
            deny_tools=frozenset({"bash"}),
        )
        assert policy.authorize("read_file", PermissionLevel.READ_ONLY) == PermissionOutcome.ALLOW


class TestAllowList:
    def test_allowed_tool_returns_allow(self):
        policy = PermissionPolicy(
            mode=PermissionMode.PROMPT,
            allow_tools=frozenset({"read_file"}),
        )
        assert policy.authorize("read_file", PermissionLevel.READ_ONLY) == PermissionOutcome.ALLOW

    def test_allowed_tool_bypasses_prompt_for_elevated(self):
        """Explicitly allowed tools bypass PROMPT mode level check."""
        policy = PermissionPolicy(
            mode=PermissionMode.PROMPT,
            allow_tools=frozenset({"write_file"}),
        )
        assert policy.authorize("write_file", PermissionLevel.WORKSPACE_WRITE) == PermissionOutcome.ALLOW

    def test_tool_not_in_allow_list_follows_mode(self):
        policy = PermissionPolicy(
            mode=PermissionMode.PROMPT,
            allow_tools=frozenset({"read_file"}),
        )
        # write_file not in allow_list, PROMPT mode → NEED_PROMPT
        assert policy.authorize("write_file", PermissionLevel.WORKSPACE_WRITE) == PermissionOutcome.NEED_PROMPT


class TestDenyPatterns:
    def test_deny_pattern_blocks_matching_tool(self):
        policy = PermissionPolicy(
            mode=PermissionMode.AUTO_ACCEPT,
            deny_patterns=("dangerous_*",),
        )
        assert policy.authorize("dangerous_bash", PermissionLevel.READ_ONLY) == PermissionOutcome.DENY

    def test_deny_pattern_does_not_block_non_matching(self):
        policy = PermissionPolicy(
            mode=PermissionMode.AUTO_ACCEPT,
            deny_patterns=("dangerous_*",),
        )
        assert policy.authorize("safe_tool", PermissionLevel.READ_ONLY) == PermissionOutcome.ALLOW

    def test_multiple_deny_patterns(self):
        policy = PermissionPolicy(
            mode=PermissionMode.AUTO_ACCEPT,
            deny_patterns=("bash*", "shell_*"),
        )
        assert policy.authorize("bash_exec", PermissionLevel.READ_ONLY) == PermissionOutcome.DENY
        assert policy.authorize("shell_run", PermissionLevel.READ_ONLY) == PermissionOutcome.DENY
        assert policy.authorize("read_file", PermissionLevel.READ_ONLY) == PermissionOutcome.ALLOW

    def test_deny_pattern_overrides_allow(self):
        policy = PermissionPolicy(
            mode=PermissionMode.AUTO_ACCEPT,
            allow_tools=frozenset({"bash_exec"}),
            deny_patterns=("bash*",),
        )
        assert policy.authorize("bash_exec", PermissionLevel.READ_ONLY) == PermissionOutcome.DENY

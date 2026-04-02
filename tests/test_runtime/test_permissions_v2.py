"""Tests for Permission.authorize() effective_level parameter (v2)."""
from __future__ import annotations


from llm_code.tools.base import PermissionLevel
from llm_code.runtime.permissions import (
    PermissionMode,
    PermissionOutcome,
    PermissionPolicy,
)


class TestEffectiveLevel:
    def test_effective_level_overrides_required(self):
        """PROMPT mode, bash FULL_ACCESS required, but effective=READ_ONLY → ALLOW."""
        policy = PermissionPolicy(mode=PermissionMode.PROMPT)
        outcome = policy.authorize(
            "bash",
            PermissionLevel.FULL_ACCESS,
            effective_level=PermissionLevel.READ_ONLY,
        )
        assert outcome == PermissionOutcome.ALLOW

    def test_effective_level_none_uses_required(self):
        """READ_ONLY mode, bash FULL_ACCESS, effective=None → DENY (uses required)."""
        policy = PermissionPolicy(mode=PermissionMode.READ_ONLY)
        outcome = policy.authorize(
            "bash",
            PermissionLevel.FULL_ACCESS,
            effective_level=None,
        )
        assert outcome == PermissionOutcome.DENY

    def test_effective_level_destructive_stays_full(self):
        """PROMPT mode, effective=FULL_ACCESS → NEED_PROMPT."""
        policy = PermissionPolicy(mode=PermissionMode.PROMPT)
        outcome = policy.authorize(
            "bash",
            PermissionLevel.FULL_ACCESS,
            effective_level=PermissionLevel.FULL_ACCESS,
        )
        assert outcome == PermissionOutcome.NEED_PROMPT

    def test_deny_list_wins_over_effective(self):
        """AUTO_ACCEPT + deny bash, effective=READ_ONLY → still DENY."""
        policy = PermissionPolicy(
            mode=PermissionMode.AUTO_ACCEPT,
            deny_tools=frozenset({"bash"}),
        )
        outcome = policy.authorize(
            "bash",
            PermissionLevel.FULL_ACCESS,
            effective_level=PermissionLevel.READ_ONLY,
        )
        assert outcome == PermissionOutcome.DENY

    def test_allow_list_wins(self):
        """READ_ONLY mode + allow bash, effective=FULL_ACCESS → ALLOW."""
        policy = PermissionPolicy(
            mode=PermissionMode.READ_ONLY,
            allow_tools=frozenset({"bash"}),
        )
        outcome = policy.authorize(
            "bash",
            PermissionLevel.FULL_ACCESS,
            effective_level=PermissionLevel.FULL_ACCESS,
        )
        assert outcome == PermissionOutcome.ALLOW

    def test_workspace_write_with_read_only_effective(self):
        """WORKSPACE_WRITE mode, bash effective=READ_ONLY → ALLOW."""
        policy = PermissionPolicy(mode=PermissionMode.WORKSPACE_WRITE)
        outcome = policy.authorize(
            "bash",
            PermissionLevel.FULL_ACCESS,
            effective_level=PermissionLevel.READ_ONLY,
        )
        assert outcome == PermissionOutcome.ALLOW

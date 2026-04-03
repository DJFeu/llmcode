"""Tests for PermissionPolicy shadowed-rule detection (detect_shadowed_rules)."""
from __future__ import annotations

import logging

import pytest

from llm_code.runtime.permissions import (
    PermissionMode,
    PermissionPolicy,
    detect_shadowed_rules,
)


# ---------------------------------------------------------------------------
# Unit tests for detect_shadowed_rules
# ---------------------------------------------------------------------------


class TestDetectShadowedRules:
    def test_no_conflicts_returns_empty(self) -> None:
        warnings = detect_shadowed_rules(
            allow_tools=frozenset({"bash"}),
            deny_tools=frozenset({"write_file"}),
            mode=PermissionMode.WORKSPACE_WRITE,
        )
        assert warnings == []

    def test_allow_shadowed_by_deny(self) -> None:
        warnings = detect_shadowed_rules(
            allow_tools=frozenset({"bash", "read_file"}),
            deny_tools=frozenset({"bash"}),
            mode=PermissionMode.WORKSPACE_WRITE,
        )
        assert any("bash" in w and "deny takes precedence" in w for w in warnings)

    def test_multiple_shadowed_tools(self) -> None:
        warnings = detect_shadowed_rules(
            allow_tools=frozenset({"bash", "write_file", "read_file"}),
            deny_tools=frozenset({"bash", "write_file"}),
            mode=PermissionMode.WORKSPACE_WRITE,
        )
        shadowed_warnings = [w for w in warnings if "deny takes precedence" in w]
        assert len(shadowed_warnings) == 2

    def test_redundant_allow_in_full_access_mode(self) -> None:
        warnings = detect_shadowed_rules(
            allow_tools=frozenset({"bash"}),
            deny_tools=frozenset(),
            mode=PermissionMode.FULL_ACCESS,
        )
        assert any("bash" in w and "Redundant allow" in w for w in warnings)

    def test_redundant_allow_in_auto_accept_mode(self) -> None:
        warnings = detect_shadowed_rules(
            allow_tools=frozenset({"read_file", "bash"}),
            deny_tools=frozenset(),
            mode=PermissionMode.AUTO_ACCEPT,
        )
        assert any("Redundant allow" in w for w in warnings)
        # All non-denied tools should be flagged
        flagged_tools = {
            w.split("'")[1] for w in warnings if "Redundant allow" in w
        }
        assert "read_file" in flagged_tools
        assert "bash" in flagged_tools

    def test_redundant_deny_in_read_only_mode(self) -> None:
        warnings = detect_shadowed_rules(
            allow_tools=frozenset(),
            deny_tools=frozenset({"bash"}),
            mode=PermissionMode.READ_ONLY,
        )
        assert any("bash" in w and "Redundant deny" in w for w in warnings)

    def test_multiple_redundant_denies_in_read_only_mode(self) -> None:
        warnings = detect_shadowed_rules(
            allow_tools=frozenset(),
            deny_tools=frozenset({"bash", "write_file"}),
            mode=PermissionMode.READ_ONLY,
        )
        redundant = [w for w in warnings if "Redundant deny" in w]
        assert len(redundant) == 2

    def test_shadowed_tool_not_double_counted_as_redundant_allow(self) -> None:
        # A tool in both allow and deny is flagged for shadowing only,
        # not also as a redundant allow in full_access mode.
        warnings = detect_shadowed_rules(
            allow_tools=frozenset({"bash"}),
            deny_tools=frozenset({"bash"}),
            mode=PermissionMode.FULL_ACCESS,
        )
        # Should have a shadowed warning
        assert any("deny takes precedence" in w for w in warnings)
        # The redundant-allow warning is for allow_tools - deny_tools only,
        # so 'bash' (which is also denied) should NOT appear as redundant-allow.
        assert not any("Redundant allow" in w and "bash" in w for w in warnings)

    def test_no_warnings_for_prompt_mode(self) -> None:
        # PROMPT mode neither unconditionally allows nor fully blocks elevated tools
        warnings = detect_shadowed_rules(
            allow_tools=frozenset({"bash"}),
            deny_tools=frozenset(),
            mode=PermissionMode.PROMPT,
        )
        # Should not trigger redundant-allow (PROMPT is not in unconditional list)
        redundant = [w for w in warnings if "Redundant allow" in w]
        assert redundant == []

    def test_empty_lists_no_warnings(self) -> None:
        for mode in PermissionMode:
            warnings = detect_shadowed_rules(
                allow_tools=frozenset(),
                deny_tools=frozenset(),
                mode=mode,
            )
            assert warnings == [], f"Unexpected warnings for mode {mode}: {warnings}"


# ---------------------------------------------------------------------------
# PermissionPolicy logs warnings at init time
# ---------------------------------------------------------------------------


class TestPermissionPolicyLogsConflicts:
    def test_logs_warning_for_shadowed_rule(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="llm_code.runtime.permissions"):
            PermissionPolicy(
                mode=PermissionMode.WORKSPACE_WRITE,
                allow_tools=frozenset({"bash"}),
                deny_tools=frozenset({"bash"}),
            )
        assert any("bash" in r.message and "deny takes precedence" in r.message for r in caplog.records)

    def test_logs_warning_for_redundant_allow_in_full_access(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="llm_code.runtime.permissions"):
            PermissionPolicy(
                mode=PermissionMode.FULL_ACCESS,
                allow_tools=frozenset({"read_file"}),
                deny_tools=frozenset(),
            )
        assert any("Redundant allow" in r.message for r in caplog.records)

    def test_logs_warning_for_redundant_deny_in_read_only(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="llm_code.runtime.permissions"):
            PermissionPolicy(
                mode=PermissionMode.READ_ONLY,
                allow_tools=frozenset(),
                deny_tools=frozenset({"write_file"}),
            )
        assert any("Redundant deny" in r.message for r in caplog.records)

    def test_no_warning_for_clean_policy(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="llm_code.runtime.permissions"):
            PermissionPolicy(
                mode=PermissionMode.WORKSPACE_WRITE,
                allow_tools=frozenset({"special_tool"}),
                deny_tools=frozenset({"bash"}),
            )
        assert caplog.records == []

    def test_no_warning_for_default_policy(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="llm_code.runtime.permissions"):
            PermissionPolicy(mode=PermissionMode.FULL_ACCESS)
        assert caplog.records == []


# ---------------------------------------------------------------------------
# authorize() still behaves correctly when warnings are emitted
# ---------------------------------------------------------------------------


class TestAuthorizeUnaffectedByConflictDetection:
    def test_deny_wins_over_allow_even_with_warning(self) -> None:
        from llm_code.tools.base import PermissionLevel
        from llm_code.runtime.permissions import PermissionOutcome

        policy = PermissionPolicy(
            mode=PermissionMode.WORKSPACE_WRITE,
            allow_tools=frozenset({"bash"}),
            deny_tools=frozenset({"bash"}),
        )
        # deny list wins even though allow list also contains the tool
        assert policy.authorize("bash", PermissionLevel.FULL_ACCESS) == PermissionOutcome.DENY

    def test_redundant_allow_still_authorizes(self) -> None:
        from llm_code.tools.base import PermissionLevel
        from llm_code.runtime.permissions import PermissionOutcome

        policy = PermissionPolicy(
            mode=PermissionMode.FULL_ACCESS,
            allow_tools=frozenset({"bash"}),
            deny_tools=frozenset(),
        )
        # Even though the allow is redundant, the tool is still authorized
        assert policy.authorize("bash", PermissionLevel.FULL_ACCESS) == PermissionOutcome.ALLOW

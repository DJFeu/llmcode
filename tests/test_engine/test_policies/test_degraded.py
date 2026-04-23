"""Tests for :mod:`llm_code.engine.policies.degraded`."""
from __future__ import annotations

import pytest

from llm_code.engine.policies import DegradedModePolicy
from llm_code.engine.policies.degraded import (
    BudgetDegraded,
    ConsecutiveFailureDegraded,
    NoDegraded,
    READ_ONLY_TOOLS,
    all_read_only,
)


class _Result:
    """Minimal tool result for tests; `is_error` attribute only."""

    def __init__(self, is_error: bool):
        self.is_error = is_error


# ---------------------------------------------------------------------------
# NoDegraded
# ---------------------------------------------------------------------------


class TestNoDegraded:
    def test_never_degrades(self):
        decision = NoDegraded().check({"tool_results": [_Result(True)] * 10})
        assert decision.should_degrade is False

    def test_protocol_conformance(self):
        assert isinstance(NoDegraded(), DegradedModePolicy)


# ---------------------------------------------------------------------------
# ConsecutiveFailureDegraded
# ---------------------------------------------------------------------------


class TestConsecutiveFailureDegraded:
    def test_validates_threshold(self):
        with pytest.raises(ValueError):
            ConsecutiveFailureDegraded(threshold=0)

    def test_empty_results_no_degrade(self):
        policy = ConsecutiveFailureDegraded(threshold=3)
        assert policy.check({"tool_results": []}).should_degrade is False

    def test_below_threshold_no_degrade(self):
        policy = ConsecutiveFailureDegraded(threshold=3)
        state = {"tool_results": [_Result(True), _Result(True)]}
        assert policy.check(state).should_degrade is False

    def test_threshold_hit_degrades(self):
        policy = ConsecutiveFailureDegraded(threshold=3)
        state = {"tool_results": [_Result(True)] * 3}
        decision = policy.check(state)
        assert decision.should_degrade is True
        assert decision.allowed_tools == READ_ONLY_TOOLS
        assert "3 consecutive" in decision.reason

    def test_recent_success_breaks_streak(self):
        policy = ConsecutiveFailureDegraded(threshold=3)
        # Three failures, one success, three failures — last 3 all fail, so trigger.
        state = {
            "tool_results": [
                _Result(True), _Result(True), _Result(True),
                _Result(False),
                _Result(True), _Result(True), _Result(True),
            ]
        }
        assert policy.check(state).should_degrade is True

    def test_last_window_with_success_no_degrade(self):
        policy = ConsecutiveFailureDegraded(threshold=3)
        state = {
            "tool_results": [_Result(True), _Result(True), _Result(False)]
        }
        assert policy.check(state).should_degrade is False

    def test_accepts_dict_stubs(self):
        policy = ConsecutiveFailureDegraded(threshold=2)
        state = {"tool_results": [{"is_error": True}, {"is_error": True}]}
        assert policy.check(state).should_degrade is True

    def test_custom_allowed_tools(self):
        custom = frozenset({"foo", "bar"})
        policy = ConsecutiveFailureDegraded(threshold=1, allowed_tools=custom)
        state = {"tool_results": [_Result(True)]}
        decision = policy.check(state)
        assert decision.allowed_tools == custom

    def test_object_without_is_error_not_counted(self):
        # Raw objects without the attribute are treated as success.
        policy = ConsecutiveFailureDegraded(threshold=2)
        state = {"tool_results": [object(), _Result(True)]}
        assert policy.check(state).should_degrade is False


# ---------------------------------------------------------------------------
# BudgetDegraded
# ---------------------------------------------------------------------------


class TestBudgetDegraded:
    def test_threshold_validation(self):
        with pytest.raises(ValueError):
            BudgetDegraded(lambda s: 0.5, threshold=0.0)
        with pytest.raises(ValueError):
            BudgetDegraded(lambda s: 0.5, threshold=1.5)

    def test_below_threshold_no_degrade(self):
        policy = BudgetDegraded(lambda s: 0.5, threshold=0.8)
        assert policy.check({}).should_degrade is False

    def test_at_threshold_degrades(self):
        policy = BudgetDegraded(lambda s: 0.8, threshold=0.8)
        decision = policy.check({})
        assert decision.should_degrade is True
        assert decision.allowed_tools == READ_ONLY_TOOLS

    def test_above_threshold_degrades(self):
        policy = BudgetDegraded(lambda s: 0.95, threshold=0.8)
        assert policy.check({}).should_degrade is True

    def test_usage_fn_exception_no_crash(self):
        def bad(s):
            raise RuntimeError("broken")

        policy = BudgetDegraded(bad, threshold=0.8)
        decision = policy.check({})
        assert decision.should_degrade is False
        assert "usage_fn raised" in decision.reason

    def test_custom_allowed_tools(self):
        policy = BudgetDegraded(
            lambda s: 1.0,
            threshold=0.5,
            allowed_tools=frozenset({"only_this"}),
        )
        decision = policy.check({})
        assert decision.allowed_tools == frozenset({"only_this"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestAllReadOnly:
    def test_true_when_all_in_set(self):
        assert all_read_only({"read_file", "grep_search"}) is True

    def test_false_when_one_missing(self):
        assert all_read_only({"read_file", "write_file"}) is False

    def test_empty_returns_true(self):
        assert all_read_only([]) is True

    def test_read_only_tools_is_frozenset(self):
        assert isinstance(READ_ONLY_TOOLS, frozenset)

    def test_read_only_tools_contains_expected(self):
        for t in ("read_file", "grep_search", "web_fetch"):
            assert t in READ_ONLY_TOOLS

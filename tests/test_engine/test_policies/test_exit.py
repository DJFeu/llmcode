"""Tests for :mod:`llm_code.engine.policies.exit`."""
from __future__ import annotations

import pytest

from llm_code.engine.policies import ExitCondition
from llm_code.engine.policies.exit import (
    BudgetExhausted,
    CompositeExit,
    DenialThreshold,
    ExplicitExitTool,
    MaxStepsReached,
    NoProgress,
)


# ---------------------------------------------------------------------------
# MaxStepsReached
# ---------------------------------------------------------------------------


class TestMaxStepsReached:
    def test_validates_cap(self):
        with pytest.raises(ValueError):
            MaxStepsReached(cap=0)

    def test_validates_warning_offset(self):
        with pytest.raises(ValueError):
            MaxStepsReached(cap=10, warning_offset=-1)

    def test_below_cap_does_not_exit(self):
        policy = MaxStepsReached(cap=10)
        assert policy.should_exit({"iteration": 5}) == (False, "")

    def test_at_cap_exits(self):
        policy = MaxStepsReached(cap=10)
        ok, reason = policy.should_exit({"iteration": 10})
        assert ok is True
        assert "10/10" in reason

    def test_over_cap_exits(self):
        policy = MaxStepsReached(cap=10)
        ok, _ = policy.should_exit({"iteration": 15})
        assert ok is True

    def test_no_iteration_defaults_zero(self):
        policy = MaxStepsReached(cap=1)
        assert policy.should_exit({}) == (False, "")

    def test_warning_reminder_at_cap_minus_offset(self):
        policy = MaxStepsReached(cap=50, warning_offset=5)
        # emitted at iteration 45
        assert policy.warning_reminder({"iteration": 45}) is not None
        # not at other iterations
        assert policy.warning_reminder({"iteration": 44}) is None
        assert policy.warning_reminder({"iteration": 46}) is None

    def test_warning_reminder_mentions_remaining(self):
        policy = MaxStepsReached(cap=50, warning_offset=5)
        text = policy.warning_reminder({"iteration": 45})
        assert text is not None
        assert "45" in text
        assert "50" in text

    def test_protocol_conformance(self):
        assert isinstance(MaxStepsReached(), ExitCondition)

    def test_cap_property(self):
        assert MaxStepsReached(cap=42).cap == 42


# ---------------------------------------------------------------------------
# NoProgress
# ---------------------------------------------------------------------------


class _Call:
    def __init__(self, name: str, args: dict):
        self.tool_name = name
        self.args = args


class TestNoProgress:
    def test_validates_window(self):
        with pytest.raises(ValueError):
            NoProgress(window=1)

    def test_not_enough_calls(self):
        policy = NoProgress(window=3)
        assert policy.should_exit({"tool_calls": [_Call("a", {})]}) == (False, "")

    def test_identical_calls_exit(self):
        policy = NoProgress(window=3)
        calls = [_Call("read_file", {"path": "/x"})] * 3
        ok, reason = policy.should_exit({"tool_calls": calls})
        assert ok is True
        assert "no progress" in reason

    def test_different_args_no_exit(self):
        policy = NoProgress(window=3)
        calls = [
            _Call("read_file", {"path": "/a"}),
            _Call("read_file", {"path": "/b"}),
            _Call("read_file", {"path": "/a"}),
        ]
        assert policy.should_exit({"tool_calls": calls}) == (False, "")

    def test_different_tools_no_exit(self):
        policy = NoProgress(window=3)
        calls = [
            _Call("read_file", {"path": "/a"}),
            _Call("read_file", {"path": "/a"}),
            _Call("bash", {"cmd": "ls"}),
        ]
        assert policy.should_exit({"tool_calls": calls}) == (False, "")

    def test_dict_calls_supported(self):
        policy = NoProgress(window=2)
        calls = [
            {"tool_name": "x", "args": {"q": 1}},
            {"tool_name": "x", "args": {"q": 1}},
        ]
        ok, _ = policy.should_exit({"tool_calls": calls})
        assert ok is True

    def test_unhashable_args_handled(self):
        policy = NoProgress(window=2)
        calls = [
            _Call("x", {"obj": object()}),  # opaque object
            _Call("x", {"obj": object()}),
        ]
        # Shouldn't crash even though objects aren't JSON-serializable.
        decision = policy.should_exit({"tool_calls": calls})
        assert decision[0] in (True, False)


# ---------------------------------------------------------------------------
# ExplicitExitTool
# ---------------------------------------------------------------------------


class TestExplicitExitTool:
    def test_default_tool_name(self):
        assert ExplicitExitTool().tool_name == "exit_agent"

    def test_custom_tool_name(self):
        policy = ExplicitExitTool("done")
        assert policy.tool_name == "done"

    def test_exit_when_last_call_is_sentinel(self):
        policy = ExplicitExitTool()
        calls = [_Call("exit_agent", {})]
        ok, _ = policy.should_exit({"tool_calls": calls})
        assert ok is True

    def test_no_exit_for_other_tool(self):
        policy = ExplicitExitTool()
        calls = [_Call("read_file", {})]
        assert policy.should_exit({"tool_calls": calls}) == (False, "")

    def test_only_checks_last_call(self):
        policy = ExplicitExitTool()
        calls = [_Call("exit_agent", {}), _Call("read_file", {})]
        assert policy.should_exit({"tool_calls": calls}) == (False, "")

    def test_empty_calls_no_exit(self):
        assert ExplicitExitTool().should_exit({"tool_calls": []}) == (False, "")

    def test_dict_call_supported(self):
        calls = [{"tool_name": "exit_agent"}]
        ok, _ = ExplicitExitTool().should_exit({"tool_calls": calls})
        assert ok is True


# ---------------------------------------------------------------------------
# DenialThreshold
# ---------------------------------------------------------------------------


class TestDenialThreshold:
    def test_validates_threshold(self):
        with pytest.raises(ValueError):
            DenialThreshold(threshold=0)

    def test_window_must_gte_threshold(self):
        with pytest.raises(ValueError):
            DenialThreshold(threshold=5, window=3)

    def test_below_threshold_no_exit(self):
        policy = DenialThreshold(threshold=3, window=10)
        state = {"denial_history": [_Call("x", {})] * 2}
        assert policy.should_exit(state) == (False, "")

    def test_threshold_hit_exits(self):
        policy = DenialThreshold(threshold=3, window=10)
        state = {"denial_history": [_Call("x", {})] * 3}
        ok, reason = policy.should_exit(state)
        assert ok is True
        assert "denials" in reason

    def test_window_bounds_recency(self):
        # 20 denials but window=5 means only last 5 counted.
        # threshold=3 means we exit regardless since 5 >= 3.
        policy = DenialThreshold(threshold=3, window=5)
        state = {"denial_history": [_Call("x", {})] * 20}
        assert policy.should_exit(state)[0] is True


# ---------------------------------------------------------------------------
# BudgetExhausted
# ---------------------------------------------------------------------------


class TestBudgetExhausted:
    def test_below_threshold_no_exit(self):
        policy = BudgetExhausted(lambda s: 0.5)
        assert policy.should_exit({}) == (False, "")

    def test_at_threshold_exits(self):
        policy = BudgetExhausted(lambda s: 1.0)
        ok, reason = policy.should_exit({})
        assert ok is True
        assert "exhausted" in reason

    def test_custom_threshold(self):
        policy = BudgetExhausted(lambda s: 0.5, threshold=0.5)
        assert policy.should_exit({})[0] is True

    def test_usage_fn_exception_no_exit(self):
        def bad(s):
            raise RuntimeError("broken")

        policy = BudgetExhausted(bad)
        ok, reason = policy.should_exit({})
        assert ok is False
        assert "usage_fn raised" in reason


# ---------------------------------------------------------------------------
# CompositeExit
# ---------------------------------------------------------------------------


class _Trip:
    def __init__(self, reason: str):
        self.reason = reason

    def should_exit(self, state):
        return True, self.reason


class _NoTrip:
    def should_exit(self, state):
        return False, ""


class TestCompositeExit:
    def test_first_match_wins(self):
        composite = CompositeExit([_Trip("A"), _Trip("B")])
        ok, reason = composite.should_exit({})
        assert ok is True
        assert reason == "A"

    def test_walks_through_non_matching(self):
        composite = CompositeExit([_NoTrip(), _Trip("found")])
        ok, reason = composite.should_exit({})
        assert ok is True
        assert reason == "found"

    def test_no_match_returns_false(self):
        composite = CompositeExit([_NoTrip(), _NoTrip()])
        assert composite.should_exit({}) == (False, "")

    def test_empty_members_no_exit(self):
        composite = CompositeExit([])
        assert composite.should_exit({}) == (False, "")

    def test_members_property_returns_tuple(self):
        m1, m2 = _NoTrip(), _NoTrip()
        composite = CompositeExit([m1, m2])
        assert composite.members == (m1, m2)

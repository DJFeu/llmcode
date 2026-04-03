"""Tests for budget enforcement in CostTracker."""
from __future__ import annotations

import pytest

from llm_code.runtime.cost_tracker import BudgetExceededError, CostTracker


class TestBudgetNotExceeded:
    def test_no_budget_never_exceeded(self):
        tracker = CostTracker(model="gpt-4o-mini")
        tracker.add_usage(1_000_000, 1_000_000)
        assert not tracker.is_budget_exceeded()

    def test_budget_not_exceeded_when_under(self):
        tracker = CostTracker(model="gpt-4o-mini", max_budget_usd=1.0)
        # gpt-4o-mini: $0.15/1M in, $0.60/1M out
        # 100k tokens each ≈ $0.015 + $0.060 = $0.075 — under $1
        tracker.add_usage(100_000, 100_000)
        assert not tracker.is_budget_exceeded()

    def test_check_budget_does_not_raise_when_under(self):
        tracker = CostTracker(model="gpt-4o-mini", max_budget_usd=1.0)
        tracker.add_usage(100_000, 100_000)
        tracker.check_budget()  # should not raise


class TestBudgetExceeded:
    def test_is_budget_exceeded_when_over(self):
        tracker = CostTracker(model="gpt-4o-mini", max_budget_usd=0.001)
        # 1M tokens in + out at gpt-4o-mini pricing will cost $0.75 >> $0.001
        tracker.add_usage(1_000_000, 1_000_000)
        assert tracker.is_budget_exceeded()

    def test_check_budget_raises_budget_exceeded_error(self):
        tracker = CostTracker(model="gpt-4o-mini", max_budget_usd=0.001)
        tracker.add_usage(1_000_000, 1_000_000)
        with pytest.raises(BudgetExceededError) as exc_info:
            tracker.check_budget()
        assert exc_info.value.budget == 0.001
        assert exc_info.value.spent > 0.001

    def test_budget_exceeded_error_message(self):
        tracker = CostTracker(model="gpt-4o-mini", max_budget_usd=0.50)
        tracker.add_usage(1_000_000, 1_000_000)
        with pytest.raises(BudgetExceededError) as exc_info:
            tracker.check_budget()
        assert "$0.50" in str(exc_info.value)


class TestRemainingBudget:
    def test_remaining_budget_none_when_no_budget(self):
        tracker = CostTracker(model="gpt-4o-mini")
        assert tracker.remaining_budget() is None

    def test_remaining_budget_decreases_with_usage(self):
        tracker = CostTracker(model="gpt-4o-mini", max_budget_usd=1.0)
        cost = tracker.add_usage(100_000, 100_000)
        remaining = tracker.remaining_budget()
        assert remaining is not None
        assert abs(remaining - (1.0 - cost)) < 1e-9

    def test_remaining_budget_floored_at_zero(self):
        tracker = CostTracker(model="gpt-4o-mini", max_budget_usd=0.001)
        tracker.add_usage(1_000_000, 1_000_000)
        assert tracker.remaining_budget() == 0.0

    def test_remaining_budget_equals_full_budget_when_unused(self):
        tracker = CostTracker(model="gpt-4o-mini", max_budget_usd=2.50)
        assert tracker.remaining_budget() == 2.50


class TestCacheAwarePricing:
    def test_add_usage_returns_request_cost(self):
        tracker = CostTracker(model="gpt-4o-mini")
        cost = tracker.add_usage(1_000_000, 0)
        # gpt-4o-mini input: $0.15/1M
        assert abs(cost - 0.15) < 1e-9

    def test_cache_read_tokens_at_10_percent_input_price(self):
        tracker = CostTracker(model="gpt-4o-mini")
        # 1M cache_read_tokens at 10% of $0.15/1M = $0.015
        cost = tracker.add_usage(0, 0, cache_read_tokens=1_000_000)
        assert abs(cost - 0.015) < 1e-9

    def test_cache_creation_tokens_at_125_percent_input_price(self):
        tracker = CostTracker(model="gpt-4o-mini")
        # 1M cache_creation_tokens at 125% of $0.15/1M = $0.1875
        cost = tracker.add_usage(0, 0, cache_creation_tokens=1_000_000)
        assert abs(cost - 0.1875) < 1e-9

    def test_cache_tokens_default_to_zero(self):
        tracker = CostTracker(model="gpt-4o-mini")
        cost_with_defaults = tracker.add_usage(100_000, 100_000)
        tracker2 = CostTracker(model="gpt-4o-mini")
        cost_explicit = tracker2.add_usage(100_000, 100_000, cache_read_tokens=0, cache_creation_tokens=0)
        assert abs(cost_with_defaults - cost_explicit) < 1e-12

    def test_combined_cache_and_regular_tokens(self):
        tracker = CostTracker(model="gpt-4o-mini")
        # 1M in ($0.15) + 1M out ($0.60) + 1M cache_read ($0.015) + 1M cache_create ($0.1875)
        cost = tracker.add_usage(1_000_000, 1_000_000, 1_000_000, 1_000_000)
        expected = 0.15 + 0.60 + 0.015 + 0.1875
        assert abs(cost - expected) < 1e-9

    def test_cache_tokens_accumulate_in_total_cost(self):
        tracker = CostTracker(model="gpt-4o-mini", max_budget_usd=0.001)
        # Cache creation at 125% should accumulate and exceed budget
        tracker.add_usage(0, 0, cache_creation_tokens=1_000_000)
        assert tracker.is_budget_exceeded()


class TestNoBudget:
    def test_no_budget_check_always_passes(self):
        tracker = CostTracker(model="gpt-4o-mini", max_budget_usd=None)
        tracker.add_usage(10_000_000, 10_000_000)
        tracker.check_budget()  # should never raise
        assert not tracker.is_budget_exceeded()
        assert tracker.remaining_budget() is None

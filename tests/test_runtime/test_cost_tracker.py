"""Tests for CostTracker."""
from __future__ import annotations

import pytest

from llm_code.runtime.cost_tracker import CostTracker, BUILTIN_PRICING


class TestCostTrackerDefaultPricing:
    def test_known_model_exact_match(self) -> None:
        tracker = CostTracker(model="gpt-4o")
        tracker.add_usage(input_tokens=1_000_000, output_tokens=0)
        # 1M input tokens at $2.50/1M = $2.50
        assert abs(tracker.total_cost_usd - 2.50) < 1e-9

    def test_known_model_output_pricing(self) -> None:
        tracker = CostTracker(model="gpt-4o")
        tracker.add_usage(input_tokens=0, output_tokens=1_000_000)
        # 1M output tokens at $10.00/1M = $10.00
        assert abs(tracker.total_cost_usd - 10.00) < 1e-9

    def test_anthropic_model_pricing(self) -> None:
        tracker = CostTracker(model="claude-sonnet-4-6")
        tracker.add_usage(input_tokens=1_000_000, output_tokens=1_000_000)
        # $3.00 input + $15.00 output = $18.00
        assert abs(tracker.total_cost_usd - 18.00) < 1e-9

    def test_partial_match_model(self) -> None:
        tracker = CostTracker(model="my-gpt-4o-custom")
        tracker.add_usage(input_tokens=1_000_000, output_tokens=0)
        # Partial match on "gpt-4o" key: $2.50/1M
        assert abs(tracker.total_cost_usd - 2.50) < 1e-9

    def test_cumulative_usage(self) -> None:
        tracker = CostTracker(model="gpt-4o-mini")
        tracker.add_usage(input_tokens=500_000, output_tokens=0)
        tracker.add_usage(input_tokens=500_000, output_tokens=0)
        # 1M input at $0.15/1M = $0.15
        assert abs(tracker.total_cost_usd - 0.15) < 1e-9
        assert tracker.total_input_tokens == 1_000_000
        assert tracker.total_output_tokens == 0


class TestCostTrackerLocalModel:
    def test_unknown_model_is_free(self) -> None:
        tracker = CostTracker(model="my-local-llm")
        tracker.add_usage(input_tokens=100_000, output_tokens=50_000)
        assert tracker.total_cost_usd == 0.0

    def test_empty_model_is_free(self) -> None:
        tracker = CostTracker(model="")
        tracker.add_usage(input_tokens=1_000_000, output_tokens=1_000_000)
        assert tracker.total_cost_usd == 0.0

    def test_local_key_exact_match(self) -> None:
        tracker = CostTracker(model="local")
        tracker.add_usage(input_tokens=1_000_000, output_tokens=1_000_000)
        assert tracker.total_cost_usd == 0.0


class TestFormatCost:
    def test_format_cost_with_nonzero_cost(self) -> None:
        tracker = CostTracker(model="gpt-4o")
        tracker.add_usage(input_tokens=1000, output_tokens=500)
        result = tracker.format_cost()
        assert "in: 1,000" in result
        assert "out: 500" in result
        assert "Cost: $" in result
        assert "local model" not in result

    def test_format_cost_zero_cost(self) -> None:
        tracker = CostTracker(model="my-local-qwen")
        tracker.add_usage(input_tokens=1000, output_tokens=500)
        result = tracker.format_cost()
        assert "in: 1,000" in result
        assert "out: 500" in result
        assert "$0" in result

    def test_format_cost_zero_usage(self) -> None:
        tracker = CostTracker(model="gpt-4o")
        result = tracker.format_cost()
        assert "in: 0" in result
        assert "out: 0" in result
        # Zero usage means zero cost → shows local model message
        assert "$0" in result

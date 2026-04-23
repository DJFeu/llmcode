"""Tests for :mod:`llm_code.engine.policies.budget`."""
from __future__ import annotations

import pytest

from llm_code.engine.policies.budget import RetryBudget


class TestRetryBudget:
    def test_default_max(self):
        assert RetryBudget().max_total_retries == 20

    def test_negative_max_rejected(self):
        with pytest.raises(ValueError):
            RetryBudget(max_total_retries=-1)

    def test_zero_max_allows_no_retries(self):
        budget = RetryBudget(max_total_retries=0)
        assert budget.can_retry() is False

    def test_can_retry_while_under_max(self):
        budget = RetryBudget(max_total_retries=3)
        assert budget.can_retry() is True
        budget.consume()
        assert budget.can_retry() is True
        budget.consume()
        assert budget.can_retry() is True

    def test_consume_then_cannot_retry(self):
        budget = RetryBudget(max_total_retries=2)
        budget.consume()
        budget.consume()
        assert budget.can_retry() is False

    def test_consume_after_exhaustion_raises(self):
        budget = RetryBudget(max_total_retries=1)
        budget.consume()
        with pytest.raises(RuntimeError):
            budget.consume()

    def test_used_counter(self):
        budget = RetryBudget(max_total_retries=5)
        assert budget.used == 0
        budget.consume()
        assert budget.used == 1

    def test_remaining_counter(self):
        budget = RetryBudget(max_total_retries=5)
        assert budget.remaining == 5
        budget.consume()
        assert budget.remaining == 4

    def test_remaining_never_negative(self):
        # After reset scenarios used >= max can't happen; defensive check.
        budget = RetryBudget(max_total_retries=2)
        budget.consume()
        budget.consume()
        assert budget.remaining == 0

    def test_reset_zeroes_counter(self):
        budget = RetryBudget(max_total_retries=3)
        budget.consume()
        budget.consume()
        budget.reset()
        assert budget.used == 0
        assert budget.can_retry() is True

    def test_independent_instances(self):
        a = RetryBudget(max_total_retries=3)
        b = RetryBudget(max_total_retries=3)
        a.consume()
        assert a.used == 1
        assert b.used == 0

    def test_large_budget(self):
        budget = RetryBudget(max_total_retries=1_000)
        for _ in range(500):
            budget.consume()
        assert budget.used == 500
        assert budget.remaining == 500

    def test_prevents_infinite_loop_scenario(self):
        """Integration-ish: simulate adversarial retry+fallback ping-pong.

        Each pass consumes one budget unit; after max the loop must
        bail out even if policies keep saying 'retry'.
        """
        budget = RetryBudget(max_total_retries=10)
        passes = 0
        while budget.can_retry():
            budget.consume()
            passes += 1
            if passes > 1000:  # safety for test itself
                break
        assert passes == 10

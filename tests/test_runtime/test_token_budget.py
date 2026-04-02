"""Tests for TokenBudget."""
from __future__ import annotations


from llm_code.runtime.token_budget import TokenBudget


class TestTokenBudget:
    def test_initial_state(self) -> None:
        budget = TokenBudget(target=1000)
        assert budget.consumed == 0
        assert budget.remaining() == 1000

    def test_add(self) -> None:
        budget = TokenBudget(target=1000)
        budget.add(100)
        assert budget.consumed == 100
        assert budget.remaining() == 900

    def test_add_multiple(self) -> None:
        budget = TokenBudget(target=1000)
        budget.add(300)
        budget.add(200)
        assert budget.consumed == 500
        assert budget.remaining() == 500

    def test_should_nudge_when_under(self) -> None:
        budget = TokenBudget(target=1000)
        budget.add(500)
        assert budget.should_nudge() is True

    def test_should_nudge_false_when_at_target(self) -> None:
        budget = TokenBudget(target=1000)
        budget.add(1000)
        assert budget.should_nudge() is False

    def test_should_nudge_false_when_over(self) -> None:
        budget = TokenBudget(target=1000)
        budget.add(1500)
        assert budget.should_nudge() is False

    def test_is_exhausted_false_when_under(self) -> None:
        budget = TokenBudget(target=1000)
        budget.add(999)
        assert budget.is_exhausted() is False

    def test_is_exhausted_true_when_at_target(self) -> None:
        budget = TokenBudget(target=1000)
        budget.add(1000)
        assert budget.is_exhausted() is True

    def test_is_exhausted_true_when_over(self) -> None:
        budget = TokenBudget(target=1000)
        budget.add(1500)
        assert budget.is_exhausted() is True

    def test_nudge_message_contains_remaining(self) -> None:
        budget = TokenBudget(target=10000)
        budget.add(3000)
        msg = budget.nudge_message()
        assert "7,000" in msg
        assert "10,000" in msg

    def test_remaining_never_negative(self) -> None:
        budget = TokenBudget(target=100)
        budget.add(500)
        assert budget.remaining() == 0

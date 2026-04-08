"""Thinking budget must be capped so it cannot consume the entire output
token budget."""
from __future__ import annotations

from llm_code.runtime.conversation import _apply_thinking_budget_cap


def test_cap_respects_half_of_max_output_tokens() -> None:
    assert _apply_thinking_budget_cap(131072, max_output_tokens=8192) == 4096


def test_cap_leaves_small_budgets_alone() -> None:
    assert _apply_thinking_budget_cap(1000, max_output_tokens=8192) == 1000


def test_cap_has_minimum_floor() -> None:
    assert _apply_thinking_budget_cap(131072, max_output_tokens=512) == 1024


def test_cap_noop_when_max_unknown() -> None:
    assert _apply_thinking_budget_cap(131072, max_output_tokens=None) == 131072
    assert _apply_thinking_budget_cap(131072, max_output_tokens=0) == 131072

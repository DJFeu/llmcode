"""thinking_mode hook sets runtime._thinking_boost_active; the next turn must
consume it (boosted budget) and clear it after."""
from __future__ import annotations

from types import SimpleNamespace

from llm_code.runtime.conversation import _apply_thinking_boost


def test_apply_boost_doubles_budget_when_flag_set() -> None:
    rt = SimpleNamespace(_thinking_boost_active=True)
    out = _apply_thinking_boost(rt, base_budget=10000)
    assert out == 20000


def test_apply_boost_caps_at_provider_max() -> None:
    rt = SimpleNamespace(_thinking_boost_active=True)
    out = _apply_thinking_boost(rt, base_budget=200000, max_budget=131072)
    assert out == 131072


def test_apply_boost_returns_base_when_flag_unset() -> None:
    rt = SimpleNamespace(_thinking_boost_active=False)
    out = _apply_thinking_boost(rt, base_budget=10000)
    assert out == 10000


def test_apply_boost_clears_flag_after_consuming() -> None:
    rt = SimpleNamespace(_thinking_boost_active=True)
    _apply_thinking_boost(rt, base_budget=10000)
    assert rt._thinking_boost_active is False


def test_apply_boost_no_attribute_falls_through() -> None:
    rt = SimpleNamespace()
    out = _apply_thinking_boost(rt, base_budget=10000)
    assert out == 10000

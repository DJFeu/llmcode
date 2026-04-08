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


def test_build_thinking_extra_body_caps_via_max_output_tokens_kwarg() -> None:
    """The conversation runner passes _current_max_tokens (the actual request
    max_tokens) into build_thinking_extra_body. Verify the cap is honored
    end-to-end through the public function — not just the helper."""
    from types import SimpleNamespace

    from llm_code.runtime.conversation import build_thinking_extra_body

    thinking_cfg = SimpleNamespace(mode="enabled", budget_tokens=131072)
    out = build_thinking_extra_body(
        thinking_cfg,
        is_local=True,
        provider_supports_reasoning=True,
        runtime=None,
        max_output_tokens=8192,
    )
    assert out is not None
    assert out["chat_template_kwargs"]["thinking_budget"] == 4096


def test_build_thinking_extra_body_no_cap_when_max_omitted() -> None:
    """If the runner forgets to pass max_output_tokens (regression of the
    original hotfix bug where bad attribute names produced None), the budget
    is unbounded — at least we can detect that this happened."""
    from types import SimpleNamespace

    from llm_code.runtime.conversation import build_thinking_extra_body

    thinking_cfg = SimpleNamespace(mode="enabled", budget_tokens=131072)
    out = build_thinking_extra_body(
        thinking_cfg,
        is_local=True,
        provider_supports_reasoning=True,
        runtime=None,
    )
    assert out is not None
    # Without a cap the local-mode max-clamp at 131072 is the only ceiling
    assert out["chat_template_kwargs"]["thinking_budget"] == 131072

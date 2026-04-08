"""Wave2-2: Cost tracker delta fixes.

Three narrow gaps found in the audit of the existing cost_tracker.py:

1. The TUI hook on StreamMessageStop was only passing input/output
   tokens, silently dropping cache_read / cache_creation buckets.
2. Unknown model names fell through to "free" with zero signal,
   masking config mistakes on paid models.
3. (Plus: TokenUsage now carries cache buckets end-to-end from the
   provider parser, so downstream consumers actually get them.)

The tests here pin each fix at the unit level. Full TUI integration
is already covered by the existing test_conversation* suites.
"""
from __future__ import annotations

import logging

import pytest

from llm_code.api.openai_compat import _token_usage_from_dict
from llm_code.api.types import TokenUsage
from llm_code.runtime.cost_tracker import CostTracker


# ---------- Fix: TokenUsage carries cache buckets ----------

def test_token_usage_defaults_cache_to_zero_for_backcompat() -> None:
    """Existing call sites that construct TokenUsage with only
    input/output must keep working — the new fields default to 0."""
    usage = TokenUsage(input_tokens=100, output_tokens=50)
    assert usage.cache_read_tokens == 0
    assert usage.cache_creation_tokens == 0


def test_token_usage_from_dict_openai_shape() -> None:
    """OpenAI-compat servers report cache reads nested under
    prompt_tokens_details.cached_tokens — the helper must pick them up."""
    usage = _token_usage_from_dict({
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "prompt_tokens_details": {"cached_tokens": 800},
    })
    assert usage.input_tokens == 1000
    assert usage.output_tokens == 500
    assert usage.cache_read_tokens == 800
    assert usage.cache_creation_tokens == 0


def test_token_usage_from_dict_anthropic_shape() -> None:
    """Anthropic surfaces cache fields top-level with explicit names."""
    usage = _token_usage_from_dict({
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 200,
        "cache_creation_input_tokens": 100,
    })
    assert usage.input_tokens == 1000
    assert usage.output_tokens == 500
    assert usage.cache_read_tokens == 200
    assert usage.cache_creation_tokens == 100


def test_token_usage_from_dict_anthropic_overrides_openai_nested() -> None:
    """If both shapes appear in the same dict (weird proxy servers),
    the explicit Anthropic field wins over the nested OpenAI one."""
    usage = _token_usage_from_dict({
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "prompt_tokens_details": {"cached_tokens": 123},
        "cache_read_input_tokens": 999,
    })
    assert usage.cache_read_tokens == 999


def test_token_usage_from_dict_missing_fields() -> None:
    """Provider may omit usage entirely — everything defaults to 0."""
    usage = _token_usage_from_dict({})
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.cache_read_tokens == 0
    assert usage.cache_creation_tokens == 0


# ---------- Fix: unknown model warns exactly once ----------

def test_unknown_model_warns_on_first_add_usage(caplog: pytest.LogCaptureFixture) -> None:
    tracker = CostTracker(model="definitely-not-a-real-model-xyz")
    with caplog.at_level(logging.WARNING, logger="llm_code.runtime.cost_tracker"):
        tracker.add_usage(input_tokens=100, output_tokens=50)
    warnings = [r for r in caplog.records if "no pricing entry" in r.message]
    assert len(warnings) == 1
    assert "definitely-not-a-real-model-xyz" in warnings[0].message


def test_unknown_model_warns_only_once_per_model(caplog: pytest.LogCaptureFixture) -> None:
    """Spamming the log on every token event would drown real warnings."""
    tracker = CostTracker(model="mystery-model")
    with caplog.at_level(logging.WARNING, logger="llm_code.runtime.cost_tracker"):
        for _ in range(5):
            tracker.add_usage(input_tokens=100, output_tokens=50)
    warnings = [r for r in caplog.records if "no pricing entry" in r.message]
    assert len(warnings) == 1


def test_unknown_model_warns_again_after_switch(caplog: pytest.LogCaptureFixture) -> None:
    """Switching to a *different* unknown model should warn once more
    so the user sees both names."""
    tracker = CostTracker(model="mystery-a")
    with caplog.at_level(logging.WARNING, logger="llm_code.runtime.cost_tracker"):
        tracker.add_usage(input_tokens=100, output_tokens=50)
        tracker.model = "mystery-b"
        tracker.add_usage(input_tokens=100, output_tokens=50)
    warnings = [r for r in caplog.records if "no pricing entry" in r.message]
    assert len(warnings) == 2


def test_known_model_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    tracker = CostTracker(model="claude-sonnet-4-6")
    with caplog.at_level(logging.WARNING, logger="llm_code.runtime.cost_tracker"):
        tracker.add_usage(input_tokens=100, output_tokens=50)
    warnings = [r for r in caplog.records if "no pricing entry" in r.message]
    assert warnings == []


def test_empty_model_name_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    """An uninitialized tracker (model='') shouldn't spam warnings
    before the real model name is assigned."""
    tracker = CostTracker(model="")
    with caplog.at_level(logging.WARNING, logger="llm_code.runtime.cost_tracker"):
        tracker.add_usage(input_tokens=100, output_tokens=50)
    warnings = [r for r in caplog.records if "no pricing entry" in r.message]
    assert warnings == []


# ---------- End-to-end: cache tokens reach the tracker ----------

def test_cache_tokens_priced_correctly_for_known_model() -> None:
    """Known model + explicit cache buckets → the existing 10% / 125%
    pricing logic in add_usage does the right thing. This would have
    been dead code before Wave2-2 because the TUI hook never passed
    the cache buckets in."""
    # claude-sonnet-4-6: input=3, output=15 per 1M
    tracker = CostTracker(model="claude-sonnet-4-6")
    cost = tracker.add_usage(
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=1_000_000,  # 1M * 3 * 0.10 = 0.30
        cache_creation_tokens=1_000_000,  # 1M * 3 * 1.25 = 3.75
    )
    assert cost == pytest.approx(0.30 + 3.75)

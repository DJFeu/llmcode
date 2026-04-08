"""Wave2-3: Model fallback quick-win fixes.

Two narrow fixes to the existing fallback path in ``conversation.py``:

1. Non-retryable provider errors (401/400/model-not-found) must propagate
   immediately instead of burning the 3-strike retry budget.
2. When we do switch to the fallback model, ``cost_tracker.model`` must
   follow so post-switch token usage is attributed to the correct model.

These tests deliberately target the decision logic with minimal
test-doubles rather than spinning up a full Runtime, which keeps them
fast and independent of unrelated conversation.py diagnostics.
"""
from __future__ import annotations

import pytest

from llm_code.api.errors import (
    ProviderAuthError,
    ProviderModelNotFoundError,
    ProviderOverloadError,
    ProviderRateLimitError,
)
from llm_code.runtime.cost_tracker import CostTracker


# ---------- Fix 1: is_retryable contract ----------

@pytest.mark.parametrize(
    "exc",
    [
        ProviderAuthError("bad key"),
        ProviderModelNotFoundError("no such model"),
    ],
)
def test_non_retryable_errors_advertise_is_retryable_false(exc: Exception) -> None:
    """Errors that must not burn the fallback budget expose is_retryable=False.

    The conversation loop's fallback block checks this flag before counting
    the error toward the 3-strike limit. If this contract ever regresses,
    the fix in conversation.py silently becomes a no-op.
    """
    assert getattr(exc, "is_retryable", None) is False


@pytest.mark.parametrize(
    "exc",
    [
        ProviderRateLimitError("429"),
        ProviderOverloadError("503"),
    ],
)
def test_retryable_errors_still_retry(exc: Exception) -> None:
    """Transient errors keep is_retryable=True so the fallback path runs."""
    assert getattr(exc, "is_retryable", None) is True


def test_plain_exception_defaults_to_retryable() -> None:
    """The conversation.py guard uses ``is False`` (not falsy), so bare
    Exception instances without the attribute stay on the retry path —
    otherwise provider timeouts wrapped as plain exceptions would bypass
    fallback entirely."""
    exc = RuntimeError("network blip")
    assert getattr(exc, "is_retryable", None) is not False


# ---------- Fix 2: cost_tracker.model follows fallback switch ----------


def test_cost_tracker_model_is_writable() -> None:
    """CostTracker is a plain dataclass; conversation.py relies on being
    able to reassign ``.model`` in place when a fallback switch happens."""
    tracker = CostTracker(model="gpt-5.4")
    tracker.model = "gpt-5.4-fallback"
    assert tracker.model == "gpt-5.4-fallback"


def test_cost_tracker_usage_after_model_switch_uses_new_pricing() -> None:
    """After the switch, add_usage must look up the new model's pricing,
    not cache the original. This is what makes the Fix 2 assignment
    meaningful: without it, every token after a fallback would still be
    priced as the failed primary model."""
    # custom_pricing uses [input_per_1m, output_per_1m] list form.
    tracker = CostTracker(
        model="__wave2_test_primary__",
        custom_pricing={
            "__wave2_test_primary__": [100.0, 200.0],
            "__wave2_test_fallback__": [1.0, 2.0],
        },
    )
    primary_cost = tracker.add_usage(input_tokens=1_000_000, output_tokens=0)
    assert primary_cost == pytest.approx(100.0)

    # Fallback switch: the conversation loop does exactly this assignment.
    tracker.model = "__wave2_test_fallback__"
    fallback_cost = tracker.add_usage(input_tokens=1_000_000, output_tokens=0)
    assert fallback_cost == pytest.approx(1.0)

    # And the running total reflects both correctly.
    assert tracker.total_cost_usd == pytest.approx(101.0)

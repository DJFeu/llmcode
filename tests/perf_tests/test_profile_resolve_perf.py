"""Perf baseline: ``get_profile`` must stay fast.

``get_profile`` is called on the hot path for every model pick, so a
regression here propagates everywhere. This test pins the ~1000-call
batch against ``baselines.json`` with a generous tolerance — exact
numbers depend on the machine, but order-of-magnitude drift is
meaningful.
"""
from __future__ import annotations

from pathlib import Path

from llm_code.perf import BaselineStore, PerfHarness
from llm_code.runtime.model_profile import get_profile

_BASELINES = Path(__file__).with_name("baselines.json")

# Generous tolerance — perf baselines travel badly across machines.
# Nightly CI pins its own baseline via UPDATE_BASELINES=1.
ALLOWED_DELTA = 50.0


def test_profile_resolve_1000_calls() -> None:
    models = (
        "claude-sonnet-4-6",
        "qwen3-coder-7b",
        "qwen3.6-plus",
        "gpt-4o",
        "deepseek-r1",
    )

    def bench() -> None:
        for _ in range(200):
            for m in models:
                get_profile(m)

    harness = PerfHarness(baseline_store=BaselineStore(_BASELINES))
    result = harness.measure(bench, samples=5, warmup=1)
    cmp = harness.compare_or_update(
        label="profile_resolve_1000_calls_median_s",
        measurement=result.median,
        allowed_delta_pct=ALLOWED_DELTA,
    )
    assert cmp.passed, cmp.reason


def test_provider_registry_lookup() -> None:
    """Registry get() must stay O(1) — regressions here would flag a
    move to a linear-scan implementation."""
    from llm_code.api.provider_registry import get_registry

    reg = get_registry()

    def bench() -> None:
        for _ in range(5000):
            reg.get("anthropic")
            reg.get("openai-compat")

    harness = PerfHarness(baseline_store=BaselineStore(_BASELINES))
    result = harness.measure(bench, samples=5, warmup=1)
    cmp = harness.compare_or_update(
        label="registry_get_10k_calls_median_s",
        measurement=result.median,
        allowed_delta_pct=ALLOWED_DELTA,
    )
    assert cmp.passed, cmp.reason

"""Tests for diminishing returns detection in conversation turn loop."""
from __future__ import annotations

from llm_code.runtime.config import DiminishingReturnsConfig


class TestDiminishingReturnsConfig:
    def test_default_values(self):
        cfg = DiminishingReturnsConfig()
        assert cfg.enabled is True
        assert cfg.min_continuations == 3
        assert cfg.min_delta_tokens == 500

    def test_custom_values(self):
        cfg = DiminishingReturnsConfig(
            enabled=False,
            min_continuations=5,
            min_delta_tokens=1000,
        )
        assert cfg.enabled is False
        assert cfg.min_continuations == 5
        assert cfg.min_delta_tokens == 1000

    def test_frozen(self):
        cfg = DiminishingReturnsConfig()
        try:
            cfg.enabled = False  # type: ignore
            assert False, "Should raise FrozenInstanceError"
        except AttributeError:
            pass


class TestDiminishingReturnsLogic:
    """Test the diminishing returns detection logic in isolation."""

    def test_triggers_after_min_continuations_with_low_delta(self):
        cfg = DiminishingReturnsConfig(min_continuations=3, min_delta_tokens=500)
        prev_tokens = 0
        continuation_count = 0
        triggered = False

        # Simulate 4 iterations with decreasing deltas
        deltas = [2000, 1500, 800, 200]  # 4th iteration has delta 200 < 500
        for delta in deltas:
            current = prev_tokens + delta
            continuation_count += 1
            if (
                continuation_count >= cfg.min_continuations
                and delta < cfg.min_delta_tokens
            ):
                triggered = True
                break
            prev_tokens = current

        assert triggered
        assert continuation_count == 4

    def test_does_not_trigger_with_high_delta(self):
        cfg = DiminishingReturnsConfig(min_continuations=3, min_delta_tokens=500)
        continuation_count = 0
        triggered = False

        deltas = [2000, 1500, 800, 600]  # all above 500
        for delta in deltas:
            continuation_count += 1
            if (
                continuation_count >= cfg.min_continuations
                and delta < cfg.min_delta_tokens
            ):
                triggered = True
                break

        assert not triggered

    def test_does_not_trigger_before_min_continuations(self):
        cfg = DiminishingReturnsConfig(min_continuations=3, min_delta_tokens=500)
        continuation_count = 0
        triggered = False

        deltas = [100, 100]  # low deltas but only 2 iterations
        for delta in deltas:
            continuation_count += 1
            if (
                continuation_count >= cfg.min_continuations
                and delta < cfg.min_delta_tokens
            ):
                triggered = True
                break

        assert not triggered
        assert continuation_count == 2

    def test_disabled_config(self):
        cfg = DiminishingReturnsConfig(enabled=False)
        # When disabled, never check
        assert cfg.enabled is False

    def test_zero_delta_triggers(self):
        cfg = DiminishingReturnsConfig(min_continuations=2, min_delta_tokens=100)
        continuation_count = 0
        triggered = False

        deltas = [500, 300, 0]  # 3rd has zero delta
        for delta in deltas:
            continuation_count += 1
            if (
                continuation_count >= cfg.min_continuations
                and delta < cfg.min_delta_tokens
            ):
                triggered = True
                break

        assert triggered
        assert continuation_count == 3

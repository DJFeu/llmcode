"""Behavioral tests for diminishing returns detection in the turn loop.

These tests simulate the relevant counter/branch logic in isolation
(matching the structure used in conversation.py) so they exercise the
public DiminishingReturnsConfig contract without requiring a full
provider mock.
"""
from __future__ import annotations

import pytest

from llm_code.runtime.config import DiminishingReturnsConfig


def _simulate(cfg: DiminishingReturnsConfig, events):
    """Replay a sequence of (kind, delta) events through the DR logic.

    kind: "text" or "tool"
    delta: int (only meaningful for "text")
    Returns (triggered, final_count, message_or_None).
    """
    count = 0
    triggered = False
    message = None
    for kind, delta in events:
        if kind == "tool":
            count = 0
            continue
        if not cfg.enabled:
            continue
        count += 1
        if count >= cfg.min_continuations and delta < cfg.min_delta_tokens:
            triggered = True
            template = getattr(cfg, "auto_stop_message", "")
            try:
                message = template.format(iteration=count, delta=delta)
            except Exception:
                message = template
            break
    return triggered, count, message


def test_three_low_delta_continuations_trigger():
    cfg = DiminishingReturnsConfig(min_continuations=3, min_delta_tokens=500)
    triggered, count, _ = _simulate(
        cfg, [("text", 100), ("text", 100), ("text", 100)]
    )
    assert triggered
    assert count == 3


def test_tool_call_resets_counter():
    cfg = DiminishingReturnsConfig(min_continuations=3, min_delta_tokens=500)
    triggered, count, _ = _simulate(
        cfg,
        [
            ("text", 100),
            ("text", 100),
            ("tool", 0),     # resets
            ("text", 100),
            ("text", 100),   # only 2 since reset
        ],
    )
    assert not triggered
    assert count == 2


def test_disabled_flag_respected():
    cfg = DiminishingReturnsConfig(enabled=False)
    triggered, count, _ = _simulate(
        cfg, [("text", 0), ("text", 0), ("text", 0), ("text", 0)]
    )
    assert not triggered
    assert count == 0


def test_high_delta_does_not_trigger():
    cfg = DiminishingReturnsConfig(min_continuations=3, min_delta_tokens=500)
    triggered, _, _ = _simulate(
        cfg, [("text", 800), ("text", 600), ("text", 700), ("text", 500)]
    )
    # delta == 500 is not strictly less than 500
    assert not triggered


@pytest.mark.parametrize("min_continuations", [2, 3, 4])
def test_parametrized_min_continuations(min_continuations):
    cfg = DiminishingReturnsConfig(
        min_continuations=min_continuations, min_delta_tokens=500
    )
    events = [("text", 50)] * (min_continuations + 1)
    triggered, count, _ = _simulate(cfg, events)
    assert triggered
    assert count == min_continuations


def test_auto_stop_message_is_formatted():
    cfg = DiminishingReturnsConfig(
        min_continuations=2,
        min_delta_tokens=500,
        auto_stop_message="STOP iter={iteration} delta={delta}",
    )
    triggered, _, msg = _simulate(cfg, [("text", 10), ("text", 20)])
    assert triggered
    assert msg == "STOP iter=2 delta=20"


def test_auto_stop_message_default_contains_iteration_token():
    cfg = DiminishingReturnsConfig()
    assert "{iteration}" in cfg.auto_stop_message
    assert "{delta}" in cfg.auto_stop_message

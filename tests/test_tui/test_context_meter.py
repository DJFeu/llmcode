"""Tests for context window meter (StatusBar) and color thresholds."""
from __future__ import annotations

import pytest

from llm_code.tui.status_bar import StatusBar, context_meter_style


@pytest.mark.unit
class TestContextMeterStyle:
    def test_dim_below_60(self) -> None:
        assert context_meter_style(0) == "dim"
        assert context_meter_style(59.9) == "dim"

    def test_yellow_60_to_80(self) -> None:
        assert context_meter_style(60) == "yellow"
        assert context_meter_style(79.9) == "yellow"

    def test_orange_80_to_95(self) -> None:
        assert context_meter_style(80) == "#ff8800"
        assert context_meter_style(94.9) == "#ff8800"

    def test_red_above_95(self) -> None:
        assert context_meter_style(95) == "bold red"
        assert context_meter_style(100) == "bold red"


@pytest.mark.unit
class TestStatusBarContextPct:
    def test_returns_zero_when_limit_unset(self) -> None:
        bar = StatusBar()
        bar.context_used = 1000
        bar.context_limit = 0
        assert bar.context_pct() == 0.0

    def test_computes_percentage(self) -> None:
        bar = StatusBar()
        bar.context_limit = 1000
        bar.context_used = 250
        assert bar.context_pct() == 25.0

    def test_caps_at_100(self) -> None:
        bar = StatusBar()
        bar.context_limit = 100
        bar.context_used = 999
        assert bar.context_pct() == 100.0

    def test_format_includes_ctx_segment(self) -> None:
        bar = StatusBar()
        bar.context_limit = 1000
        bar.context_used = 470
        out = bar._format_content()
        assert "ctx 47%" in out

    def test_format_omits_ctx_when_no_limit(self) -> None:
        bar = StatusBar()
        bar.context_limit = 0
        out = bar._format_content()
        assert "ctx" not in out

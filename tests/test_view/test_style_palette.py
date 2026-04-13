"""Tests for the M15 style palette (Task A1)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from llm_code.view.repl import style
from llm_code.view.repl.style import (
    context_color,
    default_palette,
    hyperlink,
    load_palette,
    shimmer_color,
    shimmer_phase_for_time,
)


@dataclass
class _FakeTheme:
    overrides: dict = field(default_factory=dict)


@dataclass
class _FakeConfig:
    theme: _FakeTheme = field(default_factory=_FakeTheme)


def test_default_palette_uses_tech_blue_tones() -> None:
    p = default_palette()
    assert p.llmcode_blue_mid == style.LLMCODE_BLUE_MID
    assert p.llmcode_blue_hilite == style.LLMCODE_BLUE_HILITE
    assert p.assistant_bullet == style.LLMCODE_BLUE_MID
    assert p.brand_accent == style.LLMCODE_BLUE_MID


def test_every_slot_has_nonempty_default() -> None:
    """Every slot in the semantic color map must have a non-empty default."""
    p = default_palette()
    for slot in p.slot_names():
        value = getattr(p, slot)
        assert value not in (None, ""), f"slot {slot!r} has empty default"


def test_load_palette_with_none_returns_default() -> None:
    assert load_palette(None) == default_palette()


def test_load_palette_ignores_config_without_theme() -> None:
    class _Cfg:
        pass

    assert load_palette(_Cfg()) == default_palette()


def test_load_palette_applies_theme_overrides() -> None:
    cfg = _FakeConfig(
        theme=_FakeTheme(
            overrides={
                "assistant_bullet": "magenta",
                "diff_add_fg": "#00ff00",
                "mode_bash_fg": "cyan",
            }
        )
    )
    p = load_palette(cfg)
    assert p.assistant_bullet == "magenta"
    assert p.diff_add_fg == "#00ff00"
    assert p.mode_bash_fg == "cyan"
    # Untouched slot keeps its default
    assert p.assistant_fg == default_palette().assistant_fg


def test_load_palette_drops_unknown_keys() -> None:
    cfg = _FakeConfig(theme=_FakeTheme(overrides={"nonexistent_slot": "red"}))
    p = load_palette(cfg)
    assert p == default_palette()


def test_load_palette_handles_non_dict_overrides() -> None:
    cfg = _FakeConfig(theme=_FakeTheme(overrides="not a dict"))  # type: ignore
    assert load_palette(cfg) == default_palette()


def test_set_palette_mutates_singleton() -> None:
    original = style.palette
    try:
        custom = default_palette().__class__(assistant_bullet="red")
        style.set_palette(custom)
        assert style.palette.assistant_bullet == "red"
    finally:
        style.set_palette(original)


def test_shimmer_color_phase_bounds() -> None:
    """Shimmer phase 0.0 returns the deepest stop, 1.0 the hilite."""
    low = shimmer_color(0.0)
    high = shimmer_color(1.0)
    assert low.startswith("#")
    assert high.startswith("#")
    assert low != high


def test_shimmer_color_is_deterministic() -> None:
    assert shimmer_color(0.5) == shimmer_color(0.5)


def test_shimmer_color_wraps_negative_phase() -> None:
    # Negative phases should not raise; they wrap.
    shimmer_color(-0.25)
    shimmer_color(-1.5)


def test_shimmer_phase_is_triangle_wave() -> None:
    # Triangle wave: phase(0) == 0, phase(period/2) ~ 1, phase(period) ~ 0.
    assert shimmer_phase_for_time(0.0, period=2.0) == pytest.approx(0.0)
    assert shimmer_phase_for_time(1.0, period=2.0) == pytest.approx(1.0)
    assert shimmer_phase_for_time(2.0, period=2.0) == pytest.approx(0.0)


def test_shimmer_phase_zero_period() -> None:
    assert shimmer_phase_for_time(1.0, period=0.0) == 0.0


def test_context_color_thresholds() -> None:
    assert context_color(0.0) == style.palette.status_success
    assert context_color(0.59) == style.palette.status_success
    assert context_color(0.60) == style.palette.status_warning
    assert context_color(0.79) == style.palette.status_warning
    assert context_color(0.80) == style.palette.status_error
    assert context_color(1.0) == style.palette.status_error


def test_hyperlink_envelope() -> None:
    wrapped = hyperlink("llmcode", "https://example.com/a")
    assert wrapped.startswith("\x1b]8;;https://example.com/a\x1b\\")
    assert wrapped.endswith("\x1b]8;;\x1b\\")
    assert "llmcode" in wrapped


def test_agent_palette_has_six_distinct_tones() -> None:
    p = default_palette()
    assert len(p.agent_palette) == 6
    assert len(set(p.agent_palette)) == 6


def test_palette_is_frozen() -> None:
    p = default_palette()
    with pytest.raises(Exception):
        p.assistant_bullet = "red"  # type: ignore[misc]

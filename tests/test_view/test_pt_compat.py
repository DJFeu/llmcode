"""Regression guard: every palette slot parses as a valid PT style.

The M15 palette is consumed in two places:

1. Rich Text/Panel renderers, which accept any Rich-compatible
   style token (including ``"dim"``, ``"italic"``, named colors).
2. prompt_toolkit ``FormattedText`` style strings — which have a
   stricter grammar: ``fg:<color>`` and ``bg:<color>`` require
   an actual color (hex or ANSI name), and bare modifiers like
   ``"dim"`` are NOT recognized as colors.

A first-pass M15 build used ``"dim"`` as the default for
``hint_fg``/``status_dim``/``tool_args_fg``, which worked fine in
Rich but crashed prompt_toolkit with::

    ValueError: Wrong color format 'dim'

This test guards against regressions by asserting every slot in
the default palette can be inlined into an ``fg:`` style string
and parsed by ``prompt_toolkit.styles.Style.from_dict``.
"""
from __future__ import annotations

from dataclasses import fields

import pytest
from prompt_toolkit.styles import Style

from llm_code.view.repl import style


def _simple_color_slots() -> list[str]:
    """Slots that are used directly in ``fg:<slot>`` PT contexts.

    These must be pure color tokens (hex or ANSI named), not
    compound modifier+color strings.
    """
    # Slots whose values are consumed as PT ``fg:`` arguments.
    # Compound style slots (e.g. ``markdown_heading = "bold #..."``)
    # are Rich-only and excluded.
    pt_consumed = {
        "assistant_fg",
        "assistant_bullet",
        "user_fg",
        "user_prefix",
        "system_fg",
        "thinking_fg",
        "thinking_header_fg",
        "tool_args_fg",
        "tool_elapsed_fg",
        "file_path_fg",
        "command_fg",
        "command_alias_fg",
        "diff_hunk_fg",
        "diff_lineno_fg",
        "token_count_fg",
        "status_success",
        "status_warning",
        "status_error",
        "status_info",
        "status_dim",
        "mode_plan_fg",
        "mode_yolo_fg",
        "mode_bash_fg",
        "mode_vim_fg",
        "hint_fg",
        "pasted_marker_fg",
        "brand_accent",
        "brand_muted",
        "llmcode_blue_deep",
        "llmcode_blue_dark",
        "llmcode_blue_mid",
        "llmcode_blue_light",
        "llmcode_blue_hilite",
        "logo_shadow_fg",
    }
    slot_names = {f.name for f in fields(style.BrandPalette)}
    return sorted(pt_consumed & slot_names)


@pytest.mark.parametrize("slot", _simple_color_slots())
def test_pt_style_parses_fg_with_slot(slot: str) -> None:
    """Every PT-consumed slot must be valid as ``fg:<slot>``."""
    value = getattr(style.default_palette(), slot)
    # Construct a realistic PT style string.
    style_str = f"fg:{value}"
    Style.from_dict({"test": style_str})


@pytest.mark.parametrize("slot", _simple_color_slots())
def test_pt_style_parses_fg_with_slot_plus_modifier(slot: str) -> None:
    """``fg:<slot> bold`` must also parse cleanly."""
    value = getattr(style.default_palette(), slot)
    Style.from_dict({"test": f"fg:{value} bold"})
    Style.from_dict({"test": f"fg:{value} italic"})


def test_footer_hint_renders_parseable_styles() -> None:
    from llm_code.view.repl.components.footer_hint import FooterHint

    for style_str, _ in FooterHint().render():
        if style_str:
            Style.from_dict({"t": style_str})


def test_context_meter_renders_parseable_styles() -> None:
    from llm_code.view.repl.components.context_meter import render_context_meter

    for fill in (0, 500, 800, 1000):
        for style_str, _ in render_context_meter(fill, 1000):
            if style_str:
                Style.from_dict({"t": style_str})


def test_mode_indicator_renders_parseable_styles() -> None:
    from llm_code.view.repl.components.mode_indicator import ModeIndicator

    for mode in ("prompt", "plan", "yolo", "bash", "vim", "unknown"):
        mi = ModeIndicator()
        mi.set_mode(mode)
        for style_str, _ in mi.render():
            if style_str:
                Style.from_dict({"t": style_str})


def test_shimmer_text_renders_parseable_styles() -> None:
    from llm_code.view.repl.components.shimmer import shimmer_text

    for style_str, _ in shimmer_text("llmcode", now=0.5):
        if style_str:
            Style.from_dict({"t": style_str})

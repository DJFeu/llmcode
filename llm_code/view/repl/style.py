"""Central brand palette + semantic color map for the v2.0.0 REPL.

M15 Task A1 deliverable.

Every visible text fragment in the REPL must read its color from
one of the slots on :class:`BrandPalette` — never from a bare
color literal at the call site. The grep-gated invariant test
``tests/test_view/test_no_bare_colors.py`` enforces this rule.

Usage::

    from llm_code.view.repl.style import palette
    console.print("hello", style=palette.assistant_fg)

The default palette uses llmcode's tech-blue brand tones. A user
can override any slot via their runtime config's ``theme``
section — see :func:`load_palette`.

The module exposes a singleton ``palette`` that is:

1. Populated with M15 defaults at import time, so modules that
   import at startup have a working palette.
2. Rebuilt by ``cli.main._run_repl`` via :func:`set_palette`
   after runtime config resolves, so user theme overrides take
   effect before the welcome banner prints.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields, replace
from typing import Any

__all__ = [
    # brand stops
    "LLMCODE_BLUE_DEEP",
    "LLMCODE_BLUE_DARK",
    "LLMCODE_BLUE_MID",
    "LLMCODE_BLUE_LIGHT",
    "LLMCODE_BLUE_HILITE",
    # palette object + factory
    "BrandPalette",
    "default_palette",
    "load_palette",
    "set_palette",
    "palette",
    # shimmer helpers
    "shimmer_color",
    "shimmer_phase_for_time",
    "context_color",
    # OSC8
    "hyperlink",
    # icon primitives
    "ICON_SUCCESS",
    "ICON_FAILURE",
    "ICON_START",
    "ICON_WARNING",
    "ICON_INFO",
    "ICON_BULLET",
    "ICON_DOT",
]

# -----------------------------------------------------------------
# Tech-blue brand gradient stops.
#
# These are the five reference points of the llmcode brand ramp.
# They are consumed by :func:`shimmer_color` (shimmer keyframes),
# by :class:`BrandPalette` (default slot values), and by the
# logo renderer (per-row gradient shader).
# -----------------------------------------------------------------

LLMCODE_BLUE_DEEP = "#0b2a5e"
LLMCODE_BLUE_DARK = "#0b4fae"
LLMCODE_BLUE_MID = "#1e7ce8"
LLMCODE_BLUE_LIGHT = "#4aa8ff"
LLMCODE_BLUE_HILITE = "#b4e1ff"

# -----------------------------------------------------------------
# Icon primitives — single source of truth for REPL glyphs.
# -----------------------------------------------------------------

ICON_SUCCESS = "✓"
ICON_FAILURE = "✗"
ICON_START = "▶"
ICON_WARNING = "⚠"
ICON_INFO = "ℹ"
ICON_BULLET = "●"
ICON_DOT = "·"


@dataclass(frozen=True)
class BrandPalette:
    """Full semantic color map for the v2.0.0 REPL.

    Slot names correspond one-to-one with the table in the M15 plan.
    Every visible text fragment routes through one of these slots,
    so a user theme override re-tints the entire REPL in one shot.

    The defaults use llmcode's tech-blue tones plus ANSI named
    colors where they read more naturally across terminal themes.
    """

    # --- message bodies ---
    #
    # Colors matched to Claude Code's dark theme (from sourcemap).
    # Use hex throughout so every slot is valid in BOTH Rich Text
    # styles AND prompt_toolkit ``fg:`` tokens.
    assistant_fg: str = "#ffffff"                   # rgb(255,255,255) — pure white
    assistant_bullet: str = LLMCODE_BLUE_MID        # brand accent dot
    user_fg: str = "#ffffff"
    user_prefix: str = LLMCODE_BLUE_LIGHT
    system_fg: str = "#999999"                      # Claude Code "inactive" token
    thinking_fg: str = "#999999"
    thinking_header_fg: str = LLMCODE_BLUE_LIGHT

    # --- tool events ---
    #
    # Claude Code uses `⎿` (U+23BF) hook for indented tool progress,
    # dimColor for args/elapsed. Tool name uses terminal default (bold).
    tool_name_fg: str = "#ffffff"                   # white bold (like CC)
    tool_args_fg: str = "#505050"                   # Claude Code "subtle"
    tool_ok_fg: str = "#4eba65"                     # Claude Code "success" rgb(78,186,101)
    tool_fail_fg: str = "#ff6b80"                   # Claude Code "error" rgb(255,107,128)
    tool_start_fg: str = "#505050"                  # subtle dim start marker
    tool_elapsed_fg: str = "#505050"
    tool_progress_hook: str = "#505050"             # ⎿ hook character color

    # --- file paths + commands ---
    file_path_fg: str = LLMCODE_BLUE_LIGHT
    command_fg: str = LLMCODE_BLUE_MID
    command_alias_fg: str = "#999999"

    # --- bash mode ---
    bash_cmd_fg: str = "#4eba65"                    # success green
    bash_out_fg: str = "#ffffff"
    bash_err_fg: str = "#ff6b80"                    # error red
    bash_border_fg: str = "#fd5db1"                 # Claude Code "bashBorder" rgb(253,93,177)

    # --- diff ---
    diff_add_bg: str = "#225c2b"                    # Claude Code "diffAdded" rgb(34,92,43)
    diff_add_fg: str = "#4eba65"
    diff_del_bg: str = "#7a2936"                    # Claude Code "diffRemoved" rgb(122,41,54)
    diff_del_fg: str = "#ff6b80"
    diff_hunk_fg: str = "#999999"
    diff_lineno_fg: str = "#505050"

    # --- markdown (Claude Code inline styles) ---
    markdown_heading: str = "#ffffff"               # bold (H1 also italic+underline)
    markdown_code_inline: str = "#b1b9f9"           # Claude Code "permission" rgb(177,185,249)
    markdown_link: str = LLMCODE_BLUE_LIGHT
    markdown_quote_fg: str = "#999999"              # dim + italic

    # --- status line ---
    token_count_fg: str = LLMCODE_BLUE_LIGHT

    # --- generic status aliases (Claude Code dark theme) ---
    status_success: str = "#4eba65"                 # rgb(78,186,101)
    status_warning: str = "#ffc107"                 # rgb(255,193,7)
    status_error: str = "#ff6b80"                   # rgb(255,107,128)
    status_info: str = LLMCODE_BLUE_MID
    status_dim: str = "#505050"                     # rgb(80,80,80) "subtle"

    # --- mode indicators ---
    mode_plan_fg: str = "#48968c"                   # Claude Code "planMode" rgb(72,150,140)
    mode_yolo_fg: str = "#ffc107"                   # warning amber
    mode_bash_fg: str = "#fd5db1"                   # bash pink
    mode_vim_fg: str = "magenta"

    # --- hints + pasted markers ---
    hint_fg: str = "#999999"                        # "inactive" gray
    pasted_marker_fg: str = "#999999"

    # --- brand accent (borders + panel title) ---
    brand_accent: str = LLMCODE_BLUE_MID
    brand_muted: str = LLMCODE_BLUE_DEEP

    # --- logo gradient stops (consumed by components.logo) ---
    llmcode_blue_deep: str = LLMCODE_BLUE_DEEP
    llmcode_blue_dark: str = LLMCODE_BLUE_DARK
    llmcode_blue_mid: str = LLMCODE_BLUE_MID
    llmcode_blue_light: str = LLMCODE_BLUE_LIGHT
    llmcode_blue_hilite: str = LLMCODE_BLUE_HILITE

    # --- shadow tone for 3D logo edge ---
    logo_shadow_fg: str = "#061834"

    # --- agent rotating palette (6 distinct tones for sub-agent labels) ---
    agent_palette: tuple = (
        LLMCODE_BLUE_LIGHT,
        "bright_green",
        "bright_magenta",
        "#f0a030",
        "bright_cyan",
        "#ff7ab6",
    )

    def slot_names(self) -> list[str]:
        """Return the list of slot attribute names (for introspection / tests)."""
        return [f.name for f in fields(self)]


def default_palette() -> BrandPalette:
    """Return a fresh default-tone ``BrandPalette`` instance."""
    return BrandPalette()


def load_palette(runtime_config: Any) -> BrandPalette:
    """Build a :class:`BrandPalette` from a runtime config.

    Reads ``runtime_config.theme.overrides`` (a dict keyed by slot
    name) and replaces the matching slots on the default palette.
    Unknown keys are silently ignored for forward compatibility.

    Parameters
    ----------
    runtime_config:
        Any object with a ``theme`` attribute whose ``overrides``
        is a dict. Passing ``None`` or a config without a theme
        returns the default palette unchanged.
    """
    base = default_palette()
    if runtime_config is None:
        return base
    theme = getattr(runtime_config, "theme", None)
    if theme is None:
        return base
    overrides = getattr(theme, "overrides", None) or {}
    if not isinstance(overrides, dict):
        return base
    valid_slots = {f.name for f in fields(BrandPalette)}
    filtered = {k: v for k, v in overrides.items() if k in valid_slots}
    if not filtered:
        return base
    return replace(base, **filtered)


# -----------------------------------------------------------------
# Palette singleton — accessed as ``style.palette`` across modules.
# -----------------------------------------------------------------

palette: BrandPalette = default_palette()


def set_palette(new_palette: BrandPalette) -> None:
    """Replace the module-level ``palette`` singleton.

    Called once at REPL startup from ``cli/main._run_repl`` after
    runtime config resolves so user theme overrides propagate to
    every component that imports ``palette``.
    """
    global palette
    palette = new_palette


# -----------------------------------------------------------------
# Shimmer helpers.
# -----------------------------------------------------------------

# Five keyframes interpolating across the tech-blue ramp.
SHIMMER_KEYFRAMES: tuple[str, ...] = (
    LLMCODE_BLUE_DEEP,
    LLMCODE_BLUE_DARK,
    LLMCODE_BLUE_MID,
    LLMCODE_BLUE_LIGHT,
    LLMCODE_BLUE_HILITE,
)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _lerp_rgb(
    a: tuple[int, int, int], b: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    return (
        int(round(a[0] + (b[0] - a[0]) * t)),
        int(round(a[1] + (b[1] - a[1]) * t)),
        int(round(a[2] + (b[2] - a[2]) * t)),
    )


def shimmer_color(phase: float) -> str:
    """Return the shimmer color for ``phase`` ∈ [0.0, 1.0].

    Walks the :data:`SHIMMER_KEYFRAMES` ramp and returns a hex
    color. ``phase`` wraps modulo 1.0 for convenience, so callers
    can pass un-normalized values from a time-based driver.
    """
    # Clamp 1.0 to the final keyframe instead of wrapping it to 0.0
    if phase >= 1.0:
        phase = 1.0 if phase == 1.0 else phase % 1.0
    elif phase < 0:
        phase = 1.0 - ((-phase) % 1.0)
    n = len(SHIMMER_KEYFRAMES)
    segment = phase * (n - 1)
    idx = int(math.floor(segment))
    if idx >= n - 1:
        return SHIMMER_KEYFRAMES[-1]
    t = segment - idx
    a = _hex_to_rgb(SHIMMER_KEYFRAMES[idx])
    b = _hex_to_rgb(SHIMMER_KEYFRAMES[idx + 1])
    return _rgb_to_hex(_lerp_rgb(a, b, t))


def shimmer_phase_for_time(t_seconds: float, period: float = 2.4) -> float:
    """Map a wall-clock second into a shimmer phase (triangle wave).

    Triangle wave rather than sawtooth so the color ramps up and
    down smoothly — avoids a visible "snap" when the cycle wraps.
    """
    if period <= 0:
        return 0.0
    x = (t_seconds % period) / period
    return 2 * x if x < 0.5 else 2 * (1 - x)


def context_color(pct: float) -> str:
    """Grade context-window fill into a status color.

    <60% → green, 60-80% → yellow, >80% → red. Used by the status
    line context meter.
    """
    if pct < 0.6:
        return palette.status_success
    if pct < 0.8:
        return palette.status_warning
    return palette.status_error


# -----------------------------------------------------------------
# OSC8 hyperlink helper.
# -----------------------------------------------------------------


def hyperlink(text: str, url: str) -> str:
    """Wrap ``text`` in an OSC8 hyperlink envelope.

    Supported in Warp, iTerm2, WezTerm, and recent Kitty / Alacritty.
    Terminals that don't understand the OSC8 sequence silently
    render the plain text.
    """
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"

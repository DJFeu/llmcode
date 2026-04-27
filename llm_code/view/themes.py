"""8 built-in named themes for the v2.0.0 REPL (v16 M4).

Each theme is a :class:`BrandPalette` derived by selectively
overriding semantic slots on the M15 default palette
(``llm_code.view.repl.style.default_palette``). The README's "Theme
system: 8" claim resolves to the keys in :data:`BUILTIN_THEMES`.

Why a thin layer
----------------

The v2.5.x audit flagged ``/theme`` as a no-op stub. Rather than
reinvent a Rich ``Theme`` registry from scratch, we reuse the
existing ``BrandPalette`` plumbing — every component already routes
its colors through ``palette``, so swapping the palette singleton
re-tints the live UI in one shot.

Theme names map to common terminal/editor conventions: ``default``,
``dark``, ``light``, ``solarized``, ``dracula``, ``nord``,
``gruvbox``, ``monokai``. They use named ANSI colors and hex values
that are visually equivalent in 256-color and truecolor terminals.

Risk mitigations
----------------

* Color drift between Rich versions is avoided by sticking to named
  ANSI tokens or 6-digit hex; no dependency on Rich's named-style
  table.
* Unknown theme names fall back to the default with a warning logged
  so the dispatcher's error message can list valid options.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from llm_code.view.repl.style import BrandPalette, default_palette

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Theme definitions
# ---------------------------------------------------------------------------


def _default_theme() -> BrandPalette:
    return default_palette()


def _dark_theme() -> BrandPalette:
    base = default_palette()
    return replace(
        base,
        assistant_fg="#e8e8e8",
        user_fg="#ffffff",
        system_fg="#7f7f7f",
        thinking_fg="#7f7f7f",
        tool_args_fg="#666666",
        markdown_link="#5fafff",
    )


def _light_theme() -> BrandPalette:
    base = default_palette()
    return replace(
        base,
        assistant_fg="#1c1c1c",
        user_fg="#000000",
        system_fg="#666666",
        thinking_fg="#666666",
        tool_args_fg="#444444",
        tool_ok_fg="#067a00",
        tool_fail_fg="#a01020",
        bash_cmd_fg="#067a00",
        bash_err_fg="#a01020",
        diff_add_fg="#067a00",
        diff_del_fg="#a01020",
        markdown_heading="#1c1c1c",
        markdown_link="#1565c0",
        status_success="#067a00",
        status_warning="#bf6900",
        status_error="#a01020",
        status_info="#1565c0",
        status_dim="#888888",
    )


def _solarized_theme() -> BrandPalette:
    base = default_palette()
    return replace(
        base,
        assistant_fg="#839496",
        user_fg="#93a1a1",
        user_prefix="#268bd2",
        system_fg="#586e75",
        thinking_fg="#586e75",
        tool_name_fg="#93a1a1",
        tool_args_fg="#586e75",
        tool_ok_fg="#859900",
        tool_fail_fg="#dc322f",
        bash_cmd_fg="#859900",
        bash_err_fg="#dc322f",
        diff_add_fg="#859900",
        diff_del_fg="#dc322f",
        markdown_link="#268bd2",
        status_success="#859900",
        status_warning="#b58900",
        status_error="#dc322f",
        status_info="#268bd2",
        status_dim="#586e75",
        brand_accent="#268bd2",
    )


def _dracula_theme() -> BrandPalette:
    base = default_palette()
    return replace(
        base,
        assistant_fg="#f8f8f2",
        user_fg="#f8f8f2",
        user_prefix="#bd93f9",
        system_fg="#6272a4",
        thinking_fg="#6272a4",
        tool_name_fg="#f8f8f2",
        tool_args_fg="#6272a4",
        tool_ok_fg="#50fa7b",
        tool_fail_fg="#ff5555",
        bash_cmd_fg="#50fa7b",
        bash_err_fg="#ff5555",
        diff_add_fg="#50fa7b",
        diff_del_fg="#ff5555",
        markdown_link="#8be9fd",
        status_success="#50fa7b",
        status_warning="#f1fa8c",
        status_error="#ff5555",
        status_info="#8be9fd",
        status_dim="#6272a4",
        brand_accent="#bd93f9",
    )


def _nord_theme() -> BrandPalette:
    base = default_palette()
    return replace(
        base,
        assistant_fg="#d8dee9",
        user_fg="#eceff4",
        user_prefix="#88c0d0",
        system_fg="#4c566a",
        thinking_fg="#4c566a",
        tool_name_fg="#eceff4",
        tool_args_fg="#4c566a",
        tool_ok_fg="#a3be8c",
        tool_fail_fg="#bf616a",
        bash_cmd_fg="#a3be8c",
        bash_err_fg="#bf616a",
        diff_add_fg="#a3be8c",
        diff_del_fg="#bf616a",
        markdown_link="#88c0d0",
        status_success="#a3be8c",
        status_warning="#ebcb8b",
        status_error="#bf616a",
        status_info="#88c0d0",
        status_dim="#4c566a",
        brand_accent="#5e81ac",
    )


def _gruvbox_theme() -> BrandPalette:
    base = default_palette()
    return replace(
        base,
        assistant_fg="#ebdbb2",
        user_fg="#fbf1c7",
        user_prefix="#458588",
        system_fg="#928374",
        thinking_fg="#928374",
        tool_name_fg="#fbf1c7",
        tool_args_fg="#928374",
        tool_ok_fg="#b8bb26",
        tool_fail_fg="#fb4934",
        bash_cmd_fg="#b8bb26",
        bash_err_fg="#fb4934",
        diff_add_fg="#b8bb26",
        diff_del_fg="#fb4934",
        markdown_link="#83a598",
        status_success="#b8bb26",
        status_warning="#fabd2f",
        status_error="#fb4934",
        status_info="#83a598",
        status_dim="#928374",
        brand_accent="#d79921",
    )


def _monokai_theme() -> BrandPalette:
    base = default_palette()
    return replace(
        base,
        assistant_fg="#f8f8f2",
        user_fg="#f8f8f2",
        user_prefix="#66d9ef",
        system_fg="#75715e",
        thinking_fg="#75715e",
        tool_name_fg="#f8f8f2",
        tool_args_fg="#75715e",
        tool_ok_fg="#a6e22e",
        tool_fail_fg="#f92672",
        bash_cmd_fg="#a6e22e",
        bash_err_fg="#f92672",
        diff_add_fg="#a6e22e",
        diff_del_fg="#f92672",
        markdown_link="#66d9ef",
        status_success="#a6e22e",
        status_warning="#fd971f",
        status_error="#f92672",
        status_info="#66d9ef",
        status_dim="#75715e",
        brand_accent="#fd971f",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# Theme name → factory that returns a fresh ``BrandPalette``. Lazy
# construction so import time stays cheap and tests can mutate without
# poisoning the next caller.
_THEME_FACTORIES: dict[str, "callable"] = {
    "default": _default_theme,
    "dark": _dark_theme,
    "light": _light_theme,
    "solarized": _solarized_theme,
    "dracula": _dracula_theme,
    "nord": _nord_theme,
    "gruvbox": _gruvbox_theme,
    "monokai": _monokai_theme,
}


def list_theme_names() -> tuple[str, ...]:
    """Return the 8 built-in theme names in stable order."""
    return tuple(_THEME_FACTORIES.keys())


def get_theme(name: str) -> BrandPalette | None:
    """Return a fresh palette for *name*, or ``None`` if unknown."""
    factory = _THEME_FACTORIES.get(name)
    if factory is None:
        return None
    return factory()


# Eagerly build the dict for code paths that prefer a mapping lookup.
# Each access calls the factory so ``BUILTIN_THEMES["dracula"]`` always
# yields a fresh frozen dataclass.
class _LazyThemeMap:
    """Mapping facade — keys() returns names, indexing returns palettes."""

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in _THEME_FACTORIES

    def __getitem__(self, name: str) -> BrandPalette:
        palette = get_theme(name)
        if palette is None:
            raise KeyError(name)
        return palette

    def keys(self) -> tuple[str, ...]:
        return list_theme_names()

    def __iter__(self):
        return iter(_THEME_FACTORIES)

    def __len__(self) -> int:
        return len(_THEME_FACTORIES)


BUILTIN_THEMES = _LazyThemeMap()


def apply_theme_to_palette(name: str) -> BrandPalette | None:
    """Swap the global ``style.palette`` singleton to the named theme.

    Returns the palette that was applied, or ``None`` if *name* is
    unknown (the existing palette is left untouched). Callers can use
    the return value to drive a one-shot redraw of the status line and
    the recent message buffer.
    """
    palette = get_theme(name)
    if palette is None:
        _logger.warning("unknown theme %r — ignoring", name)
        return None
    from llm_code.view.repl.style import set_palette

    set_palette(palette)
    return palette

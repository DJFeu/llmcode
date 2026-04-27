"""Tests for the v16 M4 themes module."""
from __future__ import annotations

import pytest

from llm_code.view.repl import style as style_module
from llm_code.view.repl.style import BrandPalette, default_palette, set_palette
from llm_code.view.themes import (
    BUILTIN_THEMES,
    apply_theme_to_palette,
    get_theme,
    list_theme_names,
)

# Defensive: every test in this file mutates the module-level palette
# singleton. Snapshot it once and restore after each test.


@pytest.fixture(autouse=True)
def restore_palette():
    snapshot = style_module.palette
    yield
    set_palette(snapshot)


# ---------------------------------------------------------------------------
# Theme registry
# ---------------------------------------------------------------------------


class TestThemeRegistry:
    def test_eight_themes_exposed(self) -> None:
        names = list_theme_names()
        assert len(names) == 8
        assert set(names) == {
            "default",
            "dark",
            "light",
            "solarized",
            "dracula",
            "nord",
            "gruvbox",
            "monokai",
        }

    def test_default_first(self) -> None:
        # Stable order — humans reading "/theme" expect default first.
        assert list_theme_names()[0] == "default"


class TestThemeFactory:
    @pytest.mark.parametrize("name", [
        "default", "dark", "light", "solarized",
        "dracula", "nord", "gruvbox", "monokai",
    ])
    def test_each_theme_returns_palette(self, name: str) -> None:
        palette = get_theme(name)
        assert isinstance(palette, BrandPalette)

    def test_unknown_theme_returns_none(self) -> None:
        assert get_theme("not-a-theme") is None

    def test_named_styles_resolve(self) -> None:
        # Sample a slot every theme must populate.
        for name in list_theme_names():
            palette = get_theme(name)
            assert palette is not None
            assert palette.assistant_fg
            assert palette.tool_ok_fg
            assert palette.tool_fail_fg
            assert palette.status_info

    def test_themes_differ_from_default(self) -> None:
        base = default_palette()
        for name in ["dark", "light", "solarized", "dracula",
                     "nord", "gruvbox", "monokai"]:
            other = get_theme(name)
            assert other is not None
            # At least one slot must differ — otherwise the theme is a
            # silent alias of "default", which the README would not
            # honestly call out as a separate theme.
            assert other != base, f"theme {name!r} is identical to default"


# ---------------------------------------------------------------------------
# BUILTIN_THEMES mapping facade
# ---------------------------------------------------------------------------


class TestBuiltinThemesMap:
    def test_contains(self) -> None:
        assert "dracula" in BUILTIN_THEMES
        assert "not-a-theme" not in BUILTIN_THEMES

    def test_index_returns_palette(self) -> None:
        palette = BUILTIN_THEMES["dracula"]
        assert isinstance(palette, BrandPalette)

    def test_index_unknown_raises(self) -> None:
        with pytest.raises(KeyError):
            _ = BUILTIN_THEMES["ghost"]

    def test_keys_match_factory_names(self) -> None:
        assert BUILTIN_THEMES.keys() == list_theme_names()

    def test_len(self) -> None:
        assert len(BUILTIN_THEMES) == 8

    def test_iter(self) -> None:
        assert list(iter(BUILTIN_THEMES)) == list(list_theme_names())


# ---------------------------------------------------------------------------
# apply_theme_to_palette
# ---------------------------------------------------------------------------


class TestApplyTheme:
    def test_apply_known_theme(self) -> None:
        result = apply_theme_to_palette("dracula")
        assert result is not None
        # The module-level singleton flipped to the dracula palette.
        assert style_module.palette.user_prefix == result.user_prefix

    def test_apply_unknown_theme_returns_none(self) -> None:
        original = style_module.palette
        result = apply_theme_to_palette("ghost")
        assert result is None
        # Singleton untouched on miss.
        assert style_module.palette is original

    def test_round_trip_default_after_dracula(self) -> None:
        apply_theme_to_palette("dracula")
        apply_theme_to_palette("default")
        # Slots match a fresh default palette.
        fresh = default_palette()
        assert style_module.palette.assistant_fg == fresh.assistant_fg
        assert style_module.palette.brand_accent == fresh.brand_accent

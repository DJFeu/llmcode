"""Tests for TUI theme system."""
from __future__ import annotations

from llm_code.tui.theme import COLORS, apply_theme, get_active_theme
from llm_code.tui.themes import (
    BUILTIN_THEMES,
    DEFAULT,
    DRACULA,
    get_theme,
    list_themes,
)


class TestThemeRegistry:
    def test_8_builtin_themes(self) -> None:
        assert len(BUILTIN_THEMES) == 8

    def test_list_themes_sorted(self) -> None:
        names = list_themes()
        assert names == sorted(names)
        assert "default" in names
        assert "dracula" in names
        assert "tokyo-night" in names

    def test_get_theme_known(self) -> None:
        assert get_theme("dracula") is DRACULA

    def test_get_theme_unknown_falls_back(self) -> None:
        assert get_theme("nonexistent") is DEFAULT


class TestThemeFields:
    def test_all_themes_have_required_colors(self) -> None:
        required_keys = {"prompt", "tool_name", "success", "error", "dim", "spinner"}
        for name, theme in BUILTIN_THEMES.items():
            for key in required_keys:
                assert key in theme.colors, f"Theme '{name}' missing color '{key}'"

    def test_all_themes_have_accent(self) -> None:
        for name, theme in BUILTIN_THEMES.items():
            assert theme.accent.startswith("#"), f"Theme '{name}' accent not hex"


class TestApplyTheme:
    def test_apply_changes_colors(self) -> None:
        apply_theme("dracula")
        assert COLORS["prompt"] == "bold #bd93f9"
        # Restore
        apply_theme("default")

    def test_apply_changes_active(self) -> None:
        apply_theme("monokai")
        assert get_active_theme().name == "monokai"
        apply_theme("default")

    def test_apply_unknown_falls_back(self) -> None:
        theme = apply_theme("nonexistent")
        assert theme.name == "default"

    def test_colors_dict_is_mutated_in_place(self) -> None:
        """Importers holding a reference to COLORS see the update."""
        ref = COLORS
        apply_theme("nord")
        assert ref["prompt"] == "bold #88c0d0"
        apply_theme("default")
        assert ref["prompt"] == "bold cyan"

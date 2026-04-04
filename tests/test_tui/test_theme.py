"""Tests for TUI theme constants."""
from llm_code.tui.theme import COLORS, APP_CSS


def test_colors_has_required_keys():
    required = {"prompt", "tool_name", "tool_line", "success", "error",
                "diff_add", "diff_del", "thinking", "warning", "spinner", "dim"}
    assert required.issubset(set(COLORS.keys()))


def test_app_css_is_nonempty_string():
    assert isinstance(APP_CSS, str)
    assert len(APP_CSS) > 100


def test_colors_values_are_strings():
    for key, val in COLORS.items():
        assert isinstance(val, str), f"COLORS[{key!r}] should be a string"

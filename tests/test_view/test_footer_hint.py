"""Tests for footer hint + mode indicator (M15 Task A6)."""
from __future__ import annotations

from llm_code.view.repl import style
from llm_code.view.repl.components.footer_hint import FooterHint
from llm_code.view.repl.components.mode_indicator import ModeIndicator


def _flat(out) -> str:
    return "".join(text for _, text in out)


def test_footer_hint_default_content() -> None:
    out = FooterHint().render()
    flat = _flat(out)
    assert "Ctrl+G" in flat
    assert "/" in flat
    assert "Ctrl+D" in flat


def test_footer_hint_includes_keybinding_labels() -> None:
    out = FooterHint().render()
    flat = _flat(out)
    for word in ("voice", "commands", "history", "quit", "expand"):
        assert word in flat


def test_footer_hint_custom_provider() -> None:
    out = FooterHint(hint_provider=lambda: [("F1", "help")]).render()
    flat = _flat(out)
    assert "F1" in flat and "help" in flat


def test_mode_indicator_default_prompt() -> None:
    out = ModeIndicator().render()
    assert _flat(out) == "[prompt]"


def test_mode_indicator_bash_mode_color() -> None:
    m = ModeIndicator()
    m.set_mode("bash")
    out = m.render()
    assert style.palette.mode_bash_fg in out[0][0]


def test_mode_indicator_vim_sub_mode() -> None:
    m = ModeIndicator()
    m.set_mode("vim", vim_sub="NORMAL")
    assert "[vim:NORMAL]" in _flat(m.render())


def test_mode_indicator_unknown_mode_uses_hint_color() -> None:
    m = ModeIndicator()
    m.set_mode("unknown-mode")
    out = m.render()
    assert style.palette.hint_fg in out[0][0]

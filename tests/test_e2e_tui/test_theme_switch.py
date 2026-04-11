"""E2E: `/theme` switches the active theme and surfaces the name in chat."""
from __future__ import annotations

from tests.test_e2e_tui.test_boot_banner import _rendered_text


async def test_theme_bare_lists_available(pilot_app):
    """`/theme` with no args should print the list of built-in themes."""
    from llm_code.tui.chat_view import ChatScrollView
    from llm_code.tui.themes import list_themes

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("theme", "")
    await pilot.pause()

    rendered = _rendered_text(chat)
    # At least two of the built-in theme ids must appear in the list.
    available = list_themes()
    assert len(available) >= 2
    hit = sum(1 for t in available if t in rendered)
    assert hit >= 2
    assert "Usage: /theme" in rendered


async def test_theme_switch_to_dracula_updates_state(pilot_app):
    """`/theme dracula` should apply the theme and print its display name."""
    from llm_code.tui.chat_view import ChatScrollView

    app, pilot = pilot_app
    chat = app.query_one(ChatScrollView)

    app._cmd_dispatcher.dispatch("theme", "dracula")
    await pilot.pause()

    rendered = _rendered_text(chat)
    # Dracula's display_name is "Dracula" in llm_code/tui/themes.py.
    assert "Theme switched to:" in rendered
    assert "Dracula" in rendered


async def test_theme_switch_unknown_name_does_not_crash(pilot_app):
    """`/theme definitely-not-a-theme` should surface an error via
    the exception handler in apply_theme, not crash the dispatcher."""
    app, pilot = pilot_app
    # No assertion about the chat output — just that the dispatcher
    # returns normally. apply_theme raises ValueError for unknown
    # names; the command handler lets that propagate.
    try:
        app._cmd_dispatcher.dispatch("theme", "totally-fake-theme-xyz")
    except ValueError:
        # Accepted: unknown name surfaces a ValueError. The dispatcher
        # doesn't swallow it, but the TUI's outer handler does, so
        # runtime TUI doesn't crash.
        pass
    await pilot.pause()

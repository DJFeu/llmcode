"""E2E: boot banner renders + voice hotkey hint appears when enabled."""
from __future__ import annotations


async def test_boot_banner_renders_without_crash(pilot_app):
    """Pilot smoke test: the app boots and the default widgets exist."""
    app, _pilot = pilot_app
    # If on_mount crashed we wouldn't get here.
    from llm_code.tui.chat_view import ChatScrollView
    from llm_code.tui.header_bar import HeaderBar
    from llm_code.tui.input_bar import InputBar
    from llm_code.tui.status_bar import StatusBar

    assert app.query_one(ChatScrollView) is not None
    assert app.query_one(HeaderBar) is not None
    assert app.query_one(InputBar) is not None
    assert app.query_one(StatusBar) is not None


async def test_boot_banner_shows_quick_start_rows(pilot_app):
    """The welcome banner should have rendered at least one static
    entry into the chat view by the time on_mount completes."""
    app, _pilot = pilot_app
    from llm_code.tui.chat_view import ChatScrollView

    chat = app.query_one(ChatScrollView)
    # At least one child — the welcome Static. More likely ≥2 because
    # the background version check also enqueues an entry.
    assert len(list(chat.children)) >= 1


def _rendered_text(chat) -> str:
    """Concatenate the rendered plain text of every chat child.

    Goes through ``widget.render()`` rather than ``widget.renderable``
    because Textual's Static stores content in a private attribute
    and exposes it via the ``render()`` method. Handles Rich Text
    (``.plain``), strings, and anything else (``str(x)``).
    """
    out: list[str] = []
    for child in chat.children:
        try:
            rendered = child.render()
        except Exception:
            continue
        if rendered is None:
            continue
        if hasattr(rendered, "plain"):
            out.append(rendered.plain)
        else:
            out.append(str(rendered))
    return "\n".join(out)


async def test_voice_hotkey_hint_hidden_when_voice_disabled(pilot_app):
    """When `voice.enabled == False` (the default), the welcome banner
    must NOT include the voice hotkey row — otherwise we'd pollute
    the screen for the 95% of users who never touch voice."""
    app, _pilot = pilot_app
    from llm_code.tui.chat_view import ChatScrollView

    chat = app.query_one(ChatScrollView)
    rendered = _rendered_text(chat)
    # The row format is "Voice   Ctrl+<hotkey> to start/stop (…)".
    # Absence of the unique substring is the contract.
    assert "Ctrl+G to start/stop" not in rendered
    assert "(auto-stops on silence)" not in rendered


async def test_voice_hotkey_hint_visible_when_voice_enabled(pilot_voice_app):
    """When voice is enabled in config, the welcome banner should
    surface the hotkey in the Quick Start block."""
    app, _pilot = pilot_voice_app
    from llm_code.tui.chat_view import ChatScrollView

    chat = app.query_one(ChatScrollView)
    rendered = _rendered_text(chat)
    assert "Voice" in rendered
    assert "Ctrl+G" in rendered
    assert "start/stop" in rendered
    assert "auto-stops on silence" in rendered


async def test_quick_start_rows_present(pilot_app):
    """All five default quick-start rows should appear in the banner."""
    app, _pilot = pilot_app
    from llm_code.tui.chat_view import ChatScrollView

    chat = app.query_one(ChatScrollView)
    rendered = _rendered_text(chat)
    for token in ("Quick start", "Multiline", "Images", "Scroll", "Cycle agent"):
        assert token in rendered, f"missing banner row token: {token!r}"

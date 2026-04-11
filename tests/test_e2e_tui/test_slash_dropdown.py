"""E2E: `/` autocomplete dropdown — open, navigate, Tab/Enter/→ accept, Esc cancel."""
from __future__ import annotations


async def _type_text(pilot, text: str) -> None:
    """Drive individual character presses through pilot.press.

    pilot.press takes key names, and single-character keys use the
    character itself ("a", "/", "1", etc.). Space and punctuation
    need their Textual key name.
    """
    for ch in text:
        if ch == " ":
            await pilot.press("space")
        else:
            await pilot.press(ch)


async def test_slash_opens_dropdown(pilot_app):
    from llm_code.tui.input_bar import InputBar

    app, pilot = pilot_app
    bar = app.query_one(InputBar)
    bar.focus()
    await pilot.pause()

    await pilot.press("/")
    await pilot.pause()

    assert bar.value == "/"
    assert bar._show_dropdown is True
    assert len(bar._dropdown_items) > 0


async def test_slash_dropdown_filters_by_prefix(pilot_app):
    from llm_code.tui.input_bar import InputBar

    app, pilot = pilot_app
    bar = app.query_one(InputBar)
    bar.focus()
    await pilot.pause()

    # Type "/he" — should narrow dropdown to /help (and maybe /hida)
    await _type_text(pilot, "/he")
    await pilot.pause()

    assert bar.value == "/he"
    assert bar._show_dropdown is True
    displayed = {cmd for cmd, _ in bar._dropdown_items}
    assert "/help" in displayed


async def test_dropdown_down_arrow_moves_cursor(pilot_app):
    from llm_code.tui.input_bar import InputBar

    app, pilot = pilot_app
    bar = app.query_one(InputBar)
    bar.focus()
    await pilot.pause()

    await pilot.press("/")
    await pilot.pause()
    # With the dropdown open, down should move the dropdown cursor,
    # NOT recall prompt history.
    start_cursor = bar._dropdown_cursor
    await pilot.press("down")
    await pilot.pause()
    assert bar._dropdown_cursor == (start_cursor + 1) % len(bar._dropdown_items)


async def test_right_arrow_accepts_dropdown_selection(pilot_app):
    """The v1.20.0 feature: when the dropdown is up, `→` commits the
    highlighted command just like `Tab`/`Enter`. This is safe because
    the dropdown only appears before a space is typed, so the cursor
    is always at the end of the buffer."""
    from llm_code.tui.input_bar import InputBar

    app, pilot = pilot_app
    bar = app.query_one(InputBar)
    bar.focus()
    await pilot.pause()

    # Type /h → dropdown filters to /harness, /help, /hida
    await _type_text(pilot, "/h")
    await pilot.pause()
    assert bar._show_dropdown is True

    # The first (highlighted) entry after typing /h depends on sort
    # order. Just verify that `→` committed *something* and closed
    # the dropdown.
    first = bar._dropdown_items[bar._dropdown_cursor][0]
    await pilot.press("right")
    await pilot.pause()

    assert bar._show_dropdown is False
    # The buffer now contains the committed command (possibly with a
    # trailing space for arg-taking commands, or emptied if the
    # selection was no_arg and already submitted).
    assert first.rstrip(" ") in bar.value or bar.value == ""


async def test_escape_closes_dropdown(pilot_app):
    from llm_code.tui.input_bar import InputBar

    app, pilot = pilot_app
    bar = app.query_one(InputBar)
    bar.focus()
    await pilot.pause()

    await pilot.press("/")
    await pilot.pause()
    assert bar._show_dropdown is True

    await pilot.press("escape")
    await pilot.pause()
    assert bar._show_dropdown is False
    # Buffer is preserved — we only dismissed the dropdown.
    assert bar.value == "/"


async def test_tab_completes_unique_prefix(pilot_app):
    """Typing /he then Tab should auto-complete to /help/ (single
    match) — the fallback autocomplete path when the dropdown isn't
    claimed by a key handler."""
    from llm_code.tui.input_bar import InputBar

    app, pilot = pilot_app
    bar = app.query_one(InputBar)
    bar.focus()
    await pilot.pause()

    await _type_text(pilot, "/he")
    await pilot.pause()
    # Select /help in dropdown via Tab (accepts highlighted entry).
    await pilot.press("tab")
    await pilot.pause()
    # After Tab accept on a no_arg command, the buffer should either
    # contain the selected command name followed by a space (if it
    # takes args) or be empty (if it was submitted immediately as
    # a no_arg).
    assert bar.value == "" or bar.value.startswith("/help")

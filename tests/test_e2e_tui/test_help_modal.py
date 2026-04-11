"""E2E: `/help` modal opens, all 52 commands scroll, tab switch works."""
from __future__ import annotations


async def _open_help(app, pilot):
    """Simulate the user typing `/help` + Enter at the prompt.

    Returns the ``HelpScreen`` modal once it's pushed onto the
    screen stack, or None if pushing failed.
    """
    from llm_code.tui.input_bar import InputBar

    bar = app.query_one(InputBar)
    bar.value = "/help"
    bar._cursor = len(bar.value)
    # Drive the slash-command handler via the submit path. We use
    # the Submitted message so the full dispatch chain runs.
    bar.post_message(InputBar.Submitted("/help"))
    await pilot.pause()
    await pilot.pause()
    # The top screen should now be the modal HelpScreen.
    return app.screen


async def test_help_modal_opens_without_crash(pilot_app):
    app, pilot = pilot_app
    screen = await _open_help(app, pilot)
    assert screen is not None
    # The active screen should be the modal, not the default.
    assert screen.__class__.__name__ == "HelpScreen"


async def test_help_modal_commands_tab_has_52_options(pilot_app):
    """Switch to the commands tab and verify the OptionList was
    pre-populated with every entry from COMMAND_REGISTRY minus quit
    (which is an alias for exit)."""
    from textual.widgets import OptionList

    from llm_code.cli.commands import COMMAND_REGISTRY

    app, pilot = pilot_app
    screen = await _open_help(app, pilot)
    # Tab right once to land on the commands tab.
    await pilot.press("right")
    await pilot.pause()
    assert screen._tab == 1

    cmd_list = screen.query_one("#help-commands", OptionList)
    expected_count = len([c for c in COMMAND_REGISTRY if c.name != "quit"])
    assert cmd_list.option_count == expected_count


async def test_help_modal_scrolls_past_first_viewport(pilot_app):
    """The v1.19.0 bug: pressing down arrow past the first ~13 entries
    silently stopped because the surrounding VerticalScroll didn't
    know where the inline-`>`-cursor was. OptionList rewrite should
    highlight and scroll all the way to the last option."""
    from textual.widgets import OptionList

    app, pilot = pilot_app
    await _open_help(app, pilot)
    await pilot.press("right")
    await pilot.pause()

    cmd_list = app.screen.query_one("#help-commands", OptionList)
    total = cmd_list.option_count
    # Press down 20 times — should leave the initial viewport.
    for _ in range(20):
        await pilot.press("down")
    await pilot.pause()
    assert cmd_list.highlighted == 20
    # scroll_y tracks the content offset; must have moved off 0.
    assert cmd_list.scroll_y > 0

    # Continue to the last option. Stop one press before total because
    # OptionList wraps from the last entry back to 0 — we only want
    # to prove we got past the first viewport, not verify the wrap
    # semantics (those are Textual's business, not ours).
    for _ in range(total - 1 - 20):
        await pilot.press("down")
    await pilot.pause()
    assert cmd_list.highlighted == total - 1


async def test_help_modal_end_and_home_keys(pilot_app):
    """End jumps to last, Home jumps to first. These are OptionList
    freebies that broke in the pre-rewrite Static implementation
    because keys were intercepted for tab/cursor movement."""
    from textual.widgets import OptionList

    app, pilot = pilot_app
    await _open_help(app, pilot)
    await pilot.press("right")
    await pilot.pause()

    cmd_list = app.screen.query_one("#help-commands", OptionList)
    total = cmd_list.option_count

    await pilot.press("end")
    await pilot.pause()
    assert cmd_list.highlighted == total - 1

    await pilot.press("home")
    await pilot.pause()
    assert cmd_list.highlighted == 0


async def test_help_modal_tab_switches_focus(pilot_app):
    """Left / Right should cycle between general / commands /
    custom-commands tabs and move OptionList focus appropriately."""
    from textual.widgets import OptionList

    app, pilot = pilot_app
    screen = await _open_help(app, pilot)
    assert screen._tab == 0  # general tab

    await pilot.press("right")
    await pilot.pause()
    assert screen._tab == 1  # commands tab
    # The commands OptionList should have focus after the switch.
    cmd_list = app.screen.query_one("#help-commands", OptionList)
    assert cmd_list.has_focus

    await pilot.press("right")
    await pilot.pause()
    assert screen._tab == 2  # custom-commands tab

    # Right past the last tab should clamp, not crash.
    await pilot.press("right")
    await pilot.pause()
    assert screen._tab == 2


async def test_help_modal_escape_dismisses(pilot_app):
    """Esc should close the modal and return to the default screen."""
    app, pilot = pilot_app
    screen = await _open_help(app, pilot)
    assert screen.__class__.__name__ == "HelpScreen"

    await pilot.press("escape")
    await pilot.pause()
    # After dismiss, we're back on the default screen.
    assert app.screen.__class__.__name__ != "HelpScreen"

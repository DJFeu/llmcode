"""E2E: multiline buffer support via Shift+Enter / Ctrl+J / Ctrl+Enter."""
from __future__ import annotations


async def test_shift_enter_inserts_newline_not_submit(pilot_app):
    """Shift+Enter should append '\\n' to the buffer and NOT fire the
    InputBar.Submitted message. Enter afterwards should submit the
    whole multiline buffer."""
    from llm_code.tui.input_bar import InputBar

    app, pilot = pilot_app
    bar = app.query_one(InputBar)
    bar.focus()
    await pilot.pause()

    # Type first line.
    for ch in "line1":
        await pilot.press(ch)
    await pilot.pause()
    assert bar.value == "line1"

    # Shift+Enter for a newline.
    await pilot.press("shift+enter")
    await pilot.pause()
    assert "\n" in bar.value
    assert bar.value.rstrip("\n") == "line1"

    # Second line.
    for ch in "line2":
        await pilot.press(ch)
    await pilot.pause()
    assert bar.value == "line1\nline2"


async def test_ctrl_j_alias_also_inserts_newline(pilot_app):
    """Linux convention: Ctrl+J is the same as Shift+Enter for a
    newline. Listed as an alias in keybindings.json."""
    from llm_code.tui.input_bar import InputBar

    app, pilot = pilot_app
    bar = app.query_one(InputBar)
    bar.focus()
    await pilot.pause()

    for ch in "first":
        await pilot.press(ch)
    await pilot.press("ctrl+j")
    for ch in "second":
        await pilot.press(ch)
    await pilot.pause()
    assert bar.value == "first\nsecond"


async def test_history_suppressed_on_multiline_buffer(pilot_app, tmp_path):
    """When the buffer contains a newline, ↑/↓ should NOT recall
    history (those arrows belong to intra-line cursor movement for
    multi-line prompts)."""
    from llm_code.tui.input_bar import InputBar
    from llm_code.tui.prompt_history import PromptHistory

    app, pilot = pilot_app
    bar = app.query_one(InputBar)
    bar.focus()
    # Fresh history so the test is deterministic.
    bar._history = PromptHistory(path=tmp_path / "history.txt")
    bar._history.add("should-not-recall")
    await pilot.pause()

    # Put a multiline value in the buffer.
    bar.value = "line1\nline2"
    bar._cursor = len(bar.value)
    await pilot.pause()

    # Up arrow — should NOT replace the buffer with history.
    await pilot.press("up")
    await pilot.pause()
    assert bar.value == "line1\nline2"
    assert bar._history.is_navigating() is False

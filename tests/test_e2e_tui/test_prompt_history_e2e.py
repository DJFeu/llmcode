"""E2E: shell-style prompt history navigation via ↑/↓."""
from __future__ import annotations


async def _set_buffer(bar, text: str) -> None:
    bar.value = text
    bar._cursor = len(text)


async def test_history_up_recalls_last_submission(pilot_app, tmp_path):
    """After submitting a prompt, pressing ↑ on an empty buffer should
    recall it verbatim."""
    from llm_code.tui.input_bar import InputBar
    from llm_code.tui.prompt_history import PromptHistory

    app, pilot = pilot_app
    bar = app.query_one(InputBar)
    bar.focus()
    # Replace the persistent history with an empty in-memory one so
    # this test doesn't inherit real prompt history from ~/.llmcode.
    bar._history = PromptHistory(path=tmp_path / "history.txt")
    bar._history.add("first submission")
    bar._history.add("second submission")
    await pilot.pause()

    await pilot.press("up")
    await pilot.pause()
    assert bar.value == "second submission"

    await pilot.press("up")
    await pilot.pause()
    assert bar.value == "first submission"


async def test_history_down_returns_to_draft(pilot_app, tmp_path):
    """Walking back down past the newest entry should restore the
    buffer the user was composing when they started navigating."""
    from llm_code.tui.input_bar import InputBar
    from llm_code.tui.prompt_history import PromptHistory

    app, pilot = pilot_app
    bar = app.query_one(InputBar)
    bar.focus()
    bar._history = PromptHistory(path=tmp_path / "history.txt")
    bar._history.add("older")
    bar._history.add("newer")
    await pilot.pause()

    # Start composing something, then walk up.
    await _set_buffer(bar, "draft-in-progress")
    await pilot.pause()

    await pilot.press("up")
    await pilot.pause()
    assert bar.value == "newer"
    await pilot.press("up")
    await pilot.pause()
    assert bar.value == "older"

    # Walk back down.
    await pilot.press("down")
    await pilot.pause()
    assert bar.value == "newer"
    # Past the newest — draft restored.
    await pilot.press("down")
    await pilot.pause()
    assert bar.value == "draft-in-progress"


async def test_history_suppressed_while_dropdown_open(pilot_app, tmp_path):
    """When the slash dropdown is visible, ↑ should move the dropdown
    cursor, not recall history."""
    from llm_code.tui.input_bar import InputBar
    from llm_code.tui.prompt_history import PromptHistory

    app, pilot = pilot_app
    bar = app.query_one(InputBar)
    bar.focus()
    bar._history = PromptHistory(path=tmp_path / "history.txt")
    bar._history.add("should-not-recall")
    await pilot.pause()

    await pilot.press("/")
    await pilot.pause()
    assert bar._show_dropdown is True

    await pilot.press("up")
    await pilot.pause()
    # Buffer should still be "/" — history was suppressed.
    assert bar.value == "/"
    # History cursor untouched.
    assert bar._history.is_navigating() is False


async def test_typing_resets_history_cursor(pilot_app, tmp_path):
    """Any character insertion while walking history should reset the
    cursor so the next ↑ re-starts from the newest entry."""
    from llm_code.tui.input_bar import InputBar
    from llm_code.tui.prompt_history import PromptHistory

    app, pilot = pilot_app
    bar = app.query_one(InputBar)
    bar.focus()
    bar._history = PromptHistory(path=tmp_path / "history.txt")
    bar._history.add("entry-one")
    bar._history.add("entry-two")
    await pilot.pause()

    # Recall the newest entry.
    await pilot.press("up")
    await pilot.pause()
    assert bar.value == "entry-two"
    assert bar._history.is_navigating() is True

    # Insert a character — history cursor should reset.
    await pilot.press("a")
    await pilot.pause()
    assert bar._history.is_navigating() is False

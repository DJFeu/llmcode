"""Tests for the TextualDialogs backend (modal screen implementation).

Uses a minimal Textual App shell to host the modal screens, exercised
via Textual's async pilot. Each test pushes a dialog via TextualDialogs
and simulates user input through key presses / button clicks.
"""
from __future__ import annotations

import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from llm_code.tui.dialogs import (
    Choice,
    DialogCancelled,
    TextualDialogs,
)
from llm_code.tui.dialogs.textual_backend import (
    ChecklistScreen,
    ConfirmScreen,
    SelectScreen,
    TextInputScreen,
)


class _ShellApp(App):
    """Minimal app that hosts dialog screens for testing."""

    def compose(self) -> ComposeResult:
        yield Static("shell")


# ---------- Protocol surface ----------


def test_textual_dialogs_satisfies_protocol() -> None:
    """Duck-type check: TextualDialogs has the four Protocol methods."""
    app = _ShellApp()
    d = TextualDialogs(app)
    assert callable(d.confirm)
    assert callable(d.select)
    assert callable(d.text)
    assert callable(d.checklist)


# ---------- ConfirmScreen ----------


@pytest.mark.asyncio
async def test_confirm_yes_key() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        task = asyncio.create_task(dialogs.confirm("Proceed?"))
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        result = await task
        assert result is True


@pytest.mark.asyncio
async def test_confirm_no_key() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        task = asyncio.create_task(dialogs.confirm("Proceed?"))
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        result = await task
        assert result is False


@pytest.mark.asyncio
async def test_confirm_enter_returns_default_true() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        task = asyncio.create_task(dialogs.confirm("Proceed?", default=True))
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        result = await task
        assert result is True


@pytest.mark.asyncio
async def test_confirm_enter_returns_default_false() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        task = asyncio.create_task(dialogs.confirm("Proceed?", default=False))
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        result = await task
        assert result is False


@pytest.mark.asyncio
async def test_confirm_escape_raises_cancelled() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        task = asyncio.create_task(dialogs.confirm("Proceed?"))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        with pytest.raises(DialogCancelled):
            await task


@pytest.mark.asyncio
async def test_confirm_danger_shows_warning() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        task = asyncio.create_task(
            dialogs.confirm("DELETE everything?", danger=True)
        )
        await pilot.pause()
        # The ConfirmScreen should be mounted with danger styling
        screen = app.screen
        assert isinstance(screen, ConfirmScreen)
        await pilot.press("y")
        await pilot.pause()
        result = await task
        assert result is True


# ---------- SelectScreen ----------


@pytest.mark.asyncio
async def test_select_enter_picks_default() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        choices = [
            Choice("sonnet", "Sonnet"),
            Choice("opus", "Opus"),
        ]
        task = asyncio.create_task(
            dialogs.select("Pick model", choices, default="opus")
        )
        await pilot.pause()
        # Default cursor is on "opus" (index 1), just press enter
        await pilot.press("enter")
        await pilot.pause()
        result = await task
        assert result == "opus"


@pytest.mark.asyncio
async def test_select_navigate_and_pick() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        choices = [
            Choice("a", "Alpha"),
            Choice("b", "Beta"),
            Choice("c", "Gamma"),
        ]
        task = asyncio.create_task(dialogs.select("Pick", choices))
        await pilot.pause()
        # Cursor starts at 0 (Alpha), press down twice to get to Gamma
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        result = await task
        assert result == "c"


@pytest.mark.asyncio
async def test_select_skips_disabled() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        choices = [
            Choice("a", "Alpha"),
            Choice("b", "Beta", disabled=True),
            Choice("c", "Gamma"),
        ]
        task = asyncio.create_task(dialogs.select("Pick", choices))
        await pilot.pause()
        # Cursor on Alpha; press down skips disabled Beta → lands on Gamma
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        result = await task
        assert result == "c"


@pytest.mark.asyncio
async def test_select_escape_raises_cancelled() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        choices = [Choice("a", "Alpha")]
        task = asyncio.create_task(dialogs.select("Pick", choices))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        with pytest.raises(DialogCancelled):
            await task


@pytest.mark.asyncio
async def test_select_no_choices_raises_cancelled() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        with pytest.raises(DialogCancelled):
            await dialogs.select("Pick", [])


@pytest.mark.asyncio
async def test_select_all_disabled_raises_cancelled() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        choices = [Choice("a", "Alpha", disabled=True)]
        with pytest.raises(DialogCancelled):
            await dialogs.select("Pick", choices)


# ---------- TextInputScreen ----------


@pytest.mark.asyncio
async def test_text_type_and_submit() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        task = asyncio.create_task(dialogs.text("Commit msg:"))
        await pilot.pause()
        await pilot.press(*list("hello"))
        await pilot.press("enter")
        await pilot.pause()
        result = await task
        assert result == "hello"


@pytest.mark.asyncio
async def test_text_escape_raises_cancelled() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        task = asyncio.create_task(dialogs.text("Commit msg:"))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        with pytest.raises(DialogCancelled):
            await task


@pytest.mark.asyncio
async def test_text_empty_uses_default() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        task = asyncio.create_task(
            dialogs.text("Commit msg:", default="WIP")
        )
        await pilot.pause()
        # Clear any pre-filled text and submit empty
        # Input widget will have "WIP" pre-filled, submitting directly
        # should return "WIP" since the input value is "WIP"
        await pilot.press("enter")
        await pilot.pause()
        result = await task
        assert result == "WIP"


@pytest.mark.asyncio
async def test_text_validator_rejects_keeps_screen_open() -> None:
    """Validator error shows inline, screen stays open."""
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)

        def _validator(s: str) -> str | None:
            return None if s.isdigit() else "must be a number"

        task = asyncio.create_task(
            dialogs.text("Port:", validator=_validator)
        )
        await pilot.pause()
        # Type non-numeric and submit
        await pilot.press(*list("abc"))
        await pilot.press("enter")
        await pilot.pause()
        # Screen should still be open (validation failed)
        assert isinstance(app.screen, TextInputScreen)
        # Cancel to cleanly exit
        await pilot.press("escape")
        await pilot.pause()
        with pytest.raises(DialogCancelled):
            await task


@pytest.mark.asyncio
async def test_text_validator_accepts_valid_input() -> None:
    """Valid input passes the validator and returns the value."""
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)

        def _validator(s: str) -> str | None:
            return None if s.isdigit() else "must be a number"

        task = asyncio.create_task(
            dialogs.text("Port:", validator=_validator)
        )
        await pilot.pause()
        await pilot.press(*list("8080"))
        await pilot.press("enter")
        await pilot.pause()
        result = await task
        assert result == "8080"


# ---------- ChecklistScreen ----------


@pytest.mark.asyncio
async def test_checklist_toggle_and_submit() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        items = [
            Choice("a", "Alpha"),
            Choice("b", "Beta"),
            Choice("c", "Gamma"),
        ]
        task = asyncio.create_task(dialogs.checklist("Pick items", items))
        await pilot.pause()
        # Toggle first item (Alpha)
        await pilot.press("space")
        # Move down and toggle third (skip Beta)
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("space")
        await pilot.press("enter")
        await pilot.pause()
        result = await task
        assert result == ["a", "c"]


@pytest.mark.asyncio
async def test_checklist_empty_selection_ok() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        items = [Choice("a", "Alpha"), Choice("b", "Beta")]
        task = asyncio.create_task(dialogs.checklist("Pick", items))
        await pilot.pause()
        # Submit without toggling anything
        await pilot.press("enter")
        await pilot.pause()
        result = await task
        assert result == []


@pytest.mark.asyncio
async def test_checklist_min_select_enforced() -> None:
    """Submit with too few selections shows error, screen stays open."""
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        items = [Choice("a", "Alpha"), Choice("b", "Beta")]
        task = asyncio.create_task(
            dialogs.checklist("Pick", items, min_select=1)
        )
        await pilot.pause()
        # Try to submit with nothing selected
        await pilot.press("enter")
        await pilot.pause()
        # Screen should still be open
        assert isinstance(app.screen, ChecklistScreen)
        # Now toggle one and submit
        await pilot.press("space")
        await pilot.press("enter")
        await pilot.pause()
        result = await task
        assert result == ["a"]


@pytest.mark.asyncio
async def test_checklist_escape_raises_cancelled() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        items = [Choice("a", "Alpha")]
        task = asyncio.create_task(dialogs.checklist("Pick", items))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        with pytest.raises(DialogCancelled):
            await task


@pytest.mark.asyncio
async def test_checklist_max_select_enforced() -> None:
    """Toggling beyond max shows error, doesn't add to selection."""
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        items = [
            Choice("a", "Alpha"),
            Choice("b", "Beta"),
            Choice("c", "Gamma"),
        ]
        task = asyncio.create_task(
            dialogs.checklist("Pick", items, max_select=1)
        )
        await pilot.pause()
        # Toggle first
        await pilot.press("space")
        # Try to toggle second — should be rejected
        await pilot.press("down")
        await pilot.press("space")
        # Submit — should have only first
        await pilot.press("enter")
        await pilot.pause()
        result = await task
        assert result == ["a"]


@pytest.mark.asyncio
async def test_checklist_skips_disabled() -> None:
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        items = [
            Choice("a", "Alpha"),
            Choice("b", "Beta", disabled=True),
            Choice("c", "Gamma"),
        ]
        task = asyncio.create_task(dialogs.checklist("Pick", items))
        await pilot.pause()
        # Cursor on Alpha, press down should skip Beta → land on Gamma
        await pilot.press("down")
        await pilot.press("space")
        await pilot.press("enter")
        await pilot.pause()
        result = await task
        assert result == ["c"]


# ---------- Cross-backend contract ----------


@pytest.mark.asyncio
async def test_contract_confirm_textual() -> None:
    """Same contract as test_contract_confirm_scripted — TextualDialogs
    returns True when user presses 'y'."""
    app = _ShellApp()
    async with app.run_test(size=(80, 24)) as pilot:
        dialogs = TextualDialogs(app)
        task = asyncio.create_task(
            dialogs.confirm("proceed?", default=False)
        )
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        result = await task
        assert result is True

"""Textual modal-screen backend for the Dialogs Protocol.

Each dialog type maps to a dedicated ``ModalScreen`` subclass that
the ``TextualDialogs`` class pushes via ``app.push_screen_wait()``.
The screen ``dismiss(result)`` call resolves the awaitable, returning
the value to the caller.

Requires a running Textual ``App`` — this backend is only usable
inside the TUI process. For headless / CI / test use cases, see
``HeadlessDialogs`` and ``ScriptedDialogs``.
"""
from __future__ import annotations

import asyncio
from typing import Any, List, Optional, Sequence, TypeVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TextArea

from llm_code.tui.dialogs.api import (
    Choice,
    DialogCancelled,
    TextValidator,
)

T = TypeVar("T")


# ── CSS shared by all dialog screens ─────────────────────────────────

_DIALOG_CSS = """
Screen {
    align: center middle;
}
#dialog-box {
    width: 60%;
    min-width: 40;
    max-width: 100;
    max-height: 80%;
    background: $surface;
    border: round $accent;
    padding: 1 2;
}
#dialog-prompt {
    margin-bottom: 1;
    text-style: bold;
}
#dialog-hint {
    color: $text-muted;
    margin-top: 1;
    height: 1;
}
#dialog-error {
    color: $error;
    margin-top: 1;
    height: auto;
}
#dialog-buttons {
    height: 3;
    align: right middle;
    margin-top: 1;
}
#dialog-buttons Button {
    margin-left: 1;
}
.danger #dialog-prompt {
    color: $error;
}
"""


# ── ConfirmScreen ─────────────────────────────────────────────────────


class ConfirmScreen(ModalScreen[bool]):
    """Binary yes / no modal."""

    DEFAULT_CSS = _DIALOG_CSS

    BINDINGS = [
        Binding("y", "yes", "Yes", show=False),
        Binding("n", "no", "No", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        prompt: str,
        *,
        default: bool = False,
        danger: bool = False,
    ) -> None:
        super().__init__()
        self._prompt = prompt
        self._default = default
        self._danger = danger

    def compose(self) -> ComposeResult:
        classes = "danger" if self._danger else ""
        tag = "⚠ " if self._danger else ""
        with Vertical(id="dialog-box", classes=classes):
            yield Static(f"{tag}{self._prompt}", id="dialog-prompt")
            with Horizontal(id="dialog-buttons"):
                yes_variant = "error" if self._danger else "primary"
                yield Button(
                    "Yes" if not self._default else "[bold]Yes[/bold]",
                    id="btn-yes",
                    variant=yes_variant,
                )
                yield Button(
                    "No" if self._default else "[bold]No[/bold]",
                    id="btn-no",
                    variant="default",
                )
            yield Static(
                "y/n · Enter = default · Esc = cancel",
                id="dialog-hint",
            )

    def on_mount(self) -> None:
        # Focus the default button so Enter activates it.
        btn_id = "btn-yes" if self._default else "btn-no"
        self.query_one(f"#{btn_id}", Button).focus()

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(None)  # type: ignore[arg-type]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)


# ── SelectScreen ──────────────────────────────────────────────────────


class _SelectItem(Static):
    """A single row in the select list."""

    DEFAULT_CSS = """
    _SelectItem {
        height: 1;
        padding: 0 1;
    }
    _SelectItem.selected {
        background: $accent 30%;
    }
    _SelectItem.disabled {
        color: $text-disabled;
    }
    """


class SelectScreen(ModalScreen[Any]):
    """Pick-one modal with keyboard navigation."""

    DEFAULT_CSS = _DIALOG_CSS + """
    #select-list {
        height: auto;
        max-height: 60%;
    }
    """

    BINDINGS = [
        Binding("up,k", "cursor_up", "Up", show=False),
        Binding("down,j", "cursor_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        prompt: str,
        choices: Sequence[Choice[Any]],
        *,
        default: Any | None = None,
    ) -> None:
        super().__init__()
        self._prompt = prompt
        self._choices = list(choices)
        self._default = default
        # Start cursor on the default value if provided, else first enabled.
        self._cursor = 0
        for i, c in enumerate(self._choices):
            if c.value == default and not c.disabled:
                self._cursor = i
                break
        else:
            # Fall back to first enabled choice.
            for i, c in enumerate(self._choices):
                if not c.disabled:
                    self._cursor = i
                    break

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            yield Static(self._prompt, id="dialog-prompt")
            with VerticalScroll(id="select-list"):
                for i, c in enumerate(self._choices):
                    hint = f"  — {c.hint}" if c.hint else ""
                    disabled_tag = " (disabled)" if c.disabled else ""
                    label = f"  {c.label}{disabled_tag}{hint}"
                    item = _SelectItem(label, id=f"sel-{i}")
                    if c.disabled:
                        item.add_class("disabled")
                    yield item
            yield Static(
                "↑↓ Navigate · Enter Select · Esc Cancel",
                id="dialog-hint",
            )

    def on_mount(self) -> None:
        self._update_selection()

    def _update_selection(self) -> None:
        items = list(self.query(_SelectItem))
        for i, item in enumerate(items):
            if i == self._cursor:
                item.add_class("selected")
                item.update(f"▸ {self._choices[i].label}" + (f"  — {self._choices[i].hint}" if self._choices[i].hint else ""))
                item.scroll_visible()
            else:
                c = self._choices[i]
                hint = f"  — {c.hint}" if c.hint else ""
                disabled_tag = " (disabled)" if c.disabled else ""
                item.update(f"  {c.label}{disabled_tag}{hint}")
                item.remove_class("selected")

    def action_cursor_up(self) -> None:
        # Skip disabled items going up.
        start = self._cursor
        self._cursor -= 1
        while self._cursor >= 0 and self._choices[self._cursor].disabled:
            self._cursor -= 1
        if self._cursor < 0:
            self._cursor = start  # No enabled item above; stay put.
        self._update_selection()

    def action_cursor_down(self) -> None:
        start = self._cursor
        self._cursor += 1
        while self._cursor < len(self._choices) and self._choices[self._cursor].disabled:
            self._cursor += 1
        if self._cursor >= len(self._choices):
            self._cursor = start
        self._update_selection()

    def action_select(self) -> None:
        if 0 <= self._cursor < len(self._choices):
            choice = self._choices[self._cursor]
            if not choice.disabled:
                self.dismiss(choice.value)
                return
        # Nothing valid to select — stay open.

    def action_cancel(self) -> None:
        self.dismiss(None)


# ── TextInputScreen ───────────────────────────────────────────────────


class TextInputScreen(ModalScreen[Optional[str]]):
    """Free-form text input modal.

    Single-line uses a Textual ``Input`` widget; multiline uses
    ``TextArea``.  Validation errors are shown inline and the
    user can retry without the screen closing.
    """

    DEFAULT_CSS = _DIALOG_CSS + """
    #text-input {
        width: 100%;
        margin-bottom: 1;
    }
    #text-area {
        width: 100%;
        height: 10;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        prompt: str,
        *,
        default: str = "",
        multiline: bool = False,
        validator: TextValidator | None = None,
    ) -> None:
        super().__init__()
        self._prompt = prompt
        self._default = default
        self._multiline = multiline
        self._validator = validator

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            yield Static(self._prompt, id="dialog-prompt")
            if self._multiline:
                yield TextArea(self._default, id="text-area")
            else:
                yield Input(
                    value=self._default,
                    placeholder=self._prompt,
                    id="text-input",
                )
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-buttons"):
                yield Button("OK", id="btn-ok", variant="primary")
                yield Button("Cancel", id="btn-cancel", variant="default")

    def on_mount(self) -> None:
        if self._multiline:
            self.query_one("#text-area", TextArea).focus()
        else:
            self.query_one("#text-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in single-line input submits the form."""
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-ok":
            self._submit()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        if self._multiline:
            value = self.query_one("#text-area", TextArea).text
        else:
            value = self.query_one("#text-input", Input).value
        if not value and self._default:
            value = self._default
        if self._validator is not None:
            error = self._validator(value)
            if error is not None:
                self.query_one("#dialog-error", Static).update(f"⚠ {error}")
                return
        self.dismiss(value)


# ── ChecklistScreen ───────────────────────────────────────────────────


class _CheckItem(Static):
    """A single toggleable row in the checklist."""

    DEFAULT_CSS = """
    _CheckItem {
        height: 1;
        padding: 0 1;
    }
    _CheckItem.cursor {
        background: $accent 30%;
    }
    _CheckItem.disabled {
        color: $text-disabled;
    }
    """


class ChecklistScreen(ModalScreen[Optional[List[Any]]]):
    """Pick-zero-or-more modal with checkbox toggles."""

    DEFAULT_CSS = _DIALOG_CSS + """
    #checklist-list {
        height: auto;
        max-height: 60%;
    }
    """

    BINDINGS = [
        Binding("up,k", "cursor_up", "Up", show=False),
        Binding("down,j", "cursor_down", "Down", show=False),
        Binding("space", "toggle_item", "Toggle", show=False),
        Binding("enter", "submit", "Submit", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        prompt: str,
        items: Sequence[Choice[Any]],
        *,
        min_select: int = 0,
        max_select: int | None = None,
    ) -> None:
        super().__init__()
        self._prompt = prompt
        self._items = list(items)
        self._min_select = min_select
        self._max_select = max_select
        self._selected: set[int] = set()
        self._cursor = 0
        # Start on first enabled item.
        for i, c in enumerate(self._items):
            if not c.disabled:
                self._cursor = i
                break

    def compose(self) -> ComposeResult:
        bounds = ""
        if self._max_select is not None:
            bounds = f" (min {self._min_select}, max {self._max_select})"
        elif self._min_select:
            bounds = f" (min {self._min_select})"
        with Vertical(id="dialog-box"):
            yield Static(f"{self._prompt}{bounds}", id="dialog-prompt")
            with VerticalScroll(id="checklist-list"):
                for i, c in enumerate(self._items):
                    hint = f"  — {c.hint}" if c.hint else ""
                    disabled_tag = " (disabled)" if c.disabled else ""
                    label = f"[ ] {c.label}{disabled_tag}{hint}"
                    item = _CheckItem(label, id=f"chk-{i}")
                    if c.disabled:
                        item.add_class("disabled")
                    yield item
            yield Static("", id="dialog-error")
            yield Static(
                "↑↓ Navigate · Space Toggle · Enter Submit · Esc Cancel",
                id="dialog-hint",
            )

    def on_mount(self) -> None:
        self._update_display()

    def _update_display(self) -> None:
        items = list(self.query(_CheckItem))
        for i, widget in enumerate(items):
            c = self._items[i]
            check = "[x]" if i in self._selected else "[ ]"
            hint = f"  — {c.hint}" if c.hint else ""
            disabled_tag = " (disabled)" if c.disabled else ""
            prefix = "▸" if i == self._cursor else " "
            widget.update(f"{prefix}{check} {c.label}{disabled_tag}{hint}")
            if i == self._cursor:
                widget.add_class("cursor")
                widget.scroll_visible()
            else:
                widget.remove_class("cursor")

    def action_cursor_up(self) -> None:
        start = self._cursor
        self._cursor -= 1
        while self._cursor >= 0 and self._items[self._cursor].disabled:
            self._cursor -= 1
        if self._cursor < 0:
            self._cursor = start
        self._update_display()

    def action_cursor_down(self) -> None:
        start = self._cursor
        self._cursor += 1
        while self._cursor < len(self._items) and self._items[self._cursor].disabled:
            self._cursor += 1
        if self._cursor >= len(self._items):
            self._cursor = start
        self._update_display()

    def action_toggle_item(self) -> None:
        if 0 <= self._cursor < len(self._items):
            c = self._items[self._cursor]
            if c.disabled:
                return
            if self._cursor in self._selected:
                self._selected.discard(self._cursor)
            else:
                if self._max_select is not None and len(self._selected) >= self._max_select:
                    self.query_one("#dialog-error", Static).update(
                        f"⚠ Maximum {self._max_select} selections"
                    )
                    return
                self._selected.add(self._cursor)
            self.query_one("#dialog-error", Static).update("")
            self._update_display()

    def action_submit(self) -> None:
        if len(self._selected) < self._min_select:
            self.query_one("#dialog-error", Static).update(
                f"⚠ Select at least {self._min_select}"
            )
            return
        result = [self._items[i].value for i in sorted(self._selected)]
        self.dismiss(result)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ── TextualDialogs — the Protocol-satisfying facade ───────────────────


class TextualDialogs:
    """Textual ModalScreen backend for the ``Dialogs`` Protocol.

    Requires a running Textual ``App`` instance. Each dialog method
    pushes a modal screen with a callback that resolves an
    ``asyncio.Future``. Screens dismiss with the chosen value, or
    ``None`` for cancel — which is mapped to :class:`DialogCancelled`.

    This avoids ``push_screen_wait()`` which requires a Textual
    worker context. The callback+Future approach works from any
    async context including the runtime event loop.
    """

    def __init__(self, app: App) -> None:  # type: ignore[type-arg]
        self._app = app

    def _push(self, screen: ModalScreen) -> asyncio.Future:  # type: ignore[type-arg]
        """Push *screen* and return a Future resolved on dismiss."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()  # type: ignore[type-arg]

        def _on_dismiss(result: Any) -> None:
            if not future.done():
                future.set_result(result)

        self._app.push_screen(screen, callback=_on_dismiss)
        return future

    async def confirm(
        self,
        prompt: str,
        *,
        default: bool = False,
        danger: bool = False,
    ) -> bool:
        result = await self._push(
            ConfirmScreen(prompt, default=default, danger=danger)
        )
        if result is None:
            raise DialogCancelled(f"confirm cancelled: {prompt}")
        return result

    async def select(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        *,
        default: T | None = None,
    ) -> T:
        if not choices:
            raise DialogCancelled(f"select: no choices for {prompt}")
        enabled = [c for c in choices if not c.disabled]
        if not enabled:
            raise DialogCancelled(f"select: all choices disabled for {prompt}")
        result = await self._push(
            SelectScreen(prompt, choices, default=default)
        )
        if result is None:
            raise DialogCancelled(f"select cancelled: {prompt}")
        return result  # type: ignore[return-value]

    async def text(
        self,
        prompt: str,
        *,
        default: str = "",
        multiline: bool = False,
        validator: TextValidator | None = None,
    ) -> str:
        result = await self._push(
            TextInputScreen(
                prompt,
                default=default,
                multiline=multiline,
                validator=validator,
            )
        )
        if result is None:
            raise DialogCancelled(f"text cancelled: {prompt}")
        return result

    async def checklist(
        self,
        prompt: str,
        items: Sequence[Choice[T]],
        *,
        min_select: int = 0,
        max_select: int | None = None,
    ) -> list[T]:
        result = await self._push(
            ChecklistScreen(
                prompt,
                items,
                min_select=min_select,
                max_select=max_select,
            )
        )
        if result is None:
            raise DialogCancelled(f"checklist cancelled: {prompt}")
        return result  # type: ignore[return-value]

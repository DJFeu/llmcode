"""DialogPopover — inline dialog overlays for the REPL backend.

Hosts four dialog kinds (confirm, select, text input, checklist) in a
single prompt_toolkit Float slot. The coordinator reserves one Float
for dialogs; at any time either zero or one dialog is active.

Activation protocol:
    1. Dispatcher calls show_confirm/select/text/checklist on the
       backend
    2. Backend awaits DialogPopover.show_*(request) which:
       a. Stores request + creates an asyncio.Future
       b. Awaits the future (suspends the backend coroutine)
    3. Dialog key handlers manipulate the request state and either
       set the future's result (on submit) or set DialogCancelled
       (on Esc)
    4. After the future resolves, the dispatcher's caller continues.

Only one dialog is active at a time. Calling show_*() while another
dialog is active raises RuntimeError (the dispatcher should never
nest dialogs — if it needs chained confirms, it awaits them in
sequence).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, TypeVar, Union

from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    Float,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame

from llm_code.view.dialog_types import Choice, DialogCancelled, TextValidator
from llm_code.view.types import RiskLevel

T = TypeVar("T")


@dataclass
class ConfirmRequest:
    prompt: str
    default: bool
    risk: RiskLevel
    future: asyncio.Future


@dataclass
class SelectRequest:
    prompt: str
    choices: List[Choice]
    default: Optional[Any]
    future: asyncio.Future
    cursor: int = 0


@dataclass
class TextInputRequest:
    prompt: str
    default: Optional[str]
    validator: Optional[TextValidator]
    secret: bool
    future: asyncio.Future
    buffer: str = ""
    error_message: Optional[str] = None


@dataclass
class ChecklistRequest:
    prompt: str
    choices: List[Choice]
    defaults: List[Any]
    future: asyncio.Future
    cursor: int = 0
    selected: List[Any] = field(default_factory=list)


DialogRequest = Union[
    ConfirmRequest, SelectRequest, TextInputRequest, ChecklistRequest,
]


class DialogPopover:
    """Owns zero-or-one active dialog request + renders it as a Float."""

    def __init__(self) -> None:
        self._active: Optional[DialogRequest] = None
        # M15: PT Application reference for triggering redraws when
        # a dialog becomes active. Set by ScreenCoordinator.start().
        self._app: Any = None

    def set_app(self, app: Any) -> None:
        """Inject the PT Application for invalidation."""
        self._app = app

    def _invalidate(self) -> None:
        """Trigger a PT redraw so the dialog Float appears/disappears."""
        if self._app is not None and getattr(self._app, "invalidate", None):
            self._app.invalidate()

    # === Public API ===

    def is_active(self) -> bool:
        return self._active is not None

    @property
    def active(self) -> Optional[DialogRequest]:
        return self._active

    async def show_confirm(
        self,
        prompt: str,
        default: bool = False,
        risk: RiskLevel = RiskLevel.NORMAL,
    ) -> bool:
        """Show a confirm dialog and await the user's answer."""
        if self._active is not None:
            raise RuntimeError(
                "dialog already active — nested dialogs not supported"
            )
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._active = ConfirmRequest(
            prompt=prompt, default=default, risk=risk, future=future,
        )
        self._invalidate()
        try:
            return await future
        finally:
            self._active = None
            self._invalidate()

    async def show_select(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        default: Optional[T] = None,
    ) -> T:
        if self._active is not None:
            raise RuntimeError("dialog already active")
        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        default_cursor = 0
        choices_list = list(choices)
        if default is not None:
            for i, c in enumerate(choices_list):
                if c.value == default:
                    default_cursor = i
                    break
        self._active = SelectRequest(
            prompt=prompt, choices=choices_list, default=default,
            future=future, cursor=default_cursor,
        )
        self._invalidate()
        try:
            return await future
        finally:
            self._active = None
            self._invalidate()

    async def show_text_input(
        self,
        prompt: str,
        default: Optional[str] = None,
        validator: Optional[TextValidator] = None,
        secret: bool = False,
    ) -> str:
        if self._active is not None:
            raise RuntimeError("dialog already active")
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._active = TextInputRequest(
            prompt=prompt, default=default, validator=validator,
            secret=secret, future=future, buffer=default or "",
        )
        self._invalidate()
        try:
            return await future
        finally:
            self._active = None
            self._invalidate()

    async def show_checklist(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        defaults: Optional[Sequence[T]] = None,
    ) -> Sequence[T]:
        if self._active is not None:
            raise RuntimeError("dialog already active")
        future: asyncio.Future[List[T]] = asyncio.get_running_loop().create_future()
        self._active = ChecklistRequest(
            prompt=prompt, choices=list(choices),
            defaults=list(defaults) if defaults else [],
            future=future,
            selected=list(defaults) if defaults else [],
        )
        self._invalidate()
        try:
            return await future
        finally:
            self._active = None
            self._invalidate()

    # === Programmatic control (for tests and keybindings) ===

    def submit(self) -> None:
        """Resolve the active dialog's future with the current state.

        For text input with a validator that rejects the current
        buffer, sets error_message and leaves the dialog active
        (the user gets another chance).
        """
        if self._active is None:
            return
        if isinstance(self._active, ConfirmRequest):
            self._active.future.set_result(self._active.default)
        elif isinstance(self._active, SelectRequest):
            choice = self._active.choices[self._active.cursor]
            self._active.future.set_result(choice.value)
        elif isinstance(self._active, TextInputRequest):
            if self._active.validator:
                err = self._active.validator(self._active.buffer)
                if err:
                    self._active.error_message = err
                    return  # don't resolve; user tries again
            self._active.future.set_result(self._active.buffer)
        elif isinstance(self._active, ChecklistRequest):
            self._active.future.set_result(list(self._active.selected))

    def accept_positive(self) -> None:
        """For confirm dialogs: resolve True."""
        if isinstance(self._active, ConfirmRequest):
            self._active.future.set_result(True)

    def accept_negative(self) -> None:
        """For confirm dialogs: resolve False."""
        if isinstance(self._active, ConfirmRequest):
            self._active.future.set_result(False)

    def cancel(self) -> None:
        """Reject the active dialog's future with DialogCancelled."""
        if self._active is None:
            return
        self._active.future.set_exception(DialogCancelled("user cancelled"))

    def move_cursor(self, delta: int) -> None:
        """Move cursor in select/checklist dialogs, wrapping modulo."""
        if isinstance(self._active, (SelectRequest, ChecklistRequest)):
            n = len(self._active.choices)
            if n > 0:
                self._active.cursor = (self._active.cursor + delta) % n

    def toggle_current(self) -> None:
        """For checklist dialogs: toggle current choice selected/unselected."""
        if not isinstance(self._active, ChecklistRequest):
            return
        choice_value = self._active.choices[self._active.cursor].value
        if choice_value in self._active.selected:
            self._active.selected.remove(choice_value)
        else:
            self._active.selected.append(choice_value)

    def insert_text(self, text: str) -> None:
        """For text input dialogs: append text to the buffer."""
        if isinstance(self._active, TextInputRequest):
            self._active.buffer += text
            self._active.error_message = None  # clear error on edit

    def delete_back(self) -> None:
        if isinstance(self._active, TextInputRequest) and self._active.buffer:
            self._active.buffer = self._active.buffer[:-1]

    # === Rendering ===

    def render_formatted(self) -> FormattedText:
        """Render the active dialog as a FormattedText block.

        Returns empty FormattedText when no dialog is active.
        """
        if self._active is None:
            return FormattedText([])

        if isinstance(self._active, ConfirmRequest):
            return self._render_confirm(self._active)
        if isinstance(self._active, SelectRequest):
            return self._render_select(self._active)
        if isinstance(self._active, TextInputRequest):
            return self._render_text_input(self._active)
        if isinstance(self._active, ChecklistRequest):
            return self._render_checklist(self._active)
        return FormattedText([])

    def _render_confirm(self, req: ConfirmRequest) -> FormattedText:
        risk_styles = {
            RiskLevel.NORMAL: "class:dialog.normal",
            RiskLevel.ELEVATED: "class:dialog.elevated",
            RiskLevel.HIGH: "class:dialog.high",
            RiskLevel.CRITICAL: "class:dialog.critical bold",
        }
        style = risk_styles.get(req.risk, "class:dialog.normal")
        default_char = "Y/n" if req.default else "y/N"
        return FormattedText([
            (style, f" {req.prompt} "),
            ("", f" [{default_char}] "),
        ])

    def _render_select(self, req: SelectRequest) -> FormattedText:
        parts: List[tuple] = [
            ("class:dialog.header bold", f" {req.prompt} \n"),
        ]
        for i, choice in enumerate(req.choices):
            marker = "▶ " if i == req.cursor else "  "
            style = "class:dialog.selected" if i == req.cursor else ""
            line = f"{marker}{choice.label}"
            if choice.hint:
                line += f"  ({choice.hint})"
            parts.append((style, line + "\n"))
        return FormattedText(parts)

    def _render_text_input(self, req: TextInputRequest) -> FormattedText:
        display_buffer = "*" * len(req.buffer) if req.secret else req.buffer
        parts: List[tuple] = [
            ("class:dialog.header bold", f" {req.prompt} \n"),
            ("", f" {display_buffer}▋\n"),
        ]
        if req.error_message:
            parts.append(("class:dialog.error", f" ✗ {req.error_message}\n"))
        return FormattedText(parts)

    def _render_checklist(self, req: ChecklistRequest) -> FormattedText:
        parts: List[tuple] = [
            ("class:dialog.header bold", f" {req.prompt} \n"),
        ]
        for i, choice in enumerate(req.choices):
            cursor_marker = "▶ " if i == req.cursor else "  "
            check_marker = "[x]" if choice.value in req.selected else "[ ]"
            style = "class:dialog.selected" if i == req.cursor else ""
            parts.append(
                (style, f"{cursor_marker}{check_marker} {choice.label}\n")
            )
        return FormattedText(parts)


def _dialog_height(popover: DialogPopover) -> int:
    """Dynamic height for the dialog Float based on content lines."""
    active = popover.active
    if active is None:
        return 3
    if isinstance(active, SelectRequest):
        # prompt + choices + 1 row padding
        return min(len(active.choices) + 2, 20)
    if isinstance(active, ChecklistRequest):
        return min(len(active.choices) + 2, 20)
    return 4


def build_dialog_float(popover: DialogPopover) -> Float:
    """Construct the PT Float wrapping the DialogPopover for the coordinator.

    Uses ``bottom=1`` so the dialog sits just above the input area.
    Height is computed dynamically from the dialog's content.
    """
    return Float(
        bottom=1,
        left=1,
        right=1,
        content=ConditionalContainer(
            content=Frame(
                Window(
                    FormattedTextControl(popover.render_formatted),
                    height=lambda: _dialog_height(popover),
                    wrap_lines=True,
                ),
                title="dialog",
                style="class:dialog.frame",
            ),
            filter=Condition(popover.is_active),
        ),
    )


def build_dialog_key_bindings(popover: DialogPopover) -> KeyBindings:
    """Keybindings active only while a dialog is shown.

    Bindings are scoped per dialog type to avoid collisions:
      - y/n only active in ConfirmRequest
      - space only in ChecklistRequest
      - up/down in SelectRequest or ChecklistRequest
      - <any> printable only in TextInputRequest
      - enter / escape / backspace active whenever any dialog is shown

    Merged into the main KeyBindings via PT's merge_key_bindings in
    the coordinator's start() so the dialog bindings take precedence
    while a dialog is active.
    """
    kb = KeyBindings()

    dialog_active = Condition(popover.is_active)
    is_confirm = Condition(
        lambda: isinstance(popover.active, ConfirmRequest)
    )
    is_select_like = Condition(
        lambda: isinstance(popover.active, (SelectRequest, ChecklistRequest))
    )
    is_checklist = Condition(
        lambda: isinstance(popover.active, ChecklistRequest)
    )
    is_text_input = Condition(
        lambda: isinstance(popover.active, TextInputRequest)
    )

    # Cursor movement — only in select / checklist
    @kb.add("up", filter=is_select_like)
    def _up(event) -> None:
        popover.move_cursor(-1)

    @kb.add("down", filter=is_select_like)
    def _down(event) -> None:
        popover.move_cursor(1)

    # Toggle for checklist
    @kb.add("space", filter=is_checklist)
    def _space(event) -> None:
        popover.toggle_current()

    # Y/N fast accept — only in confirm
    @kb.add("y", filter=is_confirm)
    def _y(event) -> None:
        popover.accept_positive()

    @kb.add("n", filter=is_confirm)
    def _n(event) -> None:
        popover.accept_negative()

    # Printable characters — only in text input
    @kb.add("<any>", filter=is_text_input)
    def _any(event) -> None:
        if event.data and len(event.data) == 1 and event.data.isprintable():
            popover.insert_text(event.data)

    @kb.add("backspace", filter=is_text_input)
    def _bs(event) -> None:
        popover.delete_back()

    # Enter submits
    @kb.add("enter", filter=dialog_active)
    def _enter(event) -> None:
        popover.submit()

    # Escape cancels
    @kb.add("escape", filter=dialog_active)
    def _esc(event) -> None:
        popover.cancel()

    return kb

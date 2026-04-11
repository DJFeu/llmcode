# M8 — Dialog Popovers

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Implement `DialogPopover` — inline prompt_toolkit `Float` overlays that replace the M3 hardcoded `return default` stubs for `show_confirm` / `show_select` / `show_text_input` / `show_checklist`. Transliterate the existing `tests/test_tui/test_dialogs_textual.py` coverage into the new location.

**Architecture:** A `DialogPopover` component holds an active dialog request and its awaiting `asyncio.Future`. The coordinator's `FloatContainer` has a second `Float` slot reserved for the dialog overlay, shown via `ConditionalContainer` only when a dialog is active. The dialog takes keyboard focus away from the input area (via PT's focus mechanism) and restores it on dismiss. All four dialog types share one popover class with a mode switch; this keeps the `Float` registration simple and avoids z-order complications.

**Tech Stack:** prompt_toolkit `Float`, `FloatContainer`, `Window`, `FormattedTextControl`, `ConditionalContainer`, `Condition`, `has_focus`, `Application.layout.focus()`.

**Spec reference:** §5.1 dialog methods, §6.7 dialog popovers, §9.2 test transliteration pattern.

**Dependencies:** M3 coordinator, M4 `FloatContainer` in layout. Parallel with M5/M6/M7/M9.

---

## File Structure

- Create: `llm_code/view/repl/components/dialog_popover.py` — `DialogPopover` + 4 dialog request types (~500 lines)
- Modify: `llm_code/view/repl/coordinator.py` — add dialog Float to the FloatContainer
- Modify: `llm_code/view/repl/backend.py` — replace 4 dialog stub methods with coordinator delegation
- Create: `tests/test_view/test_dialog_popover.py` — ~45 tests (transliterated + new), ~900 lines

---

## Tasks

### Task 8.1: Write DialogPopover component

**Files:** Create `llm_code/view/repl/components/dialog_popover.py`

- [ ] **Step 1: Write the component.**

```python
"""DialogPopover — inline dialog overlays for the REPL backend.

Hosts four dialog kinds (confirm, select, text input, checklist) in a
single prompt_toolkit Float slot. The coordinator reserves one Float
for dialogs; at any time either zero or one dialog is active.

Activation protocol:
    1. Dispatcher calls show_confirm/select/text/checklist on the
       backend
    2. Backend calls DialogPopover.show(request) which:
       a. Stores request + creates an asyncio.Future
       b. Takes focus (via coordinator.app.layout.focus)
       c. Triggers app.invalidate() so the Float renders
    3. Dialog key handlers manipulate the request state and either
       set the future's result (on submit) or set DialogCancelled
       (on Esc)
    4. After future resolves, DialogPopover.dismiss() is called:
       a. Clears the active request
       b. Returns focus to the input area
       c. Triggers app.invalidate()

Only one dialog is active at a time. Calling show() while another
dialog is active raises RuntimeError (the dispatcher should never
nest dialogs — if it needs chained confirms, it awaits them in
sequence).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence, TypeVar

from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    Float,
    HSplit,
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


DialogRequest = ConfirmRequest | SelectRequest | TextInputRequest | ChecklistRequest


class DialogPopover:
    """Owns zero-or-one active dialog request + renders it as a Float."""

    def __init__(self) -> None:
        self._active: Optional[DialogRequest] = None

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
            raise RuntimeError("dialog already active — nested dialogs not supported")
        future: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
        self._active = ConfirmRequest(
            prompt=prompt, default=default, risk=risk, future=future,
        )
        try:
            return await future
        finally:
            self._active = None

    async def show_select(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        default: Optional[T] = None,
    ) -> T:
        if self._active is not None:
            raise RuntimeError("dialog already active")
        future: asyncio.Future[T] = asyncio.get_event_loop().create_future()
        default_cursor = 0
        if default is not None:
            for i, c in enumerate(choices):
                if c.value == default:
                    default_cursor = i
                    break
        self._active = SelectRequest(
            prompt=prompt, choices=list(choices), default=default,
            future=future, cursor=default_cursor,
        )
        try:
            return await future
        finally:
            self._active = None

    async def show_text_input(
        self,
        prompt: str,
        default: Optional[str] = None,
        validator: Optional[TextValidator] = None,
        secret: bool = False,
    ) -> str:
        if self._active is not None:
            raise RuntimeError("dialog already active")
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._active = TextInputRequest(
            prompt=prompt, default=default, validator=validator,
            secret=secret, future=future, buffer=default or "",
        )
        try:
            return await future
        finally:
            self._active = None

    async def show_checklist(
        self,
        prompt: str,
        choices: Sequence[Choice[T]],
        defaults: Optional[Sequence[T]] = None,
    ) -> Sequence[T]:
        if self._active is not None:
            raise RuntimeError("dialog already active")
        future: asyncio.Future[List[T]] = asyncio.get_event_loop().create_future()
        self._active = ChecklistRequest(
            prompt=prompt, choices=list(choices),
            defaults=list(defaults) if defaults else [],
            future=future,
            selected=list(defaults) if defaults else [],
        )
        try:
            return await future
        finally:
            self._active = None

    # === Programmatic control (for tests and keybindings) ===

    def submit(self) -> None:
        """Resolve the active dialog's future with the current state."""
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
        """Move cursor in select/checklist dialogs."""
        if isinstance(self._active, (SelectRequest, ChecklistRequest)):
            n = len(self._active.choices)
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
        parts: list = [("class:dialog.header bold", f" {req.prompt} \n")]
        for i, choice in enumerate(req.choices):
            marker = "▶ " if i == req.cursor else "  "
            style = "class:dialog.selected" if i == req.cursor else ""
            line = f"{marker}{choice.label}"
            if choice.hint:
                line += f"  [dim]{choice.hint}[/dim]"
            parts.append((style, line + "\n"))
        return FormattedText(parts)

    def _render_text_input(self, req: TextInputRequest) -> FormattedText:
        display_buffer = "*" * len(req.buffer) if req.secret else req.buffer
        parts = [
            ("class:dialog.header bold", f" {req.prompt} \n"),
            ("", f" {display_buffer}▋\n"),
        ]
        if req.error_message:
            parts.append(("class:dialog.error", f" ✗ {req.error_message}\n"))
        return FormattedText(parts)

    def _render_checklist(self, req: ChecklistRequest) -> FormattedText:
        parts: list = [("class:dialog.header bold", f" {req.prompt} \n")]
        for i, choice in enumerate(req.choices):
            cursor_marker = "▶ " if i == req.cursor else "  "
            check_marker = "[x]" if choice.value in req.selected else "[ ]"
            style = "class:dialog.selected" if i == req.cursor else ""
            parts.append((style, f"{cursor_marker}{check_marker} {choice.label}\n"))
        return FormattedText(parts)


def build_dialog_float(popover: DialogPopover) -> Float:
    """Construct the PT Float wrapping the DialogPopover for the coordinator."""
    return Float(
        top=2,
        content=ConditionalContainer(
            content=Frame(
                Window(
                    FormattedTextControl(popover.render_formatted),
                    height=10,
                ),
                title="dialog",
                style="class:dialog.frame",
            ),
            filter=Condition(popover.is_active),
        ),
    )


def build_dialog_key_bindings(popover: DialogPopover) -> KeyBindings:
    """Keybindings active only while a dialog is shown.

    Merged into the main KeyBindings via PT's merge_key_bindings in
    the coordinator's start() so the dialog bindings take precedence
    while a dialog is active.
    """
    kb = KeyBindings()
    dialog_active = Condition(popover.is_active)

    # Cursor movement (select / checklist)
    @kb.add("up", filter=dialog_active)
    def _up(event):
        popover.move_cursor(-1)

    @kb.add("down", filter=dialog_active)
    def _down(event):
        popover.move_cursor(1)

    # Toggle for checklist
    @kb.add("space", filter=dialog_active)
    def _space(event):
        popover.toggle_current()

    # Y/N fast accept for confirm
    @kb.add("y", filter=dialog_active)
    def _y(event):
        popover.accept_positive()

    @kb.add("n", filter=dialog_active)
    def _n(event):
        popover.accept_negative()

    # Text input keys
    @kb.add("<any>", filter=dialog_active)
    def _any(event):
        from llm_code.view.repl.components.dialog_popover import TextInputRequest
        if isinstance(popover.active, TextInputRequest):
            if event.data and len(event.data) == 1 and event.data.isprintable():
                popover.insert_text(event.data)

    @kb.add("backspace", filter=dialog_active)
    def _bs(event):
        popover.delete_back()

    # Enter submits
    @kb.add("enter", filter=dialog_active)
    def _enter(event):
        popover.submit()

    # Escape cancels
    @kb.add("escape", filter=dialog_active)
    def _esc(event):
        popover.cancel()

    return kb
```

- [ ] **Step 2: Commit** — `git add llm_code/view/repl/components/dialog_popover.py && git commit -m "feat(view): DialogPopover for 4 dialog types"`

### Task 8.2: Wire DialogPopover into coordinator + backend

**Files:** Modify `llm_code/view/repl/coordinator.py`, `llm_code/view/repl/backend.py`

- [ ] **Step 1: Coordinator changes.**

Add to `__init__`:

```python
from llm_code.view.repl.components.dialog_popover import (
    DialogPopover, build_dialog_float, build_dialog_key_bindings,
)
from prompt_toolkit.key_binding import merge_key_bindings

self._dialog_popover = DialogPopover()
```

In `start()` after `self._key_bindings` is built, merge in dialog bindings:

```python
self._key_bindings = merge_key_bindings([
    self._key_bindings,
    build_dialog_key_bindings(self._dialog_popover),
])
```

In `_build_layout`, add the dialog Float to the `FloatContainer`:

```python
popover_float = self._input_area.build_popover_float()
dialog_float = build_dialog_float(self._dialog_popover)
return Layout(
    FloatContainer(
        content=HSplit([rate_limit_container, status_window, input_window]),
        floats=[popover_float, dialog_float],
    )
)
```

Add a property for backend access:

```python
@property
def dialog_popover(self) -> DialogPopover:
    return self._dialog_popover
```

- [ ] **Step 2: Backend changes.** Replace the 4 M3 stub methods in `REPLBackend`:

```python
async def show_confirm(self, prompt, default=False, risk=RiskLevel.NORMAL):
    return await self._coordinator.dialog_popover.show_confirm(prompt, default, risk)

async def show_select(self, prompt, choices, default=None):
    return await self._coordinator.dialog_popover.show_select(prompt, choices, default)

async def show_text_input(self, prompt, default=None, validator=None, secret=False):
    return await self._coordinator.dialog_popover.show_text_input(
        prompt, default, validator, secret,
    )

async def show_checklist(self, prompt, choices, defaults=None):
    return await self._coordinator.dialog_popover.show_checklist(prompt, choices, defaults)
```

- [ ] **Step 3: Run existing tests** — `pytest tests/test_view/ -v` → all pass.
- [ ] **Step 4: Commit** — `git commit -am "feat(view): coordinator + backend wire DialogPopover"`

### Task 8.3: Write DialogPopover tests

**Files:** Create `tests/test_view/test_dialog_popover.py`

- [ ] **Step 1: Write tests.**

Follow the transliteration pattern from spec §9.2. Representative samples:

```python
"""Tests for DialogPopover — transliterated from test_dialogs_textual.py."""
import pytest

from llm_code.view.repl.components.dialog_popover import DialogPopover
from llm_code.view.dialog_types import Choice, DialogCancelled
from llm_code.view.types import RiskLevel


@pytest.fixture
def popover():
    return DialogPopover()


# === Confirm ===

@pytest.mark.asyncio
async def test_confirm_accept_y(popover):
    task = asyncio.create_task(popover.show_confirm("ok?"))
    await asyncio.sleep(0)  # yield so the request is registered
    popover.accept_positive()
    result = await task
    assert result is True
    assert popover.is_active() is False

@pytest.mark.asyncio
async def test_confirm_accept_n(popover):
    task = asyncio.create_task(popover.show_confirm("ok?"))
    await asyncio.sleep(0)
    popover.accept_negative()
    assert await task is False

@pytest.mark.asyncio
async def test_confirm_default_applies_on_submit(popover):
    task = asyncio.create_task(popover.show_confirm("ok?", default=True))
    await asyncio.sleep(0)
    popover.submit()  # no explicit Y/N, uses default
    assert await task is True

@pytest.mark.asyncio
async def test_confirm_cancel_raises(popover):
    task = asyncio.create_task(popover.show_confirm("ok?"))
    await asyncio.sleep(0)
    popover.cancel()
    with pytest.raises(DialogCancelled):
        await task

@pytest.mark.asyncio
async def test_confirm_risk_level_in_style(popover):
    task = asyncio.create_task(
        popover.show_confirm("dangerous?", risk=RiskLevel.CRITICAL)
    )
    await asyncio.sleep(0)
    rendered = popover.render_formatted()
    assert any("critical" in seg[0].lower() for seg in rendered)
    popover.accept_negative()
    await task


# === Select ===

@pytest.mark.asyncio
async def test_select_default_cursor_and_submit(popover):
    task = asyncio.create_task(popover.show_select(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B"), Choice("c", "C")],
        default="b",
    ))
    await asyncio.sleep(0)
    assert popover.active.cursor == 1
    popover.submit()
    assert await task == "b"

@pytest.mark.asyncio
async def test_select_move_cursor_down(popover):
    task = asyncio.create_task(popover.show_select(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B")],
    ))
    await asyncio.sleep(0)
    popover.move_cursor(1)
    popover.submit()
    assert await task == "b"

@pytest.mark.asyncio
async def test_select_cursor_wraps(popover):
    task = asyncio.create_task(popover.show_select(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B")],
    ))
    await asyncio.sleep(0)
    popover.move_cursor(-1)  # wrap to last
    popover.submit()
    assert await task == "b"


# === Text input ===

@pytest.mark.asyncio
async def test_text_input_types_and_submits(popover):
    task = asyncio.create_task(popover.show_text_input("name:"))
    await asyncio.sleep(0)
    popover.insert_text("alice")
    popover.submit()
    assert await task == "alice"

@pytest.mark.asyncio
async def test_text_input_backspace(popover):
    task = asyncio.create_task(popover.show_text_input("x:"))
    await asyncio.sleep(0)
    popover.insert_text("hello")
    popover.delete_back()
    popover.submit()
    assert await task == "hell"

@pytest.mark.asyncio
async def test_text_input_validator_rejects(popover):
    def validator(s):
        return None if "@" in s else "must contain @"

    task = asyncio.create_task(popover.show_text_input(
        "email:", validator=validator,
    ))
    await asyncio.sleep(0)
    popover.insert_text("notanemail")
    popover.submit()  # rejected
    assert popover.is_active()
    assert popover.active.error_message is not None

    popover.insert_text("@x.com")
    popover.submit()  # accepted
    assert await task == "notanemail@x.com"

@pytest.mark.asyncio
async def test_text_input_secret_masks_render(popover):
    task = asyncio.create_task(popover.show_text_input(
        "password:", secret=True,
    ))
    await asyncio.sleep(0)
    popover.insert_text("hunter2")
    rendered = popover.render_formatted()
    # The display should show asterisks, not the raw text
    text = "".join(seg[1] for seg in rendered)
    assert "hunter2" not in text
    assert "*" in text
    popover.submit()
    assert await task == "hunter2"


# === Checklist ===

@pytest.mark.asyncio
async def test_checklist_toggle_and_submit(popover):
    task = asyncio.create_task(popover.show_checklist(
        "pick any",
        choices=[Choice("a", "A"), Choice("b", "B"), Choice("c", "C")],
    ))
    await asyncio.sleep(0)
    popover.toggle_current()  # select "a"
    popover.move_cursor(2)  # cursor on "c"
    popover.toggle_current()  # select "c"
    popover.submit()
    result = await task
    assert set(result) == {"a", "c"}

@pytest.mark.asyncio
async def test_checklist_defaults_preselected(popover):
    task = asyncio.create_task(popover.show_checklist(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B")],
        defaults=["a"],
    ))
    await asyncio.sleep(0)
    assert "a" in popover.active.selected
    popover.submit()
    assert "a" in await task

@pytest.mark.asyncio
async def test_checklist_empty_submit_returns_empty(popover):
    task = asyncio.create_task(popover.show_checklist(
        "pick",
        choices=[Choice("a", "A"), Choice("b", "B")],
    ))
    await asyncio.sleep(0)
    popover.submit()
    assert list(await task) == []


# === Nested dialogs rejected ===

@pytest.mark.asyncio
async def test_nested_dialog_raises():
    popover = DialogPopover()
    _ = asyncio.create_task(popover.show_confirm("first"))
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="already active"):
        await popover.show_confirm("second")
    popover.cancel()
```

Plus ~25 more transliterated tests covering: each risk level rendering, escape on each dialog kind, default handling edge cases, Unicode in prompts, very long prompts, choice hints, disabled choices.

Add missing `import asyncio` at top.

- [ ] **Step 2: Run** — `pytest tests/test_view/test_dialog_popover.py -v` → ~45 pass.
- [ ] **Step 3: Commit** — `git add tests/test_view/test_dialog_popover.py && git commit -m "test(view): DialogPopover transliterated + new coverage"`

---

## Milestone completion criteria

- ✅ `DialogPopover` + 4 request dataclasses + `build_dialog_float` + `build_dialog_key_bindings`
- ✅ Coordinator hosts dialog Float + merged key bindings
- ✅ Backend's 4 dialog methods delegate to coordinator popover
- ✅ ~45 tests green
- ✅ `DialogCancelled` propagates correctly (not swallowed)
- ✅ Existing view tests still green

## Estimated effort: ~3.5 hours

## Next milestone: M9 — Voice Overlay (`m9-voice.md`)

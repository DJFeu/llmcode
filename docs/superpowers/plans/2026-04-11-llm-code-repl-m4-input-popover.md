# M4 — Input Area + Slash Popover

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Replace the coordinator's placeholder input buffer with a real multi-line `InputArea` component that supports slash-command autocomplete popover, Ctrl+↑/↓ history recall, Tab/Shift+Enter/Esc bindings, and vim mode toggle. Transliterate the existing `tests/test_tui/test_input_bar.py` + `test_prompt_history_e2e.py` coverage into the `test_view/` tree.

**Architecture:** `InputArea` owns a prompt_toolkit `Buffer` with a custom `Completer` (for slash popover) plus a `KeyBindings` extension hooked into the coordinator's bindings. The slash popover uses PT's `CompletionsMenu` as a `Float` above the input window. Prompt history relocates from `tui/prompt_history.py` to `view/repl/history.py` unchanged.

**Tech Stack:** prompt_toolkit `Buffer`, `Completer`, `Completion`, `CompletionsMenu`, `KeyBindings`, `Float`, `FloatContainer`, Rich (for tests).

**Spec reference:** §5.1 keybindings summary, §6.2 bottom layout, §6.5 input handling, §6.7 slash popover.

**Dependencies:** M3 complete. Coordinator has stable layout slots. `repl_pilot` fixture works.

---

## File Structure

- Create: `llm_code/view/repl/history.py` — relocated `PromptHistory` (~130 lines, unchanged logic)
- Create: `llm_code/view/repl/components/__init__.py`
- Create: `llm_code/view/repl/components/input_area.py` — `InputArea` class (~380 lines)
- Create: `llm_code/view/repl/components/slash_popover.py` — `SlashCompleter` + menu wiring (~180 lines)
- Create: `llm_code/view/repl/keybindings.py` — PT `KeyBindings` factory (~200 lines, consolidates hardcoded bindings from M3 coordinator)
- Modify: `llm_code/view/repl/coordinator.py` — swap placeholder buffer for `InputArea`, call new `build_keybindings()`
- Create: `tests/test_view/test_input_area.py` — ~40 tests, ~800 lines
- Create: `tests/test_view/test_slash_popover.py` — ~20 tests, ~350 lines

---

## Tasks

### Task 4.1: Relocate PromptHistory

- [ ] **Step 1: Copy file.** `cp llm_code/tui/prompt_history.py llm_code/view/repl/history.py`
- [ ] **Step 2: Update imports** — no external imports reference `tui.prompt_history` outside `tui/` itself, so just keep the old file in place until M11. No other changes needed — the new file is a byte-identical copy.
- [ ] **Step 3: Verify import** — `python3 -c "from llm_code.view.repl.history import PromptHistory, default_history_path; h = PromptHistory(path=default_history_path()); print('OK')"` → `OK`
- [ ] **Step 4: Commit** — `git add llm_code/view/repl/history.py && git commit -m "feat(view): relocate PromptHistory to view/repl/history.py"`

### Task 4.2: Write view/repl/keybindings.py

**Files:** Create `llm_code/view/repl/keybindings.py`

- [ ] **Step 1: Write the file.**

```python
"""Factory for the REPL's prompt_toolkit KeyBindings.

Single place where every key → action mapping lives. The coordinator
calls build_keybindings() during Application construction; components
can extend the returned KeyBindings instance via merge_key_bindings()
for their local bindings (slash popover navigation, vim mode toggle,
voice hotkey in M9, etc.).
"""
from __future__ import annotations

from typing import Callable, Optional

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

from llm_code.view.repl.history import PromptHistory


def build_keybindings(
    *,
    input_buffer: Buffer,
    history: PromptHistory,
    on_submit: Callable[[str], None],
    on_exit: Callable[[], None],
    on_voice_toggle: Optional[Callable[[], None]] = None,
) -> KeyBindings:
    """Construct the full KeyBindings set for the REPL.

    Args:
        input_buffer: the PT Buffer the bindings operate on
        history: PromptHistory for Ctrl+↑/↓ recall
        on_submit: callback fired when Enter is pressed on non-empty text
        on_exit: callback fired on Ctrl+D (empty) / second Ctrl+C (empty)
        on_voice_toggle: optional; if set, Ctrl+G and Ctrl+Space fire it
    """
    kb = KeyBindings()

    # === Submit / newline ===

    @kb.add("enter")
    def _submit(event) -> None:
        text = input_buffer.text.strip()
        if not text:
            return
        input_buffer.reset()
        on_submit(text)
        event.app.invalidate()

    @kb.add("s-enter")
    @kb.add("c-j")           # Linux convention for newline
    @kb.add("escape", "enter")  # Alt+Enter (macOS convention)
    def _newline(event) -> None:
        input_buffer.insert_text("\n")

    # === Exit ===

    @kb.add("c-d")
    def _ctrl_d(event) -> None:
        if not input_buffer.text:
            on_exit()
            event.app.exit()

    @kb.add("c-c")
    def _ctrl_c(event) -> None:
        if input_buffer.text:
            input_buffer.reset()
        else:
            on_exit()
            event.app.exit()

    # === Clear / cancel ===

    @kb.add("c-u")
    def _clear_line(event) -> None:
        input_buffer.reset()

    @kb.add("escape")
    def _escape(event) -> None:
        # Esc clears input unless dropdown is open (handled by InputArea)
        input_buffer.reset()

    # === History recall (Ctrl+↑/↓) ===

    @kb.add("c-up")
    def _history_prev(event) -> None:
        current = input_buffer.text
        recalled = history.prev(current=current)
        if recalled is not None:
            input_buffer.text = recalled
            input_buffer.cursor_position = len(recalled)

    @kb.add("c-down")
    def _history_next(event) -> None:
        recalled = history.next()
        if recalled is not None:
            input_buffer.text = recalled
            input_buffer.cursor_position = len(recalled)

    # === Voice hotkey (Ctrl+G / Ctrl+Space) ===

    if on_voice_toggle is not None:
        @kb.add("c-g")
        @kb.add("c-@")  # prompt_toolkit encodes Ctrl+Space as Ctrl+@
        def _voice(event) -> None:
            on_voice_toggle()

    return kb
```

- [ ] **Step 2: Syntax + import check** — same pattern as previous milestones.
- [ ] **Step 3: Commit** — `git add llm_code/view/repl/keybindings.py && git commit -m "feat(view): extract prompt_toolkit KeyBindings factory"`

### Task 4.3: Write SlashCompleter (slash_popover.py)

**Files:** Create `llm_code/view/repl/components/__init__.py`, `llm_code/view/repl/components/slash_popover.py`

- [ ] **Step 1: Scaffold** — `touch llm_code/view/repl/components/__init__.py`
- [ ] **Step 2: Write `slash_popover.py`.**

```python
"""Slash-command autocomplete completer for the REPL input.

Triggers when the input buffer starts with `/`. Produces completions
from llm_code.cli.commands.COMMAND_REGISTRY + one-line descriptions.
Respects spec §6.7:

- Max 8 visible rows (4 in short terminals)
- Overflow shows "↓ N more"
- Tab accepts selected completion (does not submit)
- Esc dismisses popover + preserves typed text
- Bare ↑/↓ within popover navigates (default PT Completion behavior)
- Ctrl+↑/↓ fall through to history recall (handled in keybindings.py)
"""
from __future__ import annotations

from typing import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from llm_code.cli.commands import COMMAND_REGISTRY


def _build_slash_entries() -> list[tuple[str, str]]:
    """Return sorted (name, description) pairs for every registered command."""
    return sorted(
        (f"/{cmd.name}", cmd.description or "")
        for cmd in COMMAND_REGISTRY
    )


class SlashCompleter(Completer):
    """Completer that yields slash-command completions when the input
    starts with '/'.

    Not active for any other prefix — regular typing (no '/' prefix)
    produces no completions and the popover stays hidden.
    """

    def __init__(self) -> None:
        self._entries = _build_slash_entries()

    def refresh(self) -> None:
        """Re-scan COMMAND_REGISTRY (called after plugin load/unload)."""
        self._entries = _build_slash_entries()

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/"):
            return

        # Match prefix against the command name portion only (no args)
        command_prefix = text.split()[0] if text.split() else text
        for name, description in self._entries:
            if name.startswith(command_prefix):
                yield Completion(
                    text=name,
                    start_position=-len(command_prefix),
                    display=name,
                    display_meta=description,
                )
```

- [ ] **Step 3: Commit** — `git add llm_code/view/repl/components/__init__.py llm_code/view/repl/components/slash_popover.py && git commit -m "feat(view): SlashCompleter for command autocomplete popover"`

### Task 4.4: Write InputArea component

**Files:** Create `llm_code/view/repl/components/input_area.py`

- [ ] **Step 1: Write the class.**

```python
"""InputArea — multi-line input with slash popover, history, vim mode.

Owns a prompt_toolkit Buffer configured with:
- Multi-line editing (auto-expanding height up to 12 rows)
- SlashCompleter completer for /command popover
- History wiring via Ctrl+↑/↓ (bindings in keybindings.py)
- Optional vim mode (toggled by dispatcher via set_vim_mode())

The coordinator embeds this into its Layout. In M3 the coordinator had
a raw Buffer + placeholder Window; M4 swaps those for an InputArea
instance + its managed Window.
"""
from __future__ import annotations

from typing import Callable, Optional

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteStyle
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    Float,
    HSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.margins import ConditionalMargin, PromptMargin

from llm_code.view.repl.components.slash_popover import SlashCompleter


MIN_ROWS = 1
MAX_ROWS = 12


class InputArea:
    """Self-contained multi-line input component."""

    def __init__(self) -> None:
        self._completer = SlashCompleter()
        self.buffer = Buffer(
            multiline=True,
            completer=self._completer,
            complete_while_typing=True,
        )
        self._vim_mode = False

    @property
    def completer(self) -> SlashCompleter:
        return self._completer

    def refresh_completions(self) -> None:
        """Re-scan the slash command registry. Call after plugin load."""
        self._completer.refresh()

    def set_vim_mode(self, enabled: bool) -> None:
        """Toggle vim mode on the underlying buffer.

        prompt_toolkit implements vim mode at the Application level,
        not the Buffer level, so the coordinator is responsible for
        actually flipping Application.editing_mode. This method just
        tracks the desired state for the coordinator to query.
        """
        self._vim_mode = enabled

    @property
    def vim_mode(self) -> bool:
        return self._vim_mode

    def build_window(self) -> Window:
        """Construct the main input Window.

        Height is dynamic: min 1 row, max 12 rows, sized to the buffer's
        current content + 1 (for the trailing prompt cursor).
        """
        def _height() -> int:
            line_count = self.buffer.text.count("\n") + 1
            return max(MIN_ROWS, min(line_count, MAX_ROWS))

        return Window(
            content=BufferControl(
                buffer=self.buffer,
                focus_on_click=True,
            ),
            height=_height,
            wrap_lines=True,
            style="class:input",
        )

    def build_popover_float(self) -> Float:
        """Construct the Float that hosts the slash-completion popover.

        The popover only shows when the completer has matches AND the
        input starts with '/'. Otherwise it's hidden (no dropdown
        appears during regular typing).
        """
        has_slash = Condition(lambda: self.buffer.text.startswith("/"))

        return Float(
            xcursor=True,
            ycursor=True,
            content=ConditionalContainer(
                content=CompletionsMenu(max_height=8, scroll_offset=1),
                filter=has_slash,
            ),
        )
```

- [ ] **Step 2: Commit** — `git add llm_code/view/repl/components/input_area.py && git commit -m "feat(view): InputArea with multi-line + slash popover float"`

### Task 4.5: Wire InputArea into ScreenCoordinator

**Files:** Modify `llm_code/view/repl/coordinator.py`

- [ ] **Step 1: Refactor coordinator._build_layout().**

Replace the M3 placeholder layout with one that uses `InputArea` + `FloatContainer` so the popover Float has a host:

```python
from prompt_toolkit.layout import FloatContainer
from llm_code.view.repl.components.input_area import InputArea
from llm_code.view.repl.history import PromptHistory, default_history_path
from llm_code.view.repl.keybindings import build_keybindings

class ScreenCoordinator:
    def __init__(self, *, console=None):
        ...  # existing init
        self._history = PromptHistory(path=default_history_path())
        self._input_area = InputArea()
        # Remove the M3 _input_buffer attribute — now managed by InputArea
        self._key_bindings = build_keybindings(
            input_buffer=self._input_area.buffer,
            history=self._history,
            on_submit=self._handle_submit,
            on_exit=self.request_exit,
            on_voice_toggle=None,  # M9 wires this
        )

    def _handle_submit(self, text: str) -> None:
        """Called by the Enter keybinding with the submitted text."""
        self._history.add(text)
        if self._input_callback is not None:
            import asyncio
            asyncio.create_task(self._invoke_callback(text))

    def _build_layout(self) -> Layout:
        status_window = Window(
            FormattedTextControl(self._status_text),
            height=1,
            style="class:status",
        )
        input_window = self._input_area.build_window()
        popover_float = self._input_area.build_popover_float()
        return Layout(
            FloatContainer(
                content=HSplit([status_window, input_window]),
                floats=[popover_float],
            )
        )
```

- [ ] **Step 2: Delete the old M3 keybindings from `__init__`** (the three inline `@self._key_bindings.add(...)` for c-d, c-c, enter) — they're now in `build_keybindings()`.
- [ ] **Step 3: Adjust the input_buffer property** — add a property that forwards to `self._input_area.buffer` so existing callers continue working during the transition.
- [ ] **Step 4: Run coordinator + pilot tests** — `pytest tests/test_view/test_coordinator.py tests/test_view/test_pilot.py -v` → all pass.
- [ ] **Step 5: Commit** — `git commit -am "feat(view): coordinator wires InputArea + KeyBindings factory"`

### Task 4.6: Transliterate input tests

**Files:** Create `tests/test_view/test_input_area.py`, `tests/test_view/test_slash_popover.py`

This task transliterates the existing `tests/test_tui/test_input_bar.py` (which is test-heavy, ~40 tests) and the prompt_history e2e coverage. The **transliteration pattern** for every test:

| Old (Textual) | New (REPL) |
|---|---|
| `async def test_X(pilot_app)` | `async def test_X(repl_pilot)` |
| `app = pilot_app` | `backend = repl_pilot.backend` |
| `bar = app.query_one(InputBar)` | `input_area = backend.coordinator._input_area` |
| `bar.value = "text"` | `input_area.buffer.text = "text"` |
| `bar._cursor = N` | `input_area.buffer.cursor_position = N` |
| `await pilot.press("ctrl+up")` | Use a PT `create_pipe_input` helper (see Step 2 below) |
| `assert bar.value == "recalled"` | `assert input_area.buffer.text == "recalled"` |

- [ ] **Step 1: Add a `press()` helper to `RealREPLPilot` in conftest.py.**

```python
async def press(self, key_name: str) -> None:
    """Feed a key to the backend's buffer as if typed by the user.

    key_name is prompt_toolkit key name ('enter', 'ctrl+up', 's-enter', 'c-d').
    Resolves the matching binding from coordinator._key_bindings and
    invokes its handler with a minimal fake event.
    """
    from prompt_toolkit.key_binding.key_processor import KeyPress
    from prompt_toolkit.keys import ALL_KEYS
    # Find matching binding
    kb = self.backend.coordinator._key_bindings
    key_press = KeyPress(key_name)
    for binding in kb.bindings:
        if binding.keys == (key_press.key,):
            # Build a minimal fake event
            fake_event = type("E", (), {
                "app": type("A", (), {
                    "invalidate": lambda self: None,
                    "exit": lambda self: None,
                })(),
            })()
            binding.handler(fake_event)
            return
    raise AssertionError(f"no binding found for {key_name!r}")

async def type(self, text: str) -> None:
    self.backend.coordinator._input_area.buffer.insert_text(text)
```

- [ ] **Step 2: Write `test_input_area.py`** — transliterate test_input_bar.py tests using the table above. Key tests to include (representative set; the full transliteration copies all ~40):

```python
@pytest.mark.asyncio
async def test_enter_submits_buffer_to_handler(repl_pilot):
    received: list[str] = []
    async def handler(text: str) -> None:
        received.append(text)
    repl_pilot.backend.set_input_handler(handler)

    input_area = repl_pilot.backend.coordinator._input_area
    input_area.buffer.insert_text("hello")
    await repl_pilot.press("enter")
    # Submit is async — yield to the event loop briefly
    await asyncio.sleep(0.05)
    assert received == ["hello"]
    assert input_area.buffer.text == ""  # buffer cleared

@pytest.mark.asyncio
async def test_shift_enter_inserts_newline(repl_pilot):
    input_area = repl_pilot.backend.coordinator._input_area
    input_area.buffer.insert_text("line1")
    await repl_pilot.press("s-enter")
    input_area.buffer.insert_text("line2")
    assert input_area.buffer.text == "line1\nline2"

@pytest.mark.asyncio
async def test_ctrl_u_clears_line(repl_pilot):
    input_area = repl_pilot.backend.coordinator._input_area
    input_area.buffer.insert_text("partial")
    await repl_pilot.press("c-u")
    assert input_area.buffer.text == ""

@pytest.mark.asyncio
async def test_ctrl_up_recalls_previous_history(repl_pilot, tmp_path):
    coord = repl_pilot.backend.coordinator
    from llm_code.view.repl.history import PromptHistory
    coord._history = PromptHistory(path=tmp_path / "history.txt")
    coord._history.add("earlier")
    coord._history.add("latest")
    # Rebuild keybindings with the fresh history
    coord._key_bindings = __import__(
        "llm_code.view.repl.keybindings", fromlist=["build_keybindings"]
    ).build_keybindings(
        input_buffer=coord._input_area.buffer,
        history=coord._history,
        on_submit=coord._handle_submit,
        on_exit=coord.request_exit,
    )

    await repl_pilot.press("c-up")
    assert coord._input_area.buffer.text == "latest"
    await repl_pilot.press("c-up")
    assert coord._input_area.buffer.text == "earlier"

@pytest.mark.asyncio
async def test_bare_up_does_not_recall_history(repl_pilot, tmp_path):
    """Regression guard against v1.x wheel → up → history collision."""
    coord = repl_pilot.backend.coordinator
    from llm_code.view.repl.history import PromptHistory
    coord._history = PromptHistory(path=tmp_path / "h.txt")
    coord._history.add("should-not-recall")

    # Bare 'up' has no binding → InputArea's PT default takes it
    # (cursor-to-first-line on multiline; no-op on single-line).
    # Either way, history must not be touched.
    assert coord._input_area.buffer.text == ""
    assert not coord._history.is_navigating()

@pytest.mark.asyncio
async def test_ctrl_d_on_empty_input_exits(repl_pilot):
    exit_called = []
    repl_pilot.backend.coordinator.request_exit = lambda: exit_called.append(True)
    await repl_pilot.press("c-d")
    assert exit_called == [True]

@pytest.mark.asyncio
async def test_ctrl_d_on_non_empty_input_does_not_exit(repl_pilot):
    exit_called = []
    repl_pilot.backend.coordinator.request_exit = lambda: exit_called.append(True)
    repl_pilot.backend.coordinator._input_area.buffer.insert_text("typed")
    await repl_pilot.press("c-d")
    assert exit_called == []
```

Plus the remaining transliterated tests covering: Esc cancel, empty-enter no-op, multi-line submit strips trailing whitespace, slash popover triggers on `/`, Tab accepts completion, history draft restore.

- [ ] **Step 3: Write `test_slash_popover.py`.**

```python
import pytest
from prompt_toolkit.document import Document
from prompt_toolkit.completion import CompleteEvent

from llm_code.view.repl.components.slash_popover import SlashCompleter


def _completions(completer, text):
    doc = Document(text=text, cursor_position=len(text))
    return list(completer.get_completions(doc, CompleteEvent()))

def test_no_completions_for_non_slash_text():
    c = SlashCompleter()
    assert _completions(c, "hello") == []

def test_completions_for_slash_prefix():
    c = SlashCompleter()
    results = _completions(c, "/")
    assert len(results) > 0
    assert all(comp.text.startswith("/") for comp in results)

def test_completions_filtered_by_prefix():
    c = SlashCompleter()
    results = _completions(c, "/voi")
    names = [comp.text for comp in results]
    assert "/voice" in names

def test_completions_include_description():
    c = SlashCompleter()
    results = _completions(c, "/")
    with_desc = [r for r in results if r.display_meta]
    assert len(with_desc) > 0

def test_refresh_rescans_registry():
    c = SlashCompleter()
    original_count = len(c._entries)
    c.refresh()
    assert len(c._entries) == original_count
```

Plus tests for: dropdown hidden on empty buffer, Tab accept does not submit, Esc dismisses dropdown, Ctrl+↑ exits dropdown and runs history recall.

- [ ] **Step 4: Run** — `pytest tests/test_view/test_input_area.py tests/test_view/test_slash_popover.py -v` → ~60 tests pass.
- [ ] **Step 5: Commit** — `git add tests/test_view/test_input_area.py tests/test_view/test_slash_popover.py && git commit -m "test(view): transliterate input_bar + slash popover coverage"`

---

## Milestone completion criteria

- ✅ `llm_code/view/repl/history.py`, `keybindings.py`, `components/input_area.py`, `components/slash_popover.py` all exist and import cleanly
- ✅ Coordinator uses `InputArea` + `FloatContainer` layout
- ✅ Bare ↑/↓ do NOT recall history; Ctrl+↑/↓ do
- ✅ `/` triggers slash popover; Tab accepts; Esc dismisses
- ✅ ~60 new tests in `test_input_area.py` + `test_slash_popover.py`, all green
- ✅ Coordinator and pilot tests from M3 still pass

## Estimated effort: ~4 hours

## Next milestone: M5 — Status Line (`m5-status-line.md`)

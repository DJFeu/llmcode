"""InputBar — fixed bottom input with prompt, multiline, slash autocomplete."""
from __future__ import annotations

import os
from pathlib import Path

from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.app import RenderResult
from rich.text import Text

from llm_code.cli.commands import COMMAND_REGISTRY
from llm_code.tui.keybindings import load_keybindings

# Derived from the single-source registry in commands.py
SLASH_COMMANDS = sorted(f"/{c.name}" for c in COMMAND_REGISTRY)

# Commands that execute immediately (no arguments needed)
_NO_ARG_COMMANDS = frozenset(f"/{c.name}" for c in COMMAND_REGISTRY if c.no_arg)

SLASH_COMMAND_DESCS: list[tuple[str, str]] = [
    (f"/{c.name}", c.description) for c in COMMAND_REGISTRY
]


class InputBar(Widget):
    """Bottom input bar: ❯ {text}"""

    can_focus = True

    PROMPT = "❯ "

    DEFAULT_CSS = """
    InputBar {
        dock: bottom;
        height: auto;
        min-height: 3;
        max-height: 30;
        padding: 0 1;
        background: $surface;
    }
    InputBar:focus {
        border-top: solid $accent;
    }
    """

    value: reactive[str] = reactive("")
    disabled: reactive[bool] = reactive(False)
    vim_mode: reactive[str] = reactive("")  # "" | "NORMAL" | "INSERT"
    pending_image_count: reactive[int] = reactive(0)

    _show_dropdown: bool = False
    _dropdown_items: list[tuple[str, str]] = []
    _dropdown_cursor: int = 0

    def __init__(self) -> None:
        super().__init__()
        self._vim_engine = None
        self._cursor = 0  # cursor position within self.value
        self._show_dropdown = False
        self._dropdown_items = []
        self._dropdown_cursor = 0
        self._keybindings = load_keybindings(Path.home() / ".llmcode" / "keybindings.json")

    class Submitted(Message):
        """Fired when user presses Enter."""
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    class Cancelled(Message):
        """Fired when user presses Escape during generation."""
        pass

    def watch_vim_mode(self) -> None:
        if self.vim_mode:
            from llm_code.vim.engine import VimEngine
            if self._vim_engine is None:
                self._vim_engine = VimEngine(self.value)
        else:
            self._vim_engine = None
        self.refresh()

    # Pink color matching Claude Code's image indicator
    _IMAGE_STYLE = "bold #e05880"
    _IMAGE_MARKER = "\x00IMG\x00"  # sentinel in value text

    def insert_text(self, text: str) -> None:
        """Insert arbitrary text at current cursor position."""
        if not text:
            return
        self.value = self.value[:self._cursor] + text + self.value[self._cursor:]
        self._cursor += len(text)

    def insert_image_marker(self) -> None:
        """Insert an [image] marker at current cursor position."""
        self.value = self.value[:self._cursor] + self._IMAGE_MARKER + self.value[self._cursor:]
        self._cursor += len(self._IMAGE_MARKER)
        self.pending_image_count += 1

    def _update_dropdown(self) -> None:
        """Recompute dropdown items based on current value."""
        was_showing = self._show_dropdown
        if self.value.startswith("/") and " " not in self.value:
            query = self.value
            self._dropdown_items = [
                (cmd, desc) for cmd, desc in SLASH_COMMAND_DESCS if cmd.startswith(query)
            ]
            self._dropdown_cursor = min(self._dropdown_cursor, max(0, len(self._dropdown_items) - 1))
            self._show_dropdown = len(self._dropdown_items) > 0
        else:
            self._dropdown_items = []
            self._dropdown_cursor = 0
            self._show_dropdown = False
        # Trigger relayout when dropdown visibility or item count changes
        if self._show_dropdown != was_showing:
            self.refresh(layout=True)

    def render(self) -> RenderResult:
        text = Text()
        # Render dropdown above prompt when active
        if self._show_dropdown and self._dropdown_items:
            max_visible = 12
            total = len(self._dropdown_items)
            # Sliding window: keep cursor visible within the window
            if total <= max_visible:
                start = 0
            else:
                start = max(0, min(self._dropdown_cursor - max_visible + 1, total - max_visible))
            end = min(start + max_visible, total)
            for i in range(start, end):
                cmd, desc = self._dropdown_items[i]
                if i == self._dropdown_cursor:
                    text.append(f"  > {cmd:<20s} {desc}\n", style="bold white on #3a3a5a")
                else:
                    text.append(f"    {cmd:<20s} {desc}\n", style="dim")
            # Scroll indicators
            if start > 0:
                text.append("")  # handled by items above
            if end < total:
                text.append(f"    ↓ {total - end} more\n", style="dim italic")
        if self.vim_mode == "NORMAL":
            text.append("[N] ", style="yellow bold")
        elif self.vim_mode == "INSERT":
            text.append("[I] ", style="green bold")
        # Leading image count (for images added before any text)
        if self.pending_image_count > 0 and self._IMAGE_MARKER not in self.value:
            n = self.pending_image_count
            label = f"{n} image{'s' if n > 1 else ''}"
            text.append(f"[{label}] ", style=self._IMAGE_STYLE)
        text.append(self.PROMPT, style="bold cyan")
        if self.disabled:
            text.append("generating…", style="dim italic")
        else:
            # Render value with cursor at _cursor position
            val = self.value
            cur = min(self._cursor, len(val))
            before = val[:cur]
            after = val[cur:]
            # Render before cursor
            self._render_with_markers(text, before)
            # Cursor block
            if after:
                # Show character at cursor with highlight
                if after.startswith(self._IMAGE_MARKER):
                    text.append("[image]", style=f"{self._IMAGE_STYLE} reverse")
                    after = after[len(self._IMAGE_MARKER):]
                else:
                    text.append(after[0], style="reverse")
                    after = after[1:]
                self._render_with_markers(text, after)
            else:
                text.append("█", style="dim")
        return text

    def _render_with_markers(self, text: Text, s: str) -> None:
        """Render string with [image] markers styled in pink."""
        parts = s.split(self._IMAGE_MARKER)
        for i, part in enumerate(parts):
            if i > 0:
                text.append("[image] ", style=self._IMAGE_STYLE)
            if part:
                text.append(part)

    def get_clean_value(self) -> str:
        """Return value with image markers stripped (for display in chat)."""
        return self.value.replace(self._IMAGE_MARKER, "").strip()

    def on_key(self, event: events.Key) -> None:
        # Scroll keys — dispatch directly to App actions since event
        # bubbling doesn't reliably reach App bindings from InputBar.
        if event.key in ("shift+up", "pageup"):
            self.app.action_scroll_chat_up()
            event.stop()
            return
        if event.key in ("shift+down", "pagedown"):
            self.app.action_scroll_chat_down()
            event.stop()
            return
        if self.disabled:
            if event.key == "escape":
                self.post_message(self.Cancelled())
            return

        # Dropdown navigation (when dropdown is visible)
        if self._show_dropdown and self._dropdown_items:
            if event.key == "up":
                self._dropdown_cursor = (self._dropdown_cursor - 1) % len(self._dropdown_items)
                self.refresh()
                event.prevent_default()
                event.stop()
                return
            elif event.key == "down":
                self._dropdown_cursor = (self._dropdown_cursor + 1) % len(self._dropdown_items)
                self.refresh()
                event.prevent_default()
                event.stop()
                return
            elif event.key in ("enter", "tab"):
                selected_cmd = self._dropdown_items[self._dropdown_cursor][0]
                self._show_dropdown = False
                self._dropdown_items = []
                self._dropdown_cursor = 0
                if selected_cmd in _NO_ARG_COMMANDS:
                    # Execute immediately
                    self.value = selected_cmd
                    self.post_message(self.Submitted(selected_cmd))
                    self.value = ""
                    self._cursor = 0
                else:
                    # Fill and wait for argument
                    self.value = selected_cmd + " "
                    self._cursor = len(self.value)
                self.refresh(layout=True)
                return
            elif event.key == "escape":
                self._show_dropdown = False
                self._dropdown_items = []
                self._dropdown_cursor = 0
                self.refresh(layout=True)
                return

        # Tab autocomplete (before vim routing) — fallback when dropdown not shown
        if event.key == "tab" and self.value.startswith("/"):
            matches = [c for c in SLASH_COMMANDS if c.startswith(self.value)]
            if len(matches) == 1:
                self.value = matches[0] + " "
                self._cursor = len(self.value)
            elif matches:
                prefix = os.path.commonprefix(matches)
                if len(prefix) > len(self.value):
                    self.value = prefix
                    self._cursor = len(self.value)
            return

        # Vim mode routing
        if self._vim_engine is not None:
            from llm_code.vim.types import VimMode
            key_str = event.key if len(event.key) > 1 else (event.character or event.key)
            self._vim_engine.feed_key(key_str)
            self.value = self._vim_engine.buffer
            # Update mode display
            self.vim_mode = "NORMAL" if self._vim_engine.mode == VimMode.NORMAL else "INSERT"
            # Handle enter in insert mode for submission
            if event.key == "enter" and self._vim_engine.mode == VimMode.INSERT:
                if self.value.strip():
                    self.post_message(self.Submitted(self.value))
                    self.value = ""
                    self._vim_engine.set_buffer("")
            return

        # Normal (non-vim) key handling — table lookup
        chord_action = self._keybindings.chord_state.feed(event.key)
        if chord_action is not None:
            self._handle_action(chord_action)
            return
        if self._keybindings.chord_state.pending is not None:
            return

        action = self._keybindings.get_action(event.key)
        if action:
            self._handle_action(action)
        elif event.character and len(event.character) == 1:
            # Update cursor BEFORE setting self.value — assigning to a reactive
            # synchronously fires watch_value() which calls _update_dropdown +
            # _recompute_height + refresh(layout=True). If cursor is still the
            # old value at that point, the reflow can desync cursor vs text and
            # the next keystroke gets inserted at the wrong position.
            cur = min(self._cursor, len(self.value))
            new_value = self.value[:cur] + event.character + self.value[cur:]
            self._cursor = cur + 1
            self.value = new_value
            event.prevent_default()
            event.stop()

    def _handle_action(self, action: str) -> None:
        """Execute a named keybinding action."""
        if action == "submit":
            if self.value.strip():
                self.post_message(self.Submitted(self.value))
                self.value = ""
                self._cursor = 0
        elif action == "newline":
            self.value = self.value[:self._cursor] + "\n" + self.value[self._cursor:]
            self._cursor += 1
        elif action == "delete_back":
            if self._cursor > 0:
                self.value = self.value[:self._cursor - 1] + self.value[self._cursor:]
                self._cursor -= 1
        elif action == "delete_forward":
            if self._cursor < len(self.value):
                self.value = self.value[:self._cursor] + self.value[self._cursor + 1:]
        elif action == "cursor_left":
            if self._cursor > 0:
                self._cursor -= 1
                self.refresh()
        elif action == "cursor_right":
            if self._cursor < len(self.value):
                self._cursor += 1
                self.refresh()
        elif action == "cursor_home":
            self._cursor = 0
            self.refresh()
        elif action == "cursor_end":
            self._cursor = len(self.value)
            self.refresh()
        elif action == "cancel":
            self.value = ""
            self._cursor = 0
            self.post_message(self.Cancelled())

    def watch_value(self) -> None:
        # Keep cursor in bounds
        if self._cursor > len(self.value):
            self._cursor = len(self.value)
        self._update_dropdown()
        self._recompute_height()

    def _recompute_height(self) -> None:
        """Recalculate height based on input lines + dropdown rows.

        height: auto in CSS doesn't update in time for reactive value changes,
        so we set height explicitly here.
        """
        line_count = self.value.count("\n") + 1
        # Dropdown rows: count visible items, capped at 12 (matching render)
        dropdown_rows = 0
        if self._show_dropdown and self._dropdown_items:
            dropdown_rows = min(len(self._dropdown_items), 12)
            # +1 if there's a "↓ N more" line
            if len(self._dropdown_items) > 12:
                dropdown_rows += 1
        # +2 for prompt line padding, max 30 to fit reasonable terminals
        total = line_count + dropdown_rows + 2
        self.styles.height = max(3, min(total, 30))
        self.refresh(layout=True)

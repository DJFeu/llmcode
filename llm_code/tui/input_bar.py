"""InputBar — fixed bottom input with prompt, multiline, slash autocomplete."""
from __future__ import annotations

import os

from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.app import RenderResult
from rich.text import Text

SLASH_COMMANDS = sorted([
    "/help", "/clear", "/exit", "/quit", "/model", "/cost", "/budget",
    "/undo", "/cd", "/config", "/thinking", "/vim", "/image", "/search",
    "/index", "/session", "/skill", "/plugin", "/mcp", "/memory",
    "/lsp", "/cancel", "/cron", "/task", "/swarm", "/voice", "/ide",
    "/vcr", "/hida", "/checkpoint",
])


class InputBar(Widget):
    """Bottom input bar: ❯ {text}"""

    can_focus = True

    PROMPT = "❯ "

    DEFAULT_CSS = """
    InputBar {
        dock: bottom;
        height: auto;
        min-height: 3;
        max-height: 8;
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

    def __init__(self) -> None:
        super().__init__()
        self._vim_engine = None
        self._cursor = 0  # cursor position within self.value

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

    def insert_image_marker(self) -> None:
        """Insert an [image] marker at current cursor position."""
        self.value = self.value[:self._cursor] + self._IMAGE_MARKER + self.value[self._cursor:]
        self._cursor += len(self._IMAGE_MARKER)
        self.pending_image_count += 1

    def render(self) -> RenderResult:
        text = Text()
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
        if self.disabled:
            if event.key == "escape":
                self.post_message(self.Cancelled())
            return

        # Tab autocomplete (before vim routing)
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

        # Normal (non-vim) key handling
        if event.key == "enter":
            if self.value.strip():
                self.post_message(self.Submitted(self.value))
                self.value = ""
                self._cursor = 0
        elif event.key == "shift+enter":
            self.value = self.value[:self._cursor] + "\n" + self.value[self._cursor:]
            self._cursor += 1
        elif event.key == "backspace":
            if self._cursor > 0:
                self.value = self.value[:self._cursor - 1] + self.value[self._cursor:]
                self._cursor -= 1
        elif event.key == "delete":
            if self._cursor < len(self.value):
                self.value = self.value[:self._cursor] + self.value[self._cursor + 1:]
        elif event.key == "left":
            if self._cursor > 0:
                self._cursor -= 1
                self.refresh()
        elif event.key == "right":
            if self._cursor < len(self.value):
                self._cursor += 1
                self.refresh()
        elif event.key == "home":
            self._cursor = 0
            self.refresh()
        elif event.key == "end":
            self._cursor = len(self.value)
            self.refresh()
        elif event.key == "escape":
            self.value = ""
            self._cursor = 0
            self.post_message(self.Cancelled())
        elif event.character and len(event.character) == 1:
            self.value = self.value[:self._cursor] + event.character + self.value[self._cursor:]
            self._cursor += 1

    def watch_value(self) -> None:
        # Keep cursor in bounds
        if self._cursor > len(self.value):
            self._cursor = len(self.value)
        self.refresh()

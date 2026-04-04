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

    def __init__(self) -> None:
        super().__init__()
        self._vim_engine = None

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

    def render(self) -> RenderResult:
        text = Text()
        if self.vim_mode == "NORMAL":
            text.append("[N] ", style="yellow bold")
        elif self.vim_mode == "INSERT":
            text.append("[I] ", style="green bold")
        text.append(self.PROMPT, style="bold cyan")
        if self.disabled:
            text.append("generating…", style="dim italic")
        else:
            text.append(self.value)
            text.append("█", style="dim")  # cursor
        return text

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
            elif matches:
                # Find common prefix
                prefix = os.path.commonprefix(matches)
                if len(prefix) > len(self.value):
                    self.value = prefix
            return  # Don't add tab character

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
        elif event.key == "shift+enter":
            self.value += "\n"
        elif event.key == "backspace":
            self.value = self.value[:-1]
        elif event.key == "escape":
            self.value = ""
            self.post_message(self.Cancelled())
        elif event.character and len(event.character) == 1:
            self.value += event.character

    def watch_value(self) -> None:
        self.refresh()

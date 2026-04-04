"""InputBar — fixed bottom input with prompt, multiline, slash autocomplete."""
from __future__ import annotations

from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.app import RenderResult
from rich.text import Text


class InputBar(Widget):
    """Bottom input bar: ❯ {text}"""

    PROMPT = "❯ "

    DEFAULT_CSS = """
    InputBar {
        dock: bottom;
        height: auto;
        min-height: 1;
        max-height: 8;
        padding: 0 1;
    }
    """

    value: reactive[str] = reactive("")
    disabled: reactive[bool] = reactive(False)
    vim_mode: reactive[str] = reactive("")  # "" | "NORMAL" | "INSERT"

    class Submitted(Message):
        """Fired when user presses Enter."""
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    class Cancelled(Message):
        """Fired when user presses Escape during generation."""
        pass

    def render(self) -> RenderResult:
        text = Text()
        if self.vim_mode == "NORMAL":
            text.append("[N] ", style="yellow bold")
        text.append(self.PROMPT, style="bold cyan")
        if self.disabled:
            text.append("", style="dim")
        else:
            text.append(self.value)
            text.append("█", style="dim")  # cursor
        return text

    def on_key(self, event: events.Key) -> None:
        if self.disabled:
            if event.key == "escape":
                self.post_message(self.Cancelled())
            return

        if event.key == "enter":
            if self.value.strip():
                self.post_message(self.Submitted(self.value))
                self.value = ""
        elif event.key == "shift+enter":
            self.value += "\n"
        elif event.key == "backspace":
            self.value = self.value[:-1]
        elif event.key == "escape":
            self.post_message(self.Cancelled())
        elif event.character and len(event.character) == 1:
            self.value += event.character

    def watch_value(self) -> None:
        self.refresh()

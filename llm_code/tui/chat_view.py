# llm_code/tui/chat_view.py
"""ChatScrollView — scrollable container for chat entries."""
from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.app import RenderResult
from rich.text import Text


class UserMessage(Widget):
    """Renders a user input line: ❯ text"""

    DEFAULT_CSS = "UserMessage { height: auto; margin: 1 0 0 0; }"

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text

    def render(self) -> RenderResult:
        t = Text()
        t.append("❯ ", style="bold cyan")
        t.append(self._text)
        return t


class AssistantText(Widget):
    """Renders assistant response text."""

    DEFAULT_CSS = "AssistantText { height: auto; }"

    def __init__(self, text: str = "") -> None:
        super().__init__()
        self._text = text

    def append_text(self, new_text: str) -> None:
        self._text += new_text
        self.refresh()

    def render(self) -> RenderResult:
        return Text(self._text)


class ChatScrollView(VerticalScroll):
    """Scrollable chat area that auto-scrolls to bottom on new content."""

    DEFAULT_CSS = """
    ChatScrollView {
        height: 1fr;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._auto_scroll = True

    def on_mount(self) -> None:
        self.scroll_end(animate=False)

    def add_entry(self, widget: Widget) -> None:
        self.mount(widget)
        if self._auto_scroll:
            self.scroll_end(animate=False)

    def on_scroll_up(self) -> None:
        self._auto_scroll = False

    def pause_auto_scroll(self) -> None:
        """Disable auto-scroll (e.g. when user pages up to read history)."""
        self._auto_scroll = False

    def resume_auto_scroll(self) -> None:
        self._auto_scroll = True
        self.scroll_end(animate=False)

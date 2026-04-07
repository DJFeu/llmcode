# llm_code/tui/chat_view.py
"""ChatScrollView — scrollable container for chat entries."""
from __future__ import annotations

import re

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


def _styled_text(raw: str) -> Text:
    """Convert markdown text to Rich Text with inline styles.

    Hardened renderer:
      - **bold** / `code` / # headings
      - ~~strike~~ rendered as plain text (no strikethrough)
      - [label](url) → cyan + link
      - bare https?://... → cyan + link
      - > blockquote → indent + dim
      - triple-backtick fence → dim bg with language label
    Uses plain Text so terminal text selection still works.
    """
    # Process fenced code blocks first by splitting them out, render the
    # remainder line-by-line for blockquotes, then run the inline regex.
    t = Text()
    fence_re = re.compile(r"^```([a-zA-Z0-9_+-]*)\n(.*?)(?:^```\s*$)", re.MULTILINE | re.DOTALL)
    pos = 0
    for m in fence_re.finditer(raw):
        if m.start() > pos:
            _render_block(raw[pos:m.start()], t)
        lang = m.group(1) or ""
        code = m.group(2)
        if lang:
            t.append(f"⌐ {lang}\n", style="dim italic")
        for line in code.splitlines():
            t.append(line + "\n", style="white on grey11")
        pos = m.end()
    if pos < len(raw):
        _render_block(raw[pos:], t)
    return t


def _render_block(raw: str, t: Text) -> None:
    """Render a non-fenced text block into `t` line-by-line."""
    for line in raw.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if stripped.startswith("> "):
            t.append("  ", style="dim")
            _render_inline(stripped[2:], t, base_style="dim italic")
            t.append("\n")
        else:
            _render_inline(stripped, t)
            if line.endswith("\n"):
                t.append("\n")


_INLINE_RE = re.compile(
    r"\*\*(.+?)\*\*"                                       # 1: bold
    r"|`([^`]+)`"                                          # 2: code
    r"|^(#{1,3})\s+(.+)$"                                  # 3,4: heading
    r"|~~(.+?)~~"                                          # 5: strike (plain)
    r"|\[([^\]]+)\]\(([^)]+)\)"                            # 6,7: link
    r"|(https?://[^\s<>\)\]]+)"                            # 8: bare url
    , re.MULTILINE,
)


def _render_inline(raw: str, t: Text, base_style: str = "") -> None:
    pos = 0
    for m in _INLINE_RE.finditer(raw):
        if m.start() > pos:
            t.append(raw[pos:m.start()], style=base_style)
        if m.group(1) is not None:
            t.append(m.group(1), style=f"bold {base_style}".strip())
        elif m.group(2) is not None:
            t.append(m.group(2), style="bold cyan")
        elif m.group(4) is not None:
            t.append(m.group(4), style="bold underline")
        elif m.group(5) is not None:
            t.append(m.group(5), style=base_style)  # strikethrough disabled
        elif m.group(6) is not None:
            label, url = m.group(6), m.group(7)
            t.append(label, style=f"cyan underline link {url}")
        elif m.group(8) is not None:
            url = m.group(8)
            t.append(url, style=f"cyan underline link {url}")
        pos = m.end()
    if pos < len(raw):
        t.append(raw[pos:], style=base_style)


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
        return _styled_text(self._text)


class SkillBadge(Widget):
    """Coloured banner showing which auto-skills the router activated."""

    DEFAULT_CSS = "SkillBadge { height: auto; }"

    def __init__(self, skills: list[str]) -> None:
        super().__init__()
        self._skills = skills

    def render(self) -> RenderResult:
        t = Text()
        t.append("⚡ ", style="bold yellow")
        t.append("Skills", style="bold #d7af00")
        t.append(": ", style="dim")
        for i, name in enumerate(self._skills):
            if i > 0:
                t.append(", ", style="dim")
            t.append(name, style="bold #5fafff")
        return t


class ChatScrollView(VerticalScroll):
    """Scrollable chat area that auto-scrolls to bottom on new content."""

    DEFAULT_CSS = """
    ChatScrollView {
        height: 1fr;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
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

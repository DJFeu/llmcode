# llm_code/tui/chat_view.py
"""ChatScrollView — scrollable container for chat entries."""
from __future__ import annotations

import logging
import re
import webbrowser
from dataclasses import dataclass

from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.app import RenderResult
from rich.cells import cell_len
from rich.text import Text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinkRegion:
    """A clickable region in rendered text mapping cell coords to a URL."""

    row: int
    col_start: int
    col_end: int
    url: str

    def contains(self, x: int, y: int) -> bool:
        return y == self.row and self.col_start <= x < self.col_end


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


class _RenderState:
    """Mutable state shared across the markdown render helpers.

    Tracks the current cursor row/column (in terminal cells, NOT chars) so
    we can record `LinkRegion`s as they are emitted.
    """

    def __init__(self) -> None:
        self.text = Text()
        self.regions: list[LinkRegion] = []
        self.row = 0
        self.col = 0

    def append(self, fragment: str, style: str = "") -> None:
        if not fragment:
            return
        self.text.append(fragment, style=style)
        # Update cursor accounting line by line.
        parts = fragment.split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                self.row += 1
                self.col = 0
            self.col += cell_len(part)

    def append_link(self, label: str, url: str, style: str) -> None:
        start_col = self.col
        start_row = self.row
        self.append(label, style=style)
        # Links shouldn't span newlines in our markdown, but guard anyway.
        if self.row == start_row:
            self.regions.append(
                LinkRegion(row=start_row, col_start=start_col, col_end=self.col, url=url)
            )


def _styled_text(raw: str) -> Text:
    """Convert markdown text to Rich Text with inline styles.

    See `_styled_text_with_regions` for the version that also returns
    clickable link regions. This thin wrapper preserves the original API
    for callers that only need the rendered Text.
    """
    text, _ = _styled_text_with_regions(raw)
    return text


def _styled_text_with_regions(raw: str) -> tuple[Text, tuple[LinkRegion, ...]]:
    """Render markdown to Rich Text and collect clickable link regions.

    Hardened renderer:
      - **bold** / `code` / # headings
      - ~~strike~~ rendered as plain text (no strikethrough)
      - [label](url) → cyan + link (clickable region recorded)
      - bare https?://... → cyan + link (clickable region recorded)
      - > blockquote → indent + dim
      - triple-backtick fence → dim bg with language label
    Uses plain Text so terminal text selection still works.
    """
    state = _RenderState()
    fence_re = re.compile(r"^```([a-zA-Z0-9_+-]*)\n(.*?)(?:^```\s*$)", re.MULTILINE | re.DOTALL)
    pos = 0
    for m in fence_re.finditer(raw):
        if m.start() > pos:
            _render_block(raw[pos:m.start()], state)
        lang = m.group(1) or ""
        code = m.group(2)
        if lang:
            state.append(f"⌐ {lang}\n", style="dim italic")
        for line in code.splitlines():
            state.append(line + "\n", style="white on grey11")
        pos = m.end()
    if pos < len(raw):
        _render_block(raw[pos:], state)
    return state.text, tuple(state.regions)


def _render_block(raw: str, state: _RenderState) -> None:
    """Render a non-fenced text block into `state` line-by-line."""
    for line in raw.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if stripped.startswith("> "):
            state.append("  ", style="dim")
            _render_inline(stripped[2:], state, base_style="dim italic")
            state.append("\n")
        else:
            _render_inline(stripped, state)
            if line.endswith("\n"):
                state.append("\n")


_INLINE_RE = re.compile(
    r"\*\*(.+?)\*\*"                                       # 1: bold
    r"|`([^`]+)`"                                          # 2: code
    r"|^(#{1,3})\s+(.+)$"                                  # 3,4: heading
    r"|~~(.+?)~~"                                          # 5: strike (plain)
    r"|\[([^\]]+)\]\(([^)]+)\)"                            # 6,7: link
    r"|(https?://[^\s<>\)\]]+)"                            # 8: bare url
    , re.MULTILINE,
)


def _render_inline(raw: str, state: _RenderState, base_style: str = "") -> None:
    pos = 0
    for m in _INLINE_RE.finditer(raw):
        if m.start() > pos:
            state.append(raw[pos:m.start()], style=base_style)
        if m.group(1) is not None:
            state.append(m.group(1), style=f"bold {base_style}".strip())
        elif m.group(2) is not None:
            state.append(m.group(2), style="bold cyan")
        elif m.group(4) is not None:
            state.append(m.group(4), style="bold underline")
        elif m.group(5) is not None:
            state.append(m.group(5), style=base_style)  # strikethrough disabled
        elif m.group(6) is not None:
            label, url = m.group(6), m.group(7)
            state.append_link(label, url, style=f"cyan underline link {url}")
        elif m.group(8) is not None:
            url = m.group(8)
            state.append_link(url, url, style=f"cyan underline link {url}")
        pos = m.end()
    if pos < len(raw):
        state.append(raw[pos:], style=base_style)


class AssistantText(Widget):
    """Renders assistant response text with clickable markdown links."""

    DEFAULT_CSS = "AssistantText { height: auto; }"

    def __init__(self, text: str = "") -> None:
        super().__init__()
        self._text = text
        self._link_regions: tuple[LinkRegion, ...] = ()

    def append_text(self, new_text: str) -> None:
        self._text += new_text
        self.refresh()

    def render(self) -> RenderResult:
        text, regions = _styled_text_with_regions(self._text)
        self._link_regions = regions
        return text

    def _find_link(self, x: int, y: int) -> LinkRegion | None:
        for region in self._link_regions:
            if region.contains(x, y):
                return region
        return None

    def on_click(self, event) -> None:  # noqa: ANN001 — Textual event type
        region = self._find_link(event.x, event.y)
        if region is None:
            return  # Let the click bubble up so scroll/select still work.
        try:
            webbrowser.open(region.url)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Failed to open URL %s: %s", region.url, exc)
        event.stop()


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

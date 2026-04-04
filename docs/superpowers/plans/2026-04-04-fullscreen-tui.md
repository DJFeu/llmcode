# Fullscreen TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the default UI with a fullscreen alternate-screen TUI matching Claude Code's visual experience, using Python Textual.

**Architecture:** New `llm_code/tui/` package with 8 files (app + 6 widgets + theme). Shares existing `ConversationRuntime`. Three UI modes: default (Textual fullscreen), `--lite` (existing print CLI), `--ink` (existing React+Ink).

**Tech Stack:** Python Textual >=1.0, existing Rich for markup, existing ConversationRuntime

---

## File Map

| File | Responsibility | New/Modify |
|------|---------------|------------|
| `llm_code/tui/__init__.py` | Package init | New |
| `llm_code/tui/theme.py` | Color constants + Textual CSS string | New |
| `llm_code/tui/header_bar.py` | 1-line top bar: model · project · branch | New |
| `llm_code/tui/status_bar.py` | 1-line bottom bar: model │ tokens │ cost │ hints | New |
| `llm_code/tui/marketplace.py` | Scrollable list for /skill /plugin /mcp browsing | New |
| `llm_code/tui/chat_view.py` | Scrollable chat container, auto-scroll logic | New |
| `llm_code/tui/chat_widgets.py` | ToolBlock, ThinkingBlock, PermissionInline, TurnSummary, SpinnerLine | New |
| `llm_code/tui/input_bar.py` | Fixed bottom input: prompt, multiline, slash autocomplete | New |
| `llm_code/tui/app.py` | Textual App: composes widgets, bridges ConversationRuntime events | New |
| `llm_code/cli/tui_main.py` | Add `--ink` flag; default → Textual; `--lite` → existing | Modify |
| `tests/test_tui/test_theme.py` | Theme constants tests | New |
| `tests/test_tui/test_widgets.py` | Widget unit tests | New |
| `tests/test_tui/test_app.py` | App integration test | New |

---

### Task 1: Theme + Package Init

**Files:**
- Create: `llm_code/tui/__init__.py`
- Create: `llm_code/tui/theme.py`
- Test: `tests/test_tui/test_theme.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui/test_theme.py
"""Tests for TUI theme constants."""
from llm_code.tui.theme import COLORS, APP_CSS


def test_colors_has_required_keys():
    required = {"prompt", "tool_name", "tool_line", "success", "error",
                "diff_add", "diff_del", "thinking", "warning", "spinner", "dim"}
    assert required.issubset(set(COLORS.keys()))


def test_app_css_is_nonempty_string():
    assert isinstance(APP_CSS, str)
    assert len(APP_CSS) > 100


def test_colors_values_are_strings():
    for key, val in COLORS.items():
        assert isinstance(val, str), f"COLORS[{key!r}] should be a string"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tui/test_theme.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm_code.tui'`

- [ ] **Step 3: Write the implementation**

```python
# llm_code/tui/__init__.py
"""Fullscreen TUI package using Textual."""
```

```python
# llm_code/tui/theme.py
"""Color constants and Textual CSS for the fullscreen TUI."""
from __future__ import annotations

# Semantic color map — values are Rich/Textual style strings
COLORS: dict[str, str] = {
    "prompt": "bold cyan",
    "tool_name": "bold cyan",
    "tool_line": "dim",
    "tool_args": "dim",
    "success": "bold green",
    "error": "bold red",
    "diff_add": "green",
    "diff_del": "red",
    "thinking": "dim blue",
    "warning": "yellow",
    "spinner": "blue",
    "dim": "dim",
    "bash_cmd": "white on #2a2a3a",
    "agent": "bold cyan",
    "shortcut_key": "bold",
}

# Textual CSS applied to the App
APP_CSS = """
Screen {
    layout: vertical;
}

#header-bar {
    dock: top;
    height: 1;
    background: $surface-darken-1;
    color: $text-muted;
    padding: 0 1;
}

#chat-view {
    height: 1fr;
    overflow-y: auto;
    padding: 0 1;
}

#input-bar {
    dock: bottom;
    height: auto;
    min-height: 1;
    max-height: 8;
    padding: 0 1;
}

#status-bar {
    dock: bottom;
    height: 1;
    background: $surface-darken-1;
    color: $text-muted;
    padding: 0 1;
}

.tool-block {
    margin: 0 0 0 2;
}

.thinking-collapsed {
    color: $text-muted;
}

.thinking-expanded {
    color: $text-muted;
    border: round $accent;
    padding: 0 1;
    max-height: 20;
    overflow-y: auto;
}

.permission-inline {
    border-left: thick $warning;
    padding: 0 1;
    margin: 0 0 0 2;
}

.turn-summary {
    margin: 0 0 1 0;
}

.spinner-line {
    color: $accent;
}

.user-message {
    margin: 1 0 0 0;
}

.assistant-text {
    margin: 0 0 1 0;
}
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tui/test_theme.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add llm_code/tui/__init__.py llm_code/tui/theme.py tests/test_tui/test_theme.py
git commit -m "feat(tui): add theme constants and Textual CSS"
```

---

### Task 2: HeaderBar + StatusBar Widgets

**Files:**
- Create: `llm_code/tui/header_bar.py`
- Create: `llm_code/tui/status_bar.py`
- Test: `tests/test_tui/test_widgets.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tui/test_widgets.py
"""Tests for TUI widgets."""
from __future__ import annotations

from llm_code.tui.header_bar import HeaderBar
from llm_code.tui.status_bar import StatusBar


class TestHeaderBar:
    def test_creates_with_defaults(self):
        bar = HeaderBar()
        assert bar.model == ""
        assert bar.project == ""
        assert bar.branch == ""

    def test_format_content(self):
        bar = HeaderBar()
        bar.model = "qwen3.5"
        bar.project = "my-project"
        bar.branch = "main"
        content = bar._format_content()
        assert "qwen3.5" in content
        assert "my-project" in content
        assert "main" in content


class TestStatusBar:
    def test_creates_with_defaults(self):
        bar = StatusBar()
        assert bar.tokens == 0
        assert bar.cost == ""

    def test_format_content(self):
        bar = StatusBar()
        bar.model = "qwen3.5"
        bar.tokens = 1234
        bar.cost = "$0.03"
        content = bar._format_content()
        assert "qwen3.5" in content
        assert "1,234" in content
        assert "$0.03" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tui/test_widgets.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: Write HeaderBar**

```python
# llm_code/tui/header_bar.py
"""HeaderBar — single-line top bar showing model, project, branch."""
from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget
from textual.app import RenderResult


class HeaderBar(Widget):
    """Single-line header: llm-code · {model} · {project} · {branch}"""

    model: reactive[str] = reactive("")
    project: reactive[str] = reactive("")
    branch: reactive[str] = reactive("")

    DEFAULT_CSS = """
    HeaderBar {
        dock: top;
        height: 1;
        background: $surface-darken-1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def _format_content(self) -> str:
        parts = ["llm-code"]
        if self.model:
            parts.append(self.model)
        if self.project:
            parts.append(self.project)
        if self.branch:
            parts.append(self.branch)
        return " · ".join(parts)

    def render(self) -> RenderResult:
        return self._format_content()

    def watch_model(self) -> None:
        self.refresh()

    def watch_project(self) -> None:
        self.refresh()

    def watch_branch(self) -> None:
        self.refresh()
```

- [ ] **Step 4: Write StatusBar**

```python
# llm_code/tui/status_bar.py
"""StatusBar — persistent bottom line with model, tokens, cost, hints."""
from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget
from textual.app import RenderResult


class StatusBar(Widget):
    """Bottom status: model │ ↓tokens tok │ $cost │ streaming… │ /help │ Ctrl+D quit"""

    model: reactive[str] = reactive("")
    tokens: reactive[int] = reactive(0)
    cost: reactive[str] = reactive("")
    is_streaming: reactive[bool] = reactive(False)
    vim_mode: reactive[str] = reactive("")  # "" | "NORMAL" | "INSERT"

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def _format_content(self) -> str:
        parts: list[str] = []
        if self.vim_mode:
            parts.append(f"-- {self.vim_mode} --")
        if self.model:
            parts.append(self.model)
        if self.tokens > 0:
            parts.append(f"↓{self.tokens:,} tok")
        if self.cost:
            parts.append(self.cost)
        if self.is_streaming:
            parts.append("streaming…")
        parts.append("/help")
        parts.append("Ctrl+D quit")
        return " │ ".join(parts)

    def render(self) -> RenderResult:
        return self._format_content()
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_tui/test_widgets.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add llm_code/tui/header_bar.py llm_code/tui/status_bar.py tests/test_tui/test_widgets.py
git commit -m "feat(tui): add HeaderBar and StatusBar widgets"
```

---

### Task 3: ChatScrollView + Chat Entry Widgets

**Files:**
- Create: `llm_code/tui/chat_view.py`
- Create: `llm_code/tui/chat_widgets.py`
- Modify: `tests/test_tui/test_widgets.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_tui/test_widgets.py`:

```python
from llm_code.tui.chat_widgets import ToolBlock, ThinkingBlock, TurnSummary, SpinnerLine
from llm_code.tui.chat_view import ChatScrollView


class TestToolBlock:
    def test_format_standard(self):
        block = ToolBlock.create("read_file", "{'path': '/src/main.py'}", "Read 45 lines", is_error=False)
        rendered = block.render_text()
        assert "┌ read_file" in rendered
        assert "Read 45 lines" in rendered
        assert "✓" in rendered

    def test_format_error(self):
        block = ToolBlock.create("bash", "$ rm -rf /", "Permission denied", is_error=True)
        rendered = block.render_text()
        assert "✗" in rendered

    def test_format_bash(self):
        block = ToolBlock.create("bash", "ls -la", "total 42", is_error=False)
        rendered = block.render_text()
        assert "$ ls -la" in rendered


class TestThinkingBlock:
    def test_collapsed_format(self):
        block = ThinkingBlock(content="deep thoughts", elapsed=3.2, tokens=500)
        collapsed = block.collapsed_text()
        assert "3.2s" in collapsed
        assert "500" in collapsed

    def test_toggle(self):
        block = ThinkingBlock(content="deep thoughts", elapsed=3.2, tokens=500)
        assert not block.expanded
        block.toggle()
        assert block.expanded


class TestTurnSummary:
    def test_format(self):
        summary = TurnSummary.create(elapsed=3.2, input_tokens=2400, output_tokens=890, cost="$0.03")
        text = summary.render_text()
        assert "3.2s" in text
        assert "2,400" in text
        assert "890" in text
        assert "$0.03" in text


class TestSpinnerLine:
    def test_phases(self):
        s = SpinnerLine()
        s.phase = "waiting"
        assert "Waiting" in s.render_text()
        s.phase = "thinking"
        assert "Thinking" in s.render_text()
        s.phase = "processing"
        assert "Processing" in s.render_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tui/test_widgets.py -v`
Expected: FAIL — ImportError for chat_widgets

- [ ] **Step 3: Write chat_widgets.py**

```python
# llm_code/tui/chat_widgets.py
"""Chat entry widgets: ToolBlock, ThinkingBlock, PermissionInline, TurnSummary, SpinnerLine."""
from __future__ import annotations

from dataclasses import dataclass, field

from textual.widget import Widget
from textual.reactive import reactive
from textual.app import RenderResult
from rich.text import Text


SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


@dataclass
class ToolBlockData:
    tool_name: str
    args_display: str
    result: str
    is_error: bool
    diff_lines: list[str] = field(default_factory=list)


class ToolBlock(Widget):
    """Renders a tool call as ┌ name / │ args / ✓ result."""

    DEFAULT_CSS = "ToolBlock { height: auto; margin: 0 0 0 2; }"

    def __init__(self, data: ToolBlockData) -> None:
        super().__init__()
        self._data = data

    @staticmethod
    def create(tool_name: str, args_display: str, result: str, is_error: bool, diff_lines: list[str] | None = None) -> "ToolBlock":
        data = ToolBlockData(
            tool_name=tool_name,
            args_display=args_display,
            result=result,
            is_error=is_error,
            diff_lines=diff_lines or [],
        )
        return ToolBlock(data)

    def render_text(self) -> str:
        d = self._data
        icon = "✗" if d.is_error else "✓"
        args = d.args_display
        if d.tool_name == "bash" and not args.startswith("$"):
            args = f"$ {args}"
        lines = [
            f"  ┌ {d.tool_name}",
            f"  │ {args}",
            f"  {icon} {d.result}",
        ]
        for dl in d.diff_lines:
            lines.append(f"    {dl}")
        return "\n".join(lines)

    def render(self) -> RenderResult:
        d = self._data
        text = Text()
        text.append("  ┌ ", style="dim")
        text.append(d.tool_name, style="bold cyan")
        text.append("\n")

        args = d.args_display
        if d.tool_name == "bash" and not args.startswith("$"):
            args = f"$ {args}"
        text.append("  │ ", style="dim")
        if d.tool_name == "bash":
            text.append(args, style="white on #2a2a3a")
        else:
            text.append(args, style="dim")
        text.append("\n")

        icon = "✗" if d.is_error else "✓"
        icon_style = "bold red" if d.is_error else "bold green"
        text.append(f"  {icon} ", style=icon_style)
        text.append(d.result, style="dim")

        for dl in d.diff_lines:
            text.append("\n")
            if dl.startswith("+"):
                text.append(f"    {dl}", style="green")
            elif dl.startswith("-"):
                text.append(f"    {dl}", style="red")
            else:
                text.append(f"    {dl}", style="dim")

        return text


class ThinkingBlock(Widget):
    """Collapsible thinking block: collapsed shows summary, expanded shows content."""

    DEFAULT_CSS = """
    ThinkingBlock { height: auto; }
    """

    expanded: reactive[bool] = reactive(False)

    def __init__(self, content: str, elapsed: float, tokens: int) -> None:
        super().__init__()
        self._content = content
        self._elapsed = elapsed
        self._tokens = tokens

    def toggle(self) -> None:
        self.expanded = not self.expanded

    def collapsed_text(self) -> str:
        return f"💭 Thinking ({self._elapsed:.1f}s · ~{self._tokens:,} tok)"

    def render(self) -> RenderResult:
        text = Text()
        if not self.expanded:
            text.append(self.collapsed_text(), style="dim blue")
        else:
            text.append(self.collapsed_text(), style="dim blue")
            text.append("\n")
            truncated = self._content[:3000]
            if len(self._content) > 3000:
                truncated += f"\n… [{len(self._content):,} chars total]"
            text.append(truncated, style="dim")
        return text


class TurnSummary(Widget):
    """Turn completion line: ✓ Done (Xs) ↑N · ↓N tok · $X.XX"""

    DEFAULT_CSS = "TurnSummary { height: auto; margin: 0 0 1 0; }"

    def __init__(self, text_content: str) -> None:
        super().__init__()
        self._text_content = text_content

    @staticmethod
    def create(elapsed: float, input_tokens: int, output_tokens: int, cost: str) -> "TurnSummary":
        time_str = f"{elapsed:.1f}s" if elapsed < 60 else f"{elapsed / 60:.1f}m"
        parts = []
        if input_tokens > 0:
            parts.append(f"↑{input_tokens:,}")
        if output_tokens > 0:
            parts.append(f"↓{output_tokens:,}")
        tok_str = f"  {' · '.join(parts)} tok" if parts else ""
        cost_str = f" · {cost}" if cost else ""
        content = f"✓ Done ({time_str}){tok_str}{cost_str}"
        return TurnSummary(content)

    def render_text(self) -> str:
        return self._text_content

    def render(self) -> RenderResult:
        text = Text()
        text.append("✓", style="bold green")
        text.append(self._text_content[1:], style="dim")
        return text


class SpinnerLine(Widget):
    """Animated spinner showing current phase: waiting/thinking/processing."""

    DEFAULT_CSS = "SpinnerLine { height: 1; color: $accent; }"

    phase: reactive[str] = reactive("waiting")
    elapsed: reactive[float] = reactive(0.0)
    _frame: int = 0

    _LABELS = {
        "waiting": "Waiting for model…",
        "thinking": "Thinking…",
        "processing": "Processing…",
        "running": "Running {tool}…",
    }

    def __init__(self, tool_name: str = "") -> None:
        super().__init__()
        self._tool_name = tool_name

    def render_text(self) -> str:
        label = self._LABELS.get(self.phase, "Working…")
        if "{tool}" in label:
            label = label.replace("{tool}", self._tool_name)
        frame = SPINNER_FRAMES[self._frame % len(SPINNER_FRAMES)]
        return f"{frame} {label} ({self.elapsed:.1f}s)"

    def render(self) -> RenderResult:
        return Text(self.render_text(), style="blue")

    def advance_frame(self) -> None:
        self._frame += 1
        self.refresh()


class PermissionInline(Widget):
    """Inline permission prompt with yellow left border."""

    DEFAULT_CSS = """
    PermissionInline {
        height: auto;
        border-left: thick $warning;
        padding: 0 1;
        margin: 0 0 0 2;
    }
    """

    def __init__(self, tool_name: str, args_preview: str) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._args_preview = args_preview

    def render(self) -> RenderResult:
        text = Text()
        text.append("⚠ Allow? ", style="yellow bold")
        text.append(f"{self._tool_name}: {self._args_preview[:60]}", style="dim")
        text.append("\n  ")
        text.append("[y]", style="bold green")
        text.append(" Yes  ", style="dim")
        text.append("[n]", style="bold red")
        text.append(" No  ", style="dim")
        text.append("[a]", style="bold cyan")
        text.append(" Always", style="dim")
        return text
```

- [ ] **Step 4: Write chat_view.py**

```python
# llm_code/tui/chat_view.py
"""ChatScrollView — scrollable container for chat entries."""
from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.app import ComposeResult
from rich.text import Text


class UserMessage(Widget):
    """Renders a user input line: ❯ text"""

    DEFAULT_CSS = "UserMessage { height: auto; margin: 1 0 0 0; }"

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text

    def render(self):
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

    def render(self):
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

    def resume_auto_scroll(self) -> None:
        self._auto_scroll = True
        self.scroll_end(animate=False)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_tui/test_widgets.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add llm_code/tui/chat_view.py llm_code/tui/chat_widgets.py tests/test_tui/test_widgets.py
git commit -m "feat(tui): add ChatScrollView and chat entry widgets"
```

---

### Task 4: InputBar Widget

**Files:**
- Create: `llm_code/tui/input_bar.py`
- Modify: `tests/test_tui/test_widgets.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_tui/test_widgets.py`:

```python
from llm_code.tui.input_bar import InputBar


class TestInputBar:
    def test_creates(self):
        bar = InputBar()
        assert bar.value == ""

    def test_prompt_symbol(self):
        bar = InputBar()
        assert bar.PROMPT == "❯ "

    def test_render_default(self):
        bar = InputBar()
        text = bar.render()
        rendered = str(text)
        assert "❯" in rendered
        assert "█" in rendered

    def test_render_vim_normal(self):
        bar = InputBar()
        bar.vim_mode = "NORMAL"
        rendered = str(bar.render())
        assert "[N]" in rendered

    def test_render_vim_insert(self):
        bar = InputBar()
        bar.vim_mode = "INSERT"
        rendered = str(bar.render())
        assert "[I]" in rendered

    def test_render_disabled(self):
        bar = InputBar()
        bar.disabled = True
        rendered = str(bar.render())
        assert "generating" in rendered
        assert "█" not in rendered

    def test_on_key_character(self):
        bar = InputBar()
        bar._on_key_sim("a")
        bar._on_key_sim("b")
        assert bar.value == "ab"

    def test_on_key_backspace(self):
        bar = InputBar()
        bar.value = "hello"
        bar._on_key_sim("backspace")
        assert bar.value == "hell"

    def test_on_key_backspace_empty(self):
        bar = InputBar()
        bar._on_key_sim("backspace")
        assert bar.value == ""

    def test_on_key_shift_enter_multiline(self):
        bar = InputBar()
        bar.value = "line1"
        bar._on_key_sim("shift+enter")
        assert bar.value == "line1\n"

    def test_on_key_enter_submits_and_clears(self):
        bar = InputBar()
        bar.value = "hello"
        bar._on_key_sim("enter")
        assert bar.value == ""

    def test_on_key_enter_ignores_whitespace_only(self):
        bar = InputBar()
        bar.value = "   "
        bar._on_key_sim("enter")
        assert bar.value == "   "

    def test_on_key_escape_clears_and_cancels(self):
        bar = InputBar()
        bar.value = "draft"
        bar._on_key_sim("escape")
        assert bar.value == ""

    def test_on_key_disabled_blocks_input(self):
        bar = InputBar()
        bar.disabled = True
        bar._on_key_sim("a")
        assert bar.value == ""
```

> **Note:** `_on_key_sim(key)` is a test helper that simulates key events without a running Textual App.
> It should be added as a method on InputBar or as a standalone helper in the test file.

- [ ] **Step 2: Run test — FAIL**

- [ ] **Step 3: Write implementation**

```python
# llm_code/tui/input_bar.py
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
```

- [ ] **Step 4: Run test — PASS**

- [ ] **Step 5: Commit**

```bash
git add llm_code/tui/input_bar.py tests/test_tui/test_widgets.py
git commit -m "feat(tui): add InputBar widget with prompt and key handling"
```

---

### Task 5: App Skeleton — Boot Into Fullscreen

**Files:**
- Create: `llm_code/tui/app.py`
- Test: `tests/test_tui/test_app.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_tui/test_app.py
"""Integration test for the fullscreen TUI app."""
from __future__ import annotations

import pytest
from llm_code.tui.app import LLMCodeTUI


class TestAppCreation:
    def test_app_creates(self):
        app = LLMCodeTUI()
        assert app is not None
        assert app.title == "llm-code"

    def test_app_has_required_widgets(self):
        app = LLMCodeTUI()
        # Verify compose yields expected widget types
        from llm_code.tui.header_bar import HeaderBar
        from llm_code.tui.chat_view import ChatScrollView
        from llm_code.tui.input_bar import InputBar
        from llm_code.tui.status_bar import StatusBar
        widgets = list(app.compose())
        type_names = [type(w).__name__ for w in widgets]
        assert "HeaderBar" in type_names
        assert "ChatScrollView" in type_names
        assert "InputBar" in type_names
        assert "StatusBar" in type_names
```

- [ ] **Step 2: Run test — FAIL**

- [ ] **Step 3: Write app.py**

```python
# llm_code/tui/app.py
"""LLMCodeTUI — Textual fullscreen app composing all widgets."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult

from llm_code.tui.chat_view import ChatScrollView, UserMessage, AssistantText
from llm_code.tui.chat_widgets import (
    PermissionInline,
    SpinnerLine,
    ThinkingBlock,
    ToolBlock,
    TurnSummary,
)
from llm_code.tui.header_bar import HeaderBar
from llm_code.tui.input_bar import InputBar
from llm_code.tui.status_bar import StatusBar
from llm_code.tui.theme import APP_CSS


class LLMCodeTUI(App):
    """Fullscreen TUI matching Claude Code's visual experience."""

    TITLE = "llm-code"
    CSS = APP_CSS
    ENABLE_MOUSE_SUPPORT = False  # CRITICAL: allow terminal mouse selection + copy

    def __init__(
        self,
        config: Any = None,
        cwd: Path | None = None,
        budget: int | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._cwd = cwd or Path.cwd()
        self._budget = budget
        self._runtime = None
        self._cost_tracker = None
        self._input_tokens = 0
        self._output_tokens = 0

    @property
    def title(self) -> str:
        return self.TITLE

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header-bar")
        yield ChatScrollView(id="chat-view")
        yield InputBar(id="input-bar")
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        header = self.query_one(HeaderBar)
        if self._config:
            header.model = getattr(self._config, "model", "")
        header.project = self._cwd.name
        header.branch = self._detect_branch()

    def _detect_branch(self) -> str:
        import subprocess
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self._cwd, capture_output=True, text=True, timeout=3,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def on_input_bar_submitted(self, event: InputBar.Submitted) -> None:
        """Handle user input submission."""
        text = event.value.strip()
        if not text:
            return

        chat = self.query_one(ChatScrollView)
        chat.add_entry(UserMessage(text))

        if text.startswith("/"):
            self._handle_slash_command(text)
        else:
            asyncio.ensure_future(self._run_turn(text))

    def on_input_bar_cancelled(self, event: InputBar.Cancelled) -> None:
        """Handle Escape — cancel running generation."""
        pass  # Phase 2: cancel runtime

    async def _run_turn(self, user_input: str) -> None:
        """Run a conversation turn — Phase 2 will wire ConversationRuntime."""
        chat = self.query_one(ChatScrollView)
        chat.add_entry(AssistantText("(runtime not connected yet)"))

    def _handle_slash_command(self, text: str) -> None:
        """Handle slash commands — Phase 7 will add full support."""
        chat = self.query_one(ChatScrollView)
        if text.strip() in ("/exit", "/quit"):
            self.exit()
        elif text.strip() == "/help":
            chat.add_entry(AssistantText("Available: /help /exit /quit /model /clear"))
        elif text.strip() == "/clear":
            chat.remove_children()
        else:
            chat.add_entry(AssistantText(f"Unknown command: {text}"))
```

- [ ] **Step 4: Run test — PASS**

- [ ] **Step 5: Commit**

```bash
git add llm_code/tui/app.py tests/test_tui/test_app.py
git commit -m "feat(tui): add App skeleton — fullscreen with all 4 widget areas"
```

---

### Task 6: Wire tui_main.py — Switch Default Entry Point

**Files:**
- Modify: `llm_code/cli/tui_main.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_tui/test_app.py (append)

class TestEntryPointFlags:
    def test_tui_main_has_ink_flag(self):
        """Verify tui_main accepts --ink flag."""
        from click.testing import CliRunner
        from llm_code.cli.tui_main import main
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--ink" in result.output
        assert "--lite" in result.output
```

- [ ] **Step 2: Run test — FAIL** (no `--ink` flag yet)

- [ ] **Step 3: Modify tui_main.py**

Replace the final `if lite:` / `else:` block in `llm_code/cli/tui_main.py`:

```python
    # Add --ink flag (after existing --lite)
    # @click.option("--ink", is_flag=True, help="Use React+Ink luxury UI (requires Node.js)")
```

Full replacement of lines 121-132:

```python
    if lite:
        # Lightweight print-based CLI
        from llm_code.cli.tui import LLMCodeCLI
        cli = LLMCodeCLI(config=config, cwd=cwd, budget=budget)
        if resume_session is not None:
            cli._init_session(existing_session=resume_session)
        asyncio.run(cli.run())
    elif ink:
        # React+Ink luxury UI (requires Node.js)
        from llm_code.cli.ink_bridge import InkBridge
        bridge = InkBridge(config=config, cwd=cwd, budget=budget)
        asyncio.run(bridge.start())
    else:
        # Default: Textual fullscreen TUI
        from llm_code.tui.app import LLMCodeTUI
        app = LLMCodeTUI(config=config, cwd=cwd, budget=budget)
        app.run()
```

Also add `--ink` option to the click command decorators:

```python
@click.option("--ink", is_flag=True, help="Use React+Ink luxury UI (requires Node.js)")
```

And add `ink: bool = False` to the function signature.

- [ ] **Step 4: Run test — PASS**

- [ ] **Step 5: Run existing tests to verify no regression**

Run: `pytest tests/test_cli/ -v`
Expected: all passing

- [ ] **Step 6: Commit**

```bash
git add llm_code/cli/tui_main.py tests/test_tui/test_app.py
git commit -m "feat(tui): switch default to Textual fullscreen; --lite and --ink preserved"
```

---

### Task 7: Wire ConversationRuntime — Streaming + Tools + Thinking

**Files:**
- Modify: `llm_code/tui/app.py`

This is the largest task — it connects the runtime to the UI.

- [ ] **Step 1: Add runtime initialization to app.py on_mount**

Add `_init_runtime()` method that mirrors `tui.py._init_session()` but creates a `ConversationRuntime` and stores it in `self._runtime`. Copy the initialization pattern from `llm_code/cli/tui.py:255-575` (provider, tools, permissions, hooks, memory, skills, MCP, deferred tools, telemetry, etc.).

- [ ] **Step 2: Implement _run_turn with full event handling**

Replace the stub `_run_turn` with streaming event handling:

```python
async def _run_turn(self, user_input: str) -> None:
    from llm_code.api.types import (
        StreamTextDelta, StreamThinkingDelta, StreamToolExecStart,
        StreamToolExecResult, StreamToolProgress, StreamMessageStop,
    )

    chat = self.query_one(ChatScrollView)
    input_bar = self.query_one(InputBar)
    status = self.query_one(StatusBar)

    input_bar.disabled = True
    status.is_streaming = True

    # Spinner
    spinner = SpinnerLine()
    spinner.phase = "waiting"
    chat.add_entry(spinner)
    start = time.monotonic()

    # Background timer for spinner
    async def update_spinner():
        while input_bar.disabled:
            await asyncio.sleep(0.1)
            spinner.elapsed = time.monotonic() - start
            spinner.advance_frame()

    timer_task = asyncio.ensure_future(update_spinner())

    assistant = AssistantText()
    thinking_buffer = ""
    thinking_start = time.monotonic()

    try:
        async for event in self._runtime.run_turn(user_input):
            if isinstance(event, StreamTextDelta):
                if spinner.phase == "waiting":
                    spinner.phase = "streaming"
                    chat.remove_widget(spinner)
                    chat.add_entry(assistant)
                assistant.append_text(event.text)
                chat.resume_auto_scroll()

            elif isinstance(event, StreamThinkingDelta):
                spinner.phase = "thinking"
                thinking_buffer += event.text

            elif isinstance(event, StreamToolExecStart):
                if spinner in chat.children:
                    chat.remove_widget(spinner)
                tool_widget = ToolBlock.create(
                    event.tool_name, event.args_summary, "", is_error=False,
                )
                chat.add_entry(tool_widget)
                spinner.phase = "running"
                spinner._tool_name = event.tool_name
                chat.add_entry(spinner)

            elif isinstance(event, StreamToolExecResult):
                # Replace last ToolBlock with completed version
                chat.remove_widget(spinner)
                tool_widget = ToolBlock.create(
                    event.tool_name, "", event.output[:200], event.is_error,
                )
                chat.add_entry(tool_widget)
                spinner.phase = "processing"
                thinking_start = time.monotonic()
                chat.add_entry(spinner)

            elif isinstance(event, StreamMessageStop):
                if event.usage:
                    self._input_tokens += event.usage.input_tokens
                    self._output_tokens = event.usage.output_tokens
                    if self._cost_tracker:
                        self._cost_tracker.add_usage(
                            event.usage.input_tokens, event.usage.output_tokens,
                        )

    except Exception as exc:
        chat.add_entry(AssistantText(f"Error: {exc}"))
    finally:
        timer_task.cancel()
        if spinner in chat.children:
            chat.remove_widget(spinner)
        input_bar.disabled = False
        status.is_streaming = False

    # Thinking panel
    if thinking_buffer:
        elapsed = time.monotonic() - thinking_start
        tokens = len(thinking_buffer) // 4
        chat.add_entry(ThinkingBlock(thinking_buffer, elapsed, tokens))

    # Turn summary
    elapsed = time.monotonic() - start
    cost = self._cost_tracker.format_cost() if self._cost_tracker else ""
    chat.add_entry(TurnSummary.create(elapsed, self._input_tokens, self._output_tokens, cost))

    status.tokens = self._output_tokens
    status.cost = cost
    chat.resume_auto_scroll()
```

- [ ] **Step 3: Add MCP initialization**

Add `async def _init_mcp()` that mirrors `tui.py._init_mcp_servers()`.

- [ ] **Step 4: Test manually**

Run: `python -m llm_code.cli.tui_main`
Expected: Fullscreen TUI boots, can type, can talk to model, sees tools and thinking.

- [ ] **Step 5: Commit**

```bash
git add llm_code/tui/app.py
git commit -m "feat(tui): wire ConversationRuntime — streaming, tools, thinking, spinner"
```

---

### Task 8: Permission Inline — Single-Key y/n/a

**Files:**
- Modify: `llm_code/tui/app.py`
- Modify: `llm_code/tui/chat_widgets.py` (PermissionInline already exists)

- [ ] **Step 1: Add permission handling to app.py**

The key challenge: when `ConversationRuntime` hits `NEED_PROMPT`, the tool execution blocks. We need to intercept this by providing a custom `PermissionPolicy` that notifies the TUI and waits for a response.

Create an `AsyncPermissionPolicy` that wraps the existing one:

```python
# In app.py, add:
class TUIPermissionPolicy:
    """Permission policy that shows inline prompt and waits for user response."""

    def __init__(self, inner_policy, app: "LLMCodeTUI") -> None:
        self._inner = inner_policy
        self._app = app
        self._pending: asyncio.Future | None = None

    def authorize(self, tool_name, required, effective_level=None):
        outcome = self._inner.authorize(tool_name, required, effective_level=effective_level)
        if outcome == PermissionOutcome.NEED_PROMPT:
            # Show permission prompt and block
            self._pending = asyncio.get_event_loop().create_future()
            self._app.call_from_thread(self._app._show_permission, tool_name, "")
            # This runs in thread pool, so we can block
            import concurrent.futures
            result = concurrent.futures.Future()
            # ... bridge async future to sync return
        return outcome
```

This is complex. Simpler approach: modify the runtime to yield a permission event that the app handles.

- [ ] **Step 2: Add key binding for y/n/a**

```python
# In app.py on_key handler:
def on_key(self, event: events.Key) -> None:
    if self._permission_pending:
        if event.key == "y":
            self._resolve_permission("allow")
        elif event.key == "n":
            self._resolve_permission("deny")
        elif event.key == "a":
            self._resolve_permission("always")
        event.prevent_default()
```

- [ ] **Step 3: Test manually**

Configure a tool that requires permission, verify inline prompt appears with y/n/a.

- [ ] **Step 4: Commit**

```bash
git add llm_code/tui/app.py
git commit -m "feat(tui): add inline permission prompt with single-key y/n/a"
```

---

### Task 9: Full Slash Command Support

**Files:**
- Modify: `llm_code/tui/app.py`

- [ ] **Step 1: Port all 30 slash commands from tui.py**

Copy the `_handle_slash_command` method from `llm_code/cli/tui.py:791-1020` and adapt for Textual (replace `console.print` with `chat.add_entry(AssistantText(...))`).

Key commands to port:
- `/help`, `/clear`, `/exit`, `/quit`
- `/model`, `/config`, `/cost`, `/budget`
- `/skill`, `/plugin`, `/mcp` (marketplace)
- `/memory`, `/session`, `/task`, `/swarm`
- `/thinking`, `/vim`, `/voice`, `/search`
- `/undo`, `/index`, `/lsp`, `/ide`
- `/cron`, `/vcr`, `/checkpoint`, `/hida`
- `/cd`, `/image`, `/cancel`

- [ ] **Step 2: Add tab autocomplete to InputBar**

```python
# In input_bar.py, add autocomplete logic:
SLASH_COMMANDS = ["/help", "/clear", "/model", "/skill", ...]

def on_key(self, event):
    if event.key == "tab" and self.value.startswith("/"):
        matches = [c for c in SLASH_COMMANDS if c.startswith(self.value)]
        if len(matches) == 1:
            self.value = matches[0] + " "
```

- [ ] **Step 3: Commit**

```bash
git add llm_code/tui/app.py llm_code/tui/input_bar.py
git commit -m "feat(tui): port all 30 slash commands + tab autocomplete"
```

---

### Task 10: Polish — Vim Mode, Image Paste, Scroll Behavior

**Files:**
- Modify: `llm_code/tui/input_bar.py`
- Modify: `llm_code/tui/app.py`

- [ ] **Step 1: Add Vim mode to InputBar**

Wire existing `llm_code/vim/engine.py` to InputBar key handling. When vim enabled, route keys through `VimEngine` first.

- [ ] **Step 2: Add image paste handling**

On Ctrl+V, try `llm_code/cli/image.py:capture_clipboard_image()`. If image found, store in `self._pending_images` and show `📎 Image attached` in chat.

- [ ] **Step 3: Fix scroll behavior**

- Scroll up: pause auto-scroll
- New user input: resume auto-scroll
- Page Up/Page Down: scroll chat view

- [ ] **Step 4: Test manually end-to-end**

Run full workflow: type prompt → see thinking → see tools → see result → permission prompt → approve → see completion.

- [ ] **Step 5: Commit**

```bash
git add llm_code/tui/input_bar.py llm_code/tui/app.py
git commit -m "feat(tui): add vim mode, image paste, scroll behavior polish"
```

---

### Task 11: Final Integration Test + Cleanup

**Files:**
- Modify: `tests/test_tui/test_app.py`

- [ ] **Step 1: Write integration test using Textual pilot**

```python
import pytest
from textual.pilot import Pilot
from llm_code.tui.app import LLMCodeTUI


@pytest.mark.asyncio
async def test_app_boots_and_accepts_input():
    app = LLMCodeTUI()
    async with app.run_test() as pilot:
        # Verify all widgets present
        assert app.query_one("HeaderBar")
        assert app.query_one("ChatScrollView")
        assert app.query_one("InputBar")
        assert app.query_one("StatusBar")


@pytest.mark.asyncio
async def test_slash_help():
    app = LLMCodeTUI()
    async with app.run_test() as pilot:
        await pilot.press("/", "h", "e", "l", "p", "enter")
        # Verify help appeared in chat
```

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ --ignore=tests/test_api/test_openai_compat.py -q`
Expected: all passing, including new TUI tests

- [ ] **Step 3: Final commit**

```bash
git add -u
git commit -m "feat(tui): fullscreen TUI complete — Claude Code UI experience"
```

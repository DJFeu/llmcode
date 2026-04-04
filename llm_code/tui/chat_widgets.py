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
    """Renders a tool call — Claude Code style with diff view for edit/write."""

    DEFAULT_CSS = "ToolBlock { height: auto; margin: 0 0 0 0; }"

    # Map tool names to display actions
    _ACTION_MAP = {
        "edit_file": "Update",
        "write_file": "Write",
        "read_file": "Read",
        "bash": "Bash",
        "glob_search": "Search",
        "grep_search": "Search",
        "notebook_read": "Read",
        "notebook_edit": "Update",
    }

    def __init__(self, data: ToolBlockData) -> None:
        super().__init__()
        self._data = data

    @staticmethod
    def create(
        tool_name: str,
        args_display: str,
        result: str,
        is_error: bool,
        diff_lines: list[str] | None = None,
    ) -> "ToolBlock":
        data = ToolBlockData(
            tool_name=tool_name,
            args_display=args_display,
            result=result,
            is_error=is_error,
            diff_lines=diff_lines or [],
        )
        return ToolBlock(data)

    def _extract_file_path(self) -> str:
        """Extract file path from args_display."""
        d = self._data
        for pattern in ("'path': '", '"path": "', "'file_path': '", '"file_path": "'):
            if pattern in d.args_display:
                quote = "'" if "'" in pattern[-1] else '"'
                start = d.args_display.index(pattern) + len(pattern)
                end = d.args_display.find(quote, start)
                if end == -1:
                    # Truncated — use rest of string
                    return d.args_display[start:start + 80]
                return d.args_display[start:end]
        return d.args_display[:80]

    def _count_diff_changes(self) -> tuple[int, int]:
        """Count added and removed lines in diff."""
        added = sum(1 for l in self._data.diff_lines if l.startswith("+"))
        removed = sum(1 for l in self._data.diff_lines if l.startswith("-"))
        return added, removed

    def render_text(self) -> str:
        d = self._data
        action = self._ACTION_MAP.get(d.tool_name, d.tool_name)
        file_path = self._extract_file_path()
        icon = "✗" if d.is_error else "●"
        lines = [f"{icon} {action}({file_path})"]
        if d.result:
            lines.append(f"  └ {d.result}")
        return "\n".join(lines)

    def render(self) -> RenderResult:
        d = self._data
        text = Text()
        action = self._ACTION_MAP.get(d.tool_name, d.tool_name)
        file_path = self._extract_file_path()

        # Header: ● Action(file_path) or ✗ Action(file_path)
        if d.is_error:
            text.append("✗ ", style="bold red")
        else:
            text.append("● ", style="bold #cc7a00")
        text.append(f"{action}(", style="bold white")
        text.append(file_path, style="bold white")
        text.append(")", style="bold white")

        # For bash: show command
        if d.tool_name == "bash":
            args = d.args_display
            if not args.startswith("$"):
                args = f"$ {args}"
            text.append("\n")
            text.append(f"  │ {args}", style="white on #2a2a3a")

        # Result summary
        if d.result:
            text.append("\n")
            # For edit/write: show diff summary
            if d.diff_lines and d.tool_name in ("edit_file", "write_file"):
                added, removed = self._count_diff_changes()
                parts = []
                if added:
                    parts.append(f"Added {added} line{'s' if added != 1 else ''}")
                if removed:
                    parts.append(f"removed {removed} line{'s' if removed != 1 else ''}")
                summary = ", ".join(parts) if parts else d.result
                text.append(f"  └ {summary}", style="dim")
            else:
                icon = "✗" if d.is_error else "✓"
                icon_style = "bold red" if d.is_error else "bold green"
                text.append(f"  {icon} ", style=icon_style)
                text.append(d.result, style="dim")

        # Diff lines with line numbers and colored backgrounds
        for dl in d.diff_lines:
            text.append("\n")
            if dl.startswith("+"):
                # Added line: green background
                text.append(f"    {dl}", style="green on #0a2e0a")
            elif dl.startswith("-"):
                # Removed line: red background
                text.append(f"    {dl}", style="red on #2e0a0a")
            else:
                # Context line
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
            text.append(self.collapsed_text(), style="#cc7a00")
        else:
            text.append(self.collapsed_text(), style="#cc7a00")
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
    """Animated spinner — color changes: orange (normal) → red (>60s)."""

    DEFAULT_CSS = "SpinnerLine { height: auto; }"

    phase: reactive[str] = reactive("waiting")
    elapsed: reactive[float] = reactive(0.0)
    tokens: reactive[int] = reactive(0)
    _frame: int = 0

    _LABELS = {
        "waiting": "Waiting for model…",
        "thinking": "Puttering…",
        "processing": "Processing…",
        "running": "Reading {tool}…",
        "streaming": "Streaming…",
    }

    def __init__(self, tool_name: str = "") -> None:
        super().__init__()
        self._tool_name = tool_name
        self._detail_lines: list[str] = []

    def set_detail(self, lines: list[str]) -> None:
        """Set detail lines shown below the spinner (e.g. file paths)."""
        self._detail_lines = lines
        self.refresh()

    def render_text(self) -> str:
        label = self._LABELS.get(self.phase, "Working…")
        if "{tool}" in label:
            label = label.replace("{tool}", self._tool_name)
        # Time formatting
        if self.elapsed >= 60:
            time_str = f"{self.elapsed / 60:.0f}m {self.elapsed % 60:.0f}s"
        else:
            time_str = f"{self.elapsed:.0f}s"
        # Build status parts
        parts = [time_str]
        if self.tokens > 0:
            parts.append(f"↑ {self.tokens:,} tokens")
        if self.phase == "thinking":
            parts.append("thinking")
        meta = " · ".join(parts)
        return f"{label} ({meta})"

    def render(self) -> RenderResult:
        # Color: orange normally, red when elapsed > 60s
        color = "#cc3333" if self.elapsed > 60 else "#cc7a00"
        prefix = "●" if self.phase == "running" else "*"
        text = Text()
        text.append(f"{prefix} ", style=f"bold {color}")
        text.append(self.render_text(), style=color)
        # Detail lines (e.g. tool file paths)
        for line in self._detail_lines:
            text.append(f"\n    └ {line}", style="dim")
        return text

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

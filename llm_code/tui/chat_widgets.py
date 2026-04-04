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

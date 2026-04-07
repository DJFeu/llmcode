# llm_code/tui/chat_widgets.py
"""Chat entry widgets: ToolBlock, ThinkingBlock, PermissionInline, TurnSummary, SpinnerLine."""
from __future__ import annotations

from dataclasses import dataclass, field

from textual.widget import Widget
from textual.reactive import reactive
from textual.app import RenderResult
from rich.text import Text

from llm_code.tui.spinner_verbs import get_verb
from llm_code.tui.tool_render import render_tool_args


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

    def update_result(
        self,
        result: str,
        is_error: bool,
        diff_lines: list[str] | None = None,
    ) -> None:
        """Fill in the result on an existing in-place ToolBlock — the
        Claude Code pattern of one widget per tool_use_id transitioning
        from running -> done in place rather than mounting a second block."""
        self._data = ToolBlockData(
            tool_name=self._data.tool_name,
            args_display=self._data.args_display,
            result=result,
            is_error=is_error,
            diff_lines=diff_lines or self._data.diff_lines,
        )
        self.refresh()

    def _extract_file_path(self) -> str:
        """DEPRECATED: use render_tool_args() from tool_render.py instead.

        Kept for backward compatibility with callers/tests that rely on the
        legacy regex-based extraction.
        """
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
        added = sum(1 for line in self._data.diff_lines if line.startswith("+"))
        removed = sum(1 for line in self._data.diff_lines if line.startswith("-"))
        return added, removed

    def render_text(self) -> str:
        d = self._data
        action = self._ACTION_MAP.get(d.tool_name, d.tool_name)
        file_path = render_tool_args(d.tool_name, d.args_display)
        icon = "✗" if d.is_error else "●"
        lines = [f"{icon} {action}({file_path})"]
        if d.result:
            lines.append(f"  └ {d.result}")
        return "\n".join(lines)

    def render(self) -> RenderResult:
        d = self._data
        text = Text()
        action = self._ACTION_MAP.get(d.tool_name, d.tool_name)
        file_path = render_tool_args(d.tool_name, d.args_display)

        # Header: ● Action(file_path) or ✗ Action(file_path)
        if d.is_error:
            text.append("✗ ", style="bold red")
        else:
            text.append("● ", style="bold #cc7a00")
        text.append(f"{action}(", style="bold white")
        text.append(file_path, style="bold white")
        text.append(")", style="bold white")

        # For bash: show the actual command (not the raw args dict).
        # render_tool_args already returns "$ command" for bash.
        if d.tool_name == "bash":
            cmd = render_tool_args(d.tool_name, d.args_display)
            text.append("\n")
            text.append(f"  │ {cmd}", style="white on #2a2a3a")

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
    """Animated spinner with whimsical verbs, smooth token counter,
    width gating, and stalled color interpolation."""

    DEFAULT_CSS = "SpinnerLine { height: auto; }"

    phase: reactive[str] = reactive("waiting")
    elapsed: reactive[float] = reactive(0.0)
    tokens: reactive[int] = reactive(0)

    _LABELS = {
        "waiting": "Waiting for model…",
        "processing": "Processing…",
        "running": "Reading {tool}…",
        "streaming": "Streaming…",
        "routing": "Routing skills…",
    }

    # Base color (blue-ish) → stalled (red)
    _BASE_RGB = (96, 175, 255)
    _STALLED_RGB = (171, 43, 63)

    def __init__(
        self,
        tool_name: str = "",
        verb_override: tuple[str, ...] = (),
        verb_mode: str = "append",
    ) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._detail_lines: list[str] = []
        self._frame: int = 0
        self._verb: str = ""
        self._verb_override = verb_override
        self._verb_mode = verb_mode
        self._displayed_tokens: float = 0.0
        self._last_progress: float = 0.0

    def set_detail(self, lines: list[str]) -> None:
        """Set detail lines shown below the spinner (e.g. file paths)."""
        self._detail_lines = lines
        self.refresh()

    def _pick_verb(self) -> None:
        self._verb = get_verb(
            seed=None,
            override=self._verb_override,
            mode=self._verb_mode,
        )

    def watch_phase(self, old: str, new: str) -> None:
        if new in ("thinking", "processing") and old != new:
            self._pick_verb()
        # Reset stall-progress anchor on phase change
        self._last_progress = self.elapsed

    def watch_tokens(self, old: int, new: int) -> None:
        if new > old:
            self._last_progress = self.elapsed

    def _terminal_width(self) -> int:
        try:
            return int(self.app.size.width)
        except Exception:
            return 80

    def _stall_rgb(self) -> tuple[int, int, int]:
        stalled_for = self.elapsed - self._last_progress
        if stalled_for <= 30:
            return self._BASE_RGB
        t = min((stalled_for - 30) / 30.0, 1.0)
        br, bg, bb = self._BASE_RGB
        sr, sg, sb = self._STALLED_RGB
        r = int(br + (sr - br) * t)
        g = int(bg + (sg - bg) * t)
        b = int(bb + (sb - bb) * t)
        return r, g, b

    def _label_for_phase(self) -> str:
        phase = self.phase
        if phase in ("thinking", "processing"):
            verb = self._verb or "Working"
            return f"{verb}…"
        label = self._LABELS.get(phase, "Working…")
        if "{tool}" in label:
            label = label.replace("{tool}", self._tool_name)
        return label

    def render_text(self) -> str:
        width = self._terminal_width()
        label = self._label_for_phase()

        # Elapsed time
        time_str = ""
        if self.elapsed >= 3:
            if self.elapsed >= 60:
                time_str = f"{self.elapsed / 60:.0f}m {self.elapsed % 60:.0f}s"
            else:
                time_str = f"{self.elapsed:.0f}s"

        tokens_int = int(self._displayed_tokens)

        # Width < 40: strip suffix, just verb…/label
        if width < 40:
            return label

        parts: list[str] = []
        if time_str:
            parts.append(time_str)
        # Width < 60: drop tokens
        if tokens_int > 0 and width >= 60:
            parts.append(f"↓ {tokens_int:,} tokens")
        if not parts:
            return label
        meta = " · ".join(parts)
        return f"{label} ({meta})"

    def render(self) -> RenderResult:
        r, g, b = self._stall_rgb()
        color = f"rgb({r},{g},{b})"
        prefix = "●" if self.phase == "running" else "*"
        text = Text()
        text.append(f"{prefix} ", style=f"bold {color}")
        text.append(self.render_text(), style=color)
        for line in self._detail_lines:
            text.append(f"\n    └ {line}", style="dim")
        return text

    def _advance_tokens(self) -> None:
        target = float(self.tokens)
        if self._displayed_tokens >= target:
            self._displayed_tokens = target
            return
        delta = target - self._displayed_tokens
        step = max(1.0, delta / 8.0)
        self._displayed_tokens = min(target, self._displayed_tokens + step)

    def advance_frame(self) -> None:
        self._frame += 1
        self._advance_tokens()
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

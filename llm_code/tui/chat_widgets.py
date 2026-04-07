# llm_code/tui/chat_widgets.py
"""Chat entry widgets: ToolBlock, ThinkingBlock, PermissionInline, TurnSummary, SpinnerLine."""
from __future__ import annotations

from dataclasses import dataclass, field

from textual.widget import Widget
from textual.reactive import reactive
from textual.app import RenderResult
from rich.text import Text

import re
import time

from llm_code.tui.ansi_strip import strip_ansi
from llm_code.tui.diff_render import render_diff_lines
from llm_code.tui.spinner_verbs import get_verb
from llm_code.tui.tool_render import render_tool_args


_SANDBOX_TAG_RE = re.compile(r"<sandbox-violation>.*?</sandbox-violation>", re.DOTALL)


def _clean_tool_result(text: str) -> str:
    """Strip sandbox violation tags + ANSI escapes from a tool result."""
    if not text:
        return text
    text = _SANDBOX_TAG_RE.sub("", text)
    text = strip_ansi(text)
    return text


def _truncate_lines(text: str, max_lines: int = 8) -> tuple[str, int]:
    """Return (truncated_text, hidden_line_count)."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, 0
    return "\n".join(lines[:max_lines]), len(lines) - max_lines


class RateLimitBar(Widget):
    """Renders a remote-API rate limit usage bar.

    Reads `cost_tracker.rate_limit_info` (dict with `used`, `limit`, `reset_at`).
    Hidden entirely when info is None (e.g. local models).
    """

    DEFAULT_CSS = "RateLimitBar { height: auto; }"

    def __init__(self, cost_tracker=None, width: int = 12) -> None:
        super().__init__()
        self._cost_tracker = cost_tracker
        self._width = width

    def set_tracker(self, cost_tracker) -> None:
        self._cost_tracker = cost_tracker
        self.refresh()

    def _info(self) -> dict | None:
        if self._cost_tracker is None:
            return None
        return getattr(self._cost_tracker, "rate_limit_info", None)

    def render_text(self) -> str:
        info = self._info()
        if not info:
            return ""
        used = float(info.get("used", 0))
        limit = float(info.get("limit", 0))
        if limit <= 0:
            return ""
        pct = min(1.0, used / limit)
        filled = int(round(pct * self._width))
        bar = "█" * filled + "░" * (self._width - filled)
        reset_at = info.get("reset_at")
        reset_str = ""
        if reset_at:
            secs = max(0, int(reset_at - time.time()))
            hours = secs // 3600
            mins = (secs % 3600) // 60
            reset_str = f" · resets in {hours}h {mins:02d}m"
        return f"[{bar}] {int(pct*100)}%{reset_str}"

    def render(self) -> RenderResult:
        s = self.render_text()
        if not s:
            return Text("")
        return Text(s, style="cyan")


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
        self._verbose: bool = False

    def set_verbose(self, verbose: bool) -> None:
        self._verbose = bool(verbose)
        self.refresh()

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
            result=_clean_tool_result(result),
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
            result=_clean_tool_result(result),
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
                # Truncate long error output unless verbose
                if d.is_error and not self._verbose:
                    body, hidden = _truncate_lines(d.result, max_lines=8)
                    text.append(body, style="dim")
                    if hidden:
                        text.append(f"\n  … +{hidden} more line{'s' if hidden != 1 else ''} (Ctrl+V to expand)", style="dim italic")
                else:
                    text.append(d.result, style="dim")

        # Diff lines: delegate to structured renderer (hunk headers,
        # gutter line numbers, color blocks, truncation footer)
        if d.diff_lines:
            text.append("\n")
            text.append(render_diff_lines(d.diff_lines, max_lines=40))

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


class MCPApprovalInline(Widget):
    """Inline approval prompt for a non-root MCP server spawn."""

    DEFAULT_CSS = """
    MCPApprovalInline {
        height: auto;
        border-left: thick $warning;
        padding: 0 1;
        margin: 0 0 0 2;
    }
    """

    def __init__(
        self,
        server_name: str,
        owner_agent_id: str,
        command: str,
        description: str = "",
    ) -> None:
        super().__init__()
        self._server_name = server_name
        self._owner_agent_id = owner_agent_id
        self._command = command
        self._description = description

    def render(self) -> RenderResult:
        text = Text()
        text.append("⚠ MCP server ", style="yellow bold")
        text.append(f"'{self._server_name}'", style="bold white")
        text.append(" wants to start\n", style="yellow bold")
        if self._owner_agent_id:
            text.append("  Requested by: ", style="dim")
            text.append(f"{self._owner_agent_id}\n", style="dim")
        if self._command:
            text.append("  ")
            text.append(f" {self._command[:120]} ", style="white on #2a2a3a")
            text.append("\n")
        if self._description:
            text.append(f"  {self._description}\n", style="dim italic")
        options: list[tuple[str, str]] = [
            ("y", "Allow once"),
            ("a", f"Always allow '{self._server_name}' this session"),
            ("n", "Deny"),
        ]
        for i, (key, label) in enumerate(options):
            text.append("  ")
            text.append(f"[{key}]", style="bold bright_cyan")
            text.append(f" {label}", style="dim")
            if i < len(options) - 1:
                text.append("\n")
        return text


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

    def _supports_edit_args(self) -> bool:
        return self._tool_name in ("bash", "edit_file", "write_file", "multi_edit")

    def _always_kind_label(self) -> str:
        if self._tool_name == "bash":
            # Try to extract a sensible prefix for "all `git *`" labelling
            preview = self._args_preview
            # args_preview is JSON: {"command": "git status"}
            try:
                import json as _json
                parsed = _json.loads(preview) if preview.startswith("{") else None
                if parsed and "command" in parsed:
                    first = str(parsed["command"]).split()[0] if parsed["command"] else ""
                    if first:
                        return f"Always allow `{first} *`"
            except Exception:
                pass
            return "Always allow all bash"
        if self._tool_name in ("edit_file", "write_file", "multi_edit"):
            return "Always allow edits in workspace"
        return f"Always allow all `{self._tool_name}`"

    def render(self) -> RenderResult:
        text = Text()
        text.append("⚠ Allow ", style="yellow bold")
        text.append(self._tool_name, style="bold white")
        text.append("?\n", style="yellow bold")
        # Verbatim args in code-style box
        text.append("  ")
        text.append(f" {self._args_preview[:100]} ", style="white on #2a2a3a")
        text.append("\n")
        # Options
        options: list[tuple[str, str]] = [
            ("y", "Allow once"),
            ("a", self._always_kind_label()),
            ("A", "Always allow this exact"),
            ("n", "Deny"),
        ]
        if self._supports_edit_args():
            options.append(("e", "Edit args (TODO)"))
        for i, (key, label) in enumerate(options):
            if i == 0:
                text.append("  ")
            else:
                text.append("  ")
            text.append(f"[{key}]", style="bold bright_cyan")
            text.append(f" {label}", style="dim")
            if i < len(options) - 1:
                text.append("\n")
        return text

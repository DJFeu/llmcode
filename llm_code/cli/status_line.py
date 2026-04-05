"""CLIStatusLine — persistent bottom status bar for the print CLI."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.text import Text


@dataclass
class StatusLineState:
    model: str = ""
    tokens: int = 0
    cost: str = ""
    is_streaming: bool = False
    permission_mode: str = ""
    context_usage: float = 0.0  # 0.0-1.0


def format_status_line(state: StatusLineState) -> str:
    """Format status line as a pipe-separated string."""
    parts: list[str] = []
    if state.permission_mode and state.permission_mode != "prompt":
        parts.append(f"[{state.permission_mode}]")
    if state.model:
        parts.append(state.model)
    if state.tokens > 0:
        parts.append(f"↓{state.tokens:,} tok")
    if state.cost:
        parts.append(state.cost)
    if state.context_usage >= 0.6:
        pct = int(state.context_usage * 100)
        filled = int(state.context_usage * 8)
        bar = "█" * filled + "░" * (8 - filled)
        parts.append(f"[{bar}] {pct}%")
    if state.is_streaming:
        parts.append("streaming…")
    parts.append("/help")
    parts.append("Ctrl+D quit")
    return " │ ".join(parts)

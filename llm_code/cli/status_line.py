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


class CLIStatusLine:
    """Persistent bottom status line for the print CLI using Rich Live."""

    def __init__(self, console: Console) -> None:
        self._console = console
        self.state = StatusLineState()
        self._live: Live | None = None

    def update(self, **kwargs: Any) -> None:
        """Update one or more state fields and refresh the display."""
        for key, value in kwargs.items():
            if hasattr(self.state, key):
                setattr(self.state, key, value)
        if self._live is not None:
            self._live.update(self._render())

    def _render(self) -> Text:
        """Render the current state as a Rich Text object."""
        return Text(format_status_line(self.state), style="dim")

    def start(self) -> None:
        """Begin live rendering at the bottom of the terminal."""
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=4,
            transient=True,
        )
        self._live.start()

    def stop(self) -> None:
        """Stop live rendering."""
        if self._live is not None:
            self._live.stop()
            self._live = None

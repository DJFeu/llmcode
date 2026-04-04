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

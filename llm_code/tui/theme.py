"""Color constants and Textual CSS for the fullscreen TUI.

Supports theme switching via ``apply_theme(name)``.  The active theme's
color map is accessible via ``COLORS`` (module-level dict, mutated in
place so existing references stay valid).
"""
from __future__ import annotations

from llm_code.tui.themes import DEFAULT, Theme, get_theme

# Active semantic color map — values are Rich/Textual style strings.
# Mutated in-place by apply_theme() so all importers see the update.
COLORS: dict[str, str] = dict(DEFAULT.colors)

# Current theme (mutable module state)
_active_theme: Theme = DEFAULT


def apply_theme(name: str) -> Theme:
    """Switch the active theme by name.  Returns the applied Theme."""
    global _active_theme
    theme = get_theme(name)
    _active_theme = theme
    COLORS.clear()
    COLORS.update(theme.colors)
    return theme


def get_active_theme() -> Theme:
    """Return the currently active theme."""
    return _active_theme


# Textual CSS applied to the App (uses Textual CSS variables which
# are overridden at runtime by the theme's accent/surface colors).
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

"""Theme definitions for the TUI.

Each theme provides a semantic color map + Textual CSS variable overrides.
Themes are applied by replacing the COLORS dict and injecting CSS vars.

Built-in themes:
    - default (cyan on dark)
    - dracula (purple accent)
    - monokai (warm yellow/green)
    - tokyo-night (blue/purple)
    - github-dark (blue accent)
    - solarized-dark (teal/orange)
    - nord (arctic blue)
    - gruvbox (earthy orange/green)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    display_name: str
    colors: dict[str, str]
    # Textual CSS variable overrides (injected as :root vars)
    accent: str        # primary accent color
    surface: str       # background surface tint
    warning_color: str  # warning/permission border


# ---------------------------------------------------------------------------
# Built-in themes
# ---------------------------------------------------------------------------

DEFAULT = Theme(
    name="default",
    display_name="Default (Cyan)",
    colors={
        "prompt": "bold cyan",
        "tool_name": "bold cyan",
        "tool_line": "dim",
        "tool_args": "dim",
        "success": "bold green",
        "error": "bold red",
        "diff_add": "green",
        "diff_del": "red",
        "thinking": "#cc7a00",
        "warning": "yellow",
        "spinner": "blue",
        "dim": "dim",
        "bash_cmd": "white on #2a2a3a",
        "agent": "bold cyan",
        "shortcut_key": "bold",
    },
    accent="#00d7ff",
    surface="#1e1e2e",
    warning_color="#e5c07b",
)

DRACULA = Theme(
    name="dracula",
    display_name="Dracula",
    colors={
        "prompt": "bold #bd93f9",
        "tool_name": "bold #bd93f9",
        "tool_line": "dim",
        "tool_args": "#6272a4",
        "success": "bold #50fa7b",
        "error": "bold #ff5555",
        "diff_add": "#50fa7b",
        "diff_del": "#ff5555",
        "thinking": "#f1fa8c",
        "warning": "#ffb86c",
        "spinner": "#8be9fd",
        "dim": "#6272a4",
        "bash_cmd": "#f8f8f2 on #44475a",
        "agent": "bold #bd93f9",
        "shortcut_key": "bold #ff79c6",
    },
    accent="#bd93f9",
    surface="#282a36",
    warning_color="#ffb86c",
)

MONOKAI = Theme(
    name="monokai",
    display_name="Monokai Pro",
    colors={
        "prompt": "bold #a6e22e",
        "tool_name": "bold #66d9ef",
        "tool_line": "dim",
        "tool_args": "#75715e",
        "success": "bold #a6e22e",
        "error": "bold #f92672",
        "diff_add": "#a6e22e",
        "diff_del": "#f92672",
        "thinking": "#e6db74",
        "warning": "#fd971f",
        "spinner": "#66d9ef",
        "dim": "#75715e",
        "bash_cmd": "#f8f8f2 on #3e3d32",
        "agent": "bold #ae81ff",
        "shortcut_key": "bold #f92672",
    },
    accent="#a6e22e",
    surface="#272822",
    warning_color="#fd971f",
)

TOKYO_NIGHT = Theme(
    name="tokyo-night",
    display_name="Tokyo Night",
    colors={
        "prompt": "bold #7aa2f7",
        "tool_name": "bold #7dcfff",
        "tool_line": "dim",
        "tool_args": "#565f89",
        "success": "bold #9ece6a",
        "error": "bold #f7768e",
        "diff_add": "#9ece6a",
        "diff_del": "#f7768e",
        "thinking": "#e0af68",
        "warning": "#e0af68",
        "spinner": "#7aa2f7",
        "dim": "#565f89",
        "bash_cmd": "#c0caf5 on #1a1b26",
        "agent": "bold #bb9af7",
        "shortcut_key": "bold #ff9e64",
    },
    accent="#7aa2f7",
    surface="#1a1b26",
    warning_color="#e0af68",
)

GITHUB_DARK = Theme(
    name="github-dark",
    display_name="GitHub Dark",
    colors={
        "prompt": "bold #58a6ff",
        "tool_name": "bold #79c0ff",
        "tool_line": "dim",
        "tool_args": "#8b949e",
        "success": "bold #3fb950",
        "error": "bold #f85149",
        "diff_add": "#3fb950",
        "diff_del": "#f85149",
        "thinking": "#d29922",
        "warning": "#d29922",
        "spinner": "#58a6ff",
        "dim": "#8b949e",
        "bash_cmd": "#c9d1d9 on #161b22",
        "agent": "bold #d2a8ff",
        "shortcut_key": "bold #ffa657",
    },
    accent="#58a6ff",
    surface="#0d1117",
    warning_color="#d29922",
)

SOLARIZED_DARK = Theme(
    name="solarized-dark",
    display_name="Solarized Dark",
    colors={
        "prompt": "bold #268bd2",
        "tool_name": "bold #2aa198",
        "tool_line": "dim",
        "tool_args": "#586e75",
        "success": "bold #859900",
        "error": "bold #dc322f",
        "diff_add": "#859900",
        "diff_del": "#dc322f",
        "thinking": "#b58900",
        "warning": "#cb4b16",
        "spinner": "#268bd2",
        "dim": "#586e75",
        "bash_cmd": "#93a1a1 on #073642",
        "agent": "bold #6c71c4",
        "shortcut_key": "bold #d33682",
    },
    accent="#268bd2",
    surface="#002b36",
    warning_color="#cb4b16",
)

NORD = Theme(
    name="nord",
    display_name="Nord",
    colors={
        "prompt": "bold #88c0d0",
        "tool_name": "bold #81a1c1",
        "tool_line": "dim",
        "tool_args": "#4c566a",
        "success": "bold #a3be8c",
        "error": "bold #bf616a",
        "diff_add": "#a3be8c",
        "diff_del": "#bf616a",
        "thinking": "#ebcb8b",
        "warning": "#d08770",
        "spinner": "#5e81ac",
        "dim": "#4c566a",
        "bash_cmd": "#d8dee9 on #3b4252",
        "agent": "bold #b48ead",
        "shortcut_key": "bold #d08770",
    },
    accent="#88c0d0",
    surface="#2e3440",
    warning_color="#d08770",
)

GRUVBOX = Theme(
    name="gruvbox",
    display_name="Gruvbox Dark",
    colors={
        "prompt": "bold #fe8019",
        "tool_name": "bold #83a598",
        "tool_line": "dim",
        "tool_args": "#928374",
        "success": "bold #b8bb26",
        "error": "bold #fb4934",
        "diff_add": "#b8bb26",
        "diff_del": "#fb4934",
        "thinking": "#fabd2f",
        "warning": "#fe8019",
        "spinner": "#83a598",
        "dim": "#928374",
        "bash_cmd": "#ebdbb2 on #3c3836",
        "agent": "bold #d3869b",
        "shortcut_key": "bold #fabd2f",
    },
    accent="#fe8019",
    surface="#282828",
    warning_color="#fe8019",
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

BUILTIN_THEMES: dict[str, Theme] = {
    t.name: t for t in [
        DEFAULT, DRACULA, MONOKAI, TOKYO_NIGHT,
        GITHUB_DARK, SOLARIZED_DARK, NORD, GRUVBOX,
    ]
}


def get_theme(name: str) -> Theme:
    """Return a theme by name, falling back to default."""
    return BUILTIN_THEMES.get(name, DEFAULT)


def list_themes() -> list[str]:
    """Return sorted list of available theme names."""
    return sorted(BUILTIN_THEMES.keys())

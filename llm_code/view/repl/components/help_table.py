"""Rich categorized /help output (M15 Task F1)."""
from __future__ import annotations

from typing import Dict, List, Tuple

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table

from llm_code.view.repl import style

__all__ = ["render_help"]


# Ordered category map keyed on command name prefix. Unmatched
# commands land in "Other".
_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("Core", ("help", "exit", "quit", "clear", "about", "version")),
    ("Session", ("new", "save", "load", "export", "compact", "history")),
    ("Mode", ("plan", "yolo", "vim", "theme", "config")),
    ("Tools", ("bash", "edit", "read", "write", "lsp", "sandbox")),
    ("Agents", ("agent", "swarm", "task", "spawn", "coordinator")),
    ("Skills & Plugins", ("skill", "plugin", "mcp")),
    ("Runtime", ("cache", "hook", "memory", "telemetry", "budget")),
]


def _categorize(commands: List[Tuple[str, str]]) -> Dict[str, List[Tuple[str, str]]]:
    """Return {category: [(name, description), ...]} bucketed by category.

    A command's category is the first matching prefix from
    :data:`_CATEGORIES`. Unmatched commands go into ``"Other"``.
    """
    buckets: Dict[str, List[Tuple[str, str]]] = {
        name: [] for name, _ in _CATEGORIES
    }
    buckets["Other"] = []
    for name, desc in commands:
        bare = name.lstrip("/")
        placed = False
        for category, prefixes in _CATEGORIES:
            if any(bare == p or bare.startswith(p) for p in prefixes):
                buckets[category].append((name, desc))
                placed = True
                break
        if not placed:
            buckets["Other"].append((name, desc))
    return buckets


def render_help(commands: List[Tuple[str, str]]) -> RenderableType:
    """Return a Rich renderable with one Panel per command category.

    Commands is a list of ``(name, description)`` tuples — typically
    pulled from ``cli.commands.COMMAND_REGISTRY``.
    """
    buckets = _categorize(sorted(set(commands)))
    panels: List[RenderableType] = []
    for category, _ in _CATEGORIES + [("Other", ())]:
        entries = buckets.get(category) or []
        if not entries:
            continue
        table = Table.grid(padding=(0, 2), expand=True)
        table.add_column(
            justify="left",
            style=f"bold {style.palette.command_fg}",
            no_wrap=True,
        )
        table.add_column(justify="left", style=style.palette.hint_fg)
        for name, desc in entries:
            table.add_row(name, desc or "")
        panels.append(
            Panel(
                table,
                title=f"[bold {style.palette.brand_accent}]{category}[/]",
                title_align="left",
                border_style=style.palette.brand_accent,
                padding=(0, 1),
            )
        )
    return Group(*panels)

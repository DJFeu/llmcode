"""Marketplace metadata + select flow helpers (M15 Task F2)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from rich.panel import Panel
from rich.table import Table

from llm_code.view.repl import style

__all__ = [
    "MarketplaceEntry",
    "render_entry_metadata",
    "render_entry_list",
]


@dataclass
class MarketplaceEntry:
    name: str
    version: str = ""
    description: str = ""
    author: str = ""
    installed: bool = False


def render_entry_metadata(entry: MarketplaceEntry) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", style=f"bold {style.palette.brand_accent}")
    table.add_column(justify="left")
    table.add_row("name", entry.name)
    if entry.version:
        table.add_row("version", entry.version)
    if entry.author:
        table.add_row("author", entry.author)
    if entry.description:
        table.add_row("description", entry.description)
    table.add_row(
        "installed",
        "yes" if entry.installed else "no",
    )
    return Panel(
        table,
        title=f"[bold {style.palette.brand_accent}]{entry.name}[/]",
        border_style=style.palette.brand_accent,
        padding=(0, 1),
    )


def render_entry_list(entries: List[MarketplaceEntry]) -> Table:
    table = Table(
        title=None,
        show_header=True,
        header_style=f"bold {style.palette.brand_accent}",
    )
    table.add_column("name", style=f"bold {style.palette.command_fg}")
    table.add_column("version", style=style.palette.hint_fg)
    table.add_column("installed", style=style.palette.hint_fg)
    table.add_column("description", style=style.palette.system_fg)
    for e in entries:
        installed = (
            f"[{style.palette.status_success}]yes[/]"
            if e.installed
            else f"[{style.palette.hint_fg}]no[/]"
        )
        table.add_row(e.name, e.version or "—", installed, e.description or "")
    return table

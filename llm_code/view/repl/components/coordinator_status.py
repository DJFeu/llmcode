"""Coordinator agent status block (M15 Task E2)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal

from rich.panel import Panel
from rich.table import Table

from llm_code.view.repl import style
from llm_code.view.repl.components.agent_label import color_for_agent

__all__ = ["AgentStatus", "render_coordinator_status"]


AgentState = Literal["idle", "running", "completed", "failed"]


@dataclass
class AgentStatus:
    name: str
    state: AgentState
    task: str = ""


def _state_color(state: AgentState) -> str:
    return {
        "idle": style.palette.hint_fg,
        "running": style.palette.status_info,
        "completed": style.palette.status_success,
        "failed": style.palette.status_error,
    }.get(state, style.palette.hint_fg)


def _state_glyph(state: AgentState) -> str:
    return {
        "idle": style.ICON_DOT,
        "running": style.ICON_START,
        "completed": style.ICON_SUCCESS,
        "failed": style.ICON_FAILURE,
    }.get(state, style.ICON_DOT)


def render_coordinator_status(agents: List[AgentStatus]) -> Panel:
    table = Table.grid(padding=(0, 2), expand=True)
    table.add_column(justify="left", no_wrap=True)
    table.add_column(justify="left", no_wrap=True)
    table.add_column(justify="left", no_wrap=False)

    for agent in agents:
        glyph = f"[{_state_color(agent.state)}]{_state_glyph(agent.state)}[/]"
        name = f"[bold {color_for_agent(agent.name)}]{agent.name}[/]"
        task = agent.task or "—"
        table.add_row(glyph, name, task)

    return Panel(
        table,
        title=f"[bold {style.palette.brand_accent}]swarm coordinator[/]",
        border_style=style.palette.brand_accent,
        padding=(0, 1),
    )

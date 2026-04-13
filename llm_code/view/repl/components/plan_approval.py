"""Plan approval + task assignment message renderers (M15 Task E3)."""
from __future__ import annotations

from rich.panel import Panel
from rich.text import Text

from llm_code.view.repl import style

__all__ = ["render_plan_approval", "render_task_assignment"]


def render_plan_approval(plan_text: str) -> Panel:
    return Panel(
        Text(plan_text, style=style.palette.assistant_fg),
        title=f"[bold {style.palette.mode_plan_fg}]plan awaiting approval[/]",
        border_style=style.palette.mode_plan_fg,
        padding=(1, 2),
    )


def render_task_assignment(task_id: str, title: str) -> Text:
    out = Text()
    out.append("▶ task ", style=style.palette.status_info)
    out.append(f"{task_id}", style=f"bold {style.palette.command_fg}")
    out.append(f" — {title}", style=style.palette.system_fg)
    return out

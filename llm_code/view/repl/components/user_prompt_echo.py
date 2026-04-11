"""User prompt echo renderer (M15 Task C2).

Produces ``> <text>`` in bright white so the user's submitted
input appears in scrollback with clear semantic prefix distinct
from the assistant ``● `` bullet.
"""
from __future__ import annotations

from rich.text import Text

from llm_code.view.repl import style

__all__ = ["render_user_prompt_echo"]


def render_user_prompt_echo(body: str) -> Text:
    out = Text()
    out.append("> ", style=f"bold {style.palette.user_prefix}")
    out.append(body, style=style.palette.user_fg)
    return out

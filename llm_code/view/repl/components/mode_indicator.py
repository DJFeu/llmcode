"""Mode indicator label renderer (M15 Task A6).

Renders the right-side mode label (``[plan]`` / ``[yolo]`` /
``[bash]`` / ``[vim:NORMAL]``) that appears at the end of the
footer hint row.
"""
from __future__ import annotations

from typing import List, Tuple

from llm_code.view.repl import style

__all__ = ["ModeIndicator"]


class ModeIndicator:
    """Renders a ``[mode]`` label using the palette mode colors."""

    def __init__(self) -> None:
        self._mode: str = "prompt"
        self._vim_sub: str | None = None

    def set_mode(self, mode: str, *, vim_sub: str | None = None) -> None:
        self._mode = mode
        self._vim_sub = vim_sub

    def render(self) -> List[Tuple[str, str]]:
        label_color = self._color_for(self._mode)
        if self._mode == "vim" and self._vim_sub:
            body = f"[vim:{self._vim_sub}]"
        else:
            body = f"[{self._mode}]"
        return [(f"fg:{label_color} bold", body)]

    @staticmethod
    def _color_for(mode: str) -> str:
        mapping = {
            "plan": style.palette.mode_plan_fg,
            "yolo": style.palette.mode_yolo_fg,
            "bash": style.palette.mode_bash_fg,
            "vim": style.palette.mode_vim_fg,
        }
        return mapping.get(mode, style.palette.hint_fg)

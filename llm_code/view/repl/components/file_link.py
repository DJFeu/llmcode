"""OSC8 hyperlink helper for file paths (M15 Task D4).

Wraps a file path in an OSC8 ``file://`` hyperlink envelope and
applies the file-path palette tone. Terminals that don't understand
OSC8 render the raw path.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

from rich.text import Text

from llm_code.view.repl import style

__all__ = ["render_path"]


def render_path(path: Union[str, Path]) -> Text:
    p = Path(path)
    try:
        abs_path = p.resolve()
    except Exception:
        abs_path = p
    url = f"file://{abs_path}"
    # Rich Text supports a ``meta`` dict with ``@click`` for local
    # actions, plus the ``link`` parameter for OSC8 emission.
    text = Text(str(p), style=style.palette.file_path_fg)
    try:
        text.stylize(f"link {url}")
    except Exception:
        pass
    return text

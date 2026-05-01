"""Context-window fill meter for the status line (M15 Task A5).

Produces a ``N/M tok ▁▃▅▇█`` fragment with the 5-block bar colored
by :func:`llm_code.view.repl.style.context_color` — green under
60% fill, yellow 60-80%, red above 80%.
"""
from __future__ import annotations

from typing import List, Tuple

from llm_code.view.repl import style

__all__ = ["render_context_meter"]

# Filled and empty cell glyphs for the 5-wide bar.
_FILLED = "█"
_EMPTY = "░"


def _format_compact(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 10000:
        return f"{n / 1000:.1f}k"
    return f"{n // 1000}k"


def render_context_meter(
    used: int,
    limit: int,
    *,
    compact: bool = False,
) -> List[Tuple[str, str]]:
    """Return a PT-compatible style list for ``N/M tok ▁▃▅▇█``.

    The bar is always 5 characters wide; each block is filled
    proportionally to the current fill ratio. The whole fragment
    uses the graded status color from
    :func:`style.context_color`.
    """
    if limit <= 0:
        return [(f"fg:{style.palette.status_dim}", f"{used}/? tok")]
    pct = used / limit
    color = style.context_color(pct)
    # Number of fully-filled bar cells.
    filled = min(5, max(0, int(round(pct * 5))))
    bar_chars = [_FILLED if i < filled else _EMPTY for i in range(5)]
    if compact:
        label = f"{_format_compact(used)}/{_format_compact(limit)} tok "
    else:
        label = f"{used}/{limit} tok "
    bar = "".join(bar_chars)
    # Emit the number in token_count color, the bar in the graded color.
    return [
        (f"fg:{style.palette.token_count_fg}", label),
        (f"fg:{color} bold", bar),
    ]

"""Shimmer-text helper for the status line (M15 Task A4).

Turns a plain string into a per-char gradient cycle, suitable for
feeding into prompt_toolkit's ``FormattedText`` API (the status
line's bottom-toolbar).

The core math lives in :mod:`llm_code.view.repl.style`
(``shimmer_color``, ``shimmer_phase_for_time``). This module
adds:

- per-char phase offset so neighboring characters show adjacent
  gradient stops (i.e. the shimmer appears to "slide" across the
  string)
- a 100 ms recomputation cache keyed by (text, coarse-time) so
  the status line can redraw many times per second without
  burning CPU on shimmer math
"""
from __future__ import annotations

import time
from typing import List, Tuple

from llm_code.view.repl import style

__all__ = ["shimmer_text", "reset_cache"]


_CACHE_WINDOW_S = 0.1  # 100 ms — 10 fps is plenty for a slow shimmer
_cache: dict[tuple[str, int], list[tuple[str, str]]] = {}


def reset_cache() -> None:
    """Clear the shimmer cache (used in tests)."""
    _cache.clear()


def shimmer_text(
    text: str,
    *,
    now: float | None = None,
    period: float = 2.4,
    per_char_offset: float = 0.04,
) -> List[Tuple[str, str]]:
    """Return a PT ``FormattedText``-compatible per-char style list.

    Each character gets a hex color pulled from the brand shimmer
    ramp at ``phase + i * per_char_offset`` mod 1.0. The phase
    updates with wall-clock time via
    :func:`style.shimmer_phase_for_time`.

    Parameters
    ----------
    text:
        Source string. Empty strings return an empty list.
    now:
        Wall-clock seconds (defaults to ``time.monotonic()``).
        Passed for testability.
    period:
        Full shimmer cycle in seconds.
    per_char_offset:
        Phase increment per character — small values make the
        shimmer look like a smooth wave across the string; larger
        values produce a faster traveling highlight.
    """
    if not text:
        return []
    t = time.monotonic() if now is None else now
    bucket = int(t / _CACHE_WINDOW_S)
    cache_key = (text, bucket)
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    base_phase = style.shimmer_phase_for_time(t, period=period)
    out: list[tuple[str, str]] = []
    for i, ch in enumerate(text):
        phase = (base_phase + i * per_char_offset) % 1.0
        color = style.shimmer_color(phase)
        out.append((f"fg:{color}", ch))
    _cache[cache_key] = out
    return out

"""Type-specific exponential decay scoring for typed memory entries.

The default decay rates roughly correspond to the half-lives:

- USER / FEEDBACK: ~140 day half-life (slow — reference-like personal facts)
- PROJECT:        ~35 day half-life  (medium — fast-moving project state)
- REFERENCE:      no decay            (timeless reference material)

A floor multiplier prevents very old high-relevance entries from being
completely buried.
"""
from __future__ import annotations

import math
import time as _time
from datetime import datetime
from typing import Union

from llm_code.runtime.memory_taxonomy import MemoryType

# Hardcoded fallback defaults — overridden by ``MemoryDecayConfig``.
DECAY_LAMBDA: dict[MemoryType, float] = {
    MemoryType.USER: 0.005,       # ~140 day half-life
    MemoryType.FEEDBACK: 0.005,   # ~140 day half-life
    MemoryType.PROJECT: 0.02,     # ~35 day half-life
    MemoryType.REFERENCE: 0.0,    # never decays
}

DEFAULT_FLOOR = 0.1


def decay_factor(
    memory_type: MemoryType,
    age_days: float,
    lambda_override: float | None = None,
) -> float:
    """Return exponential decay multiplier in [0, 1].

    ``lambda_override`` lets callers pass a config-derived rate; otherwise
    the hardcoded :data:`DECAY_LAMBDA` fallback is used.
    """
    lam = lambda_override if lambda_override is not None else DECAY_LAMBDA.get(
        memory_type, 0.01
    )
    if lam <= 0:
        return 1.0
    age = max(0.0, age_days)
    return math.exp(-lam * age)


def _to_epoch(created_at: Union[float, int, str]) -> float:
    """Coerce a created_at value (epoch float or ISO-8601 string) to epoch seconds."""
    if isinstance(created_at, (int, float)):
        return float(created_at)
    if not created_at:
        return _time.time()
    try:
        return datetime.fromisoformat(str(created_at)).timestamp()
    except ValueError:
        return _time.time()


def apply_decay(
    score: float,
    memory_type: MemoryType,
    created_at: Union[float, int, str],
    now: float | None = None,
    floor: float = DEFAULT_FLOOR,
    lambda_override: float | None = None,
) -> float:
    """Apply decay to a similarity score, with a minimum floor multiplier.

    Final score = ``score * max(decay_factor(...), floor)``. The floor
    prevents very old but highly relevant entries from being buried.
    """
    now_ts = now if now is not None else _time.time()
    created_ts = _to_epoch(created_at)
    age_days = max(0.0, (now_ts - created_ts) / 86400.0)
    return score * max(decay_factor(memory_type, age_days, lambda_override), floor)

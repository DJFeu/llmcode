"""Auto-compaction policy with configurable thresholds.

Wraps the existing manual ``compact_session`` so the conversation runtime
can decide when to fire compaction automatically — after each turn we ask
``should_compact`` whether the context has crossed the trigger threshold.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class CompactionThresholds:
    """Configurable knobs for the auto-compaction policy."""

    trigger_pct: float = 0.85       # auto-fire when context usage crosses this %
    min_messages: int = 30          # don't compact tiny conversations
    min_text_blocks: int = 10       # need enough text to be worth compacting
    target_pct: float = 0.50        # compact down to ~this fraction of max


def _count_text_blocks(messages: Sequence[Any]) -> int:
    n = 0
    for msg in messages:
        content = getattr(msg, "content", ())
        for block in content:
            if hasattr(block, "text"):
                n += 1
    return n


def should_compact(
    messages: Sequence[Any],
    used_tokens: int,
    max_tokens: int,
    thresholds: CompactionThresholds,
) -> bool:
    """Return True when auto-compaction should fire for the given context state."""
    if max_tokens <= 0:
        return False
    if len(messages) < thresholds.min_messages:
        return False
    if _count_text_blocks(messages) < thresholds.min_text_blocks:
        return False
    return (used_tokens / max_tokens) >= thresholds.trigger_pct


def target_token_count(max_tokens: int, thresholds: CompactionThresholds) -> int:
    """The token budget we want the compacted conversation to fit into."""
    return int(max_tokens * thresholds.target_pct)


def compact_messages(session: Any, target_tokens: int) -> Any:
    """Compact ``session`` toward ``target_tokens``.

    Delegates to the existing ``compact_session`` helper, choosing a
    ``keep_recent`` window proportional to the target.
    """
    from llm_code.runtime.compaction import compact_session

    # Heuristic: keep ~1 message per 2k target tokens, bounded [4, 32].
    keep_recent = max(4, min(32, target_tokens // 2000))
    return compact_session(session, keep_recent=keep_recent, summary="auto-compacted")

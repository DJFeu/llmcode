"""Auto-compaction policy with configurable thresholds.

Wraps the existing manual ``compact_session`` so the conversation runtime
can decide when to fire compaction automatically — after each turn we ask
``should_compact`` whether the context has crossed the trigger threshold.

A lightweight circuit breaker (``AutoCompactState``) rides alongside the
thresholds. When compaction fails repeatedly the runtime should stop
retrying — burning the whole context window in a failure loop is worse
than giving up and letting the caller hit the hard context limit
(which can then fall through to ``compact_with_todo_preserve`` or a
fresh session).
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

    # C4: circuit-breaker + output-token reserve
    max_consecutive_failures: int = 3
    output_token_reserve: int = 20_000


class AutoCompactState:
    """Per-runtime failure counter for the auto-compaction circuit breaker.

    Lifetime: one instance per ``ConversationRuntime``. Reset implicitly
    on successful compaction; incremented on every failure. Once
    ``failure_count`` crosses the threshold ``should_compact`` returns
    False and the runtime will fall back to other compaction layers
    (API-reported compact, token-upgrade retry, or a hard stop).
    """

    __slots__ = ("_failures",)

    def __init__(self) -> None:
        self._failures: int = 0

    @property
    def failure_count(self) -> int:
        return self._failures

    def record_failure(self) -> None:
        self._failures += 1

    def record_success(self) -> None:
        self._failures = 0

    def is_blocked(self, max_consecutive_failures: int) -> bool:
        return self._failures >= max_consecutive_failures


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
    state: AutoCompactState | None = None,
) -> bool:
    """Return True when auto-compaction should fire for the given context state.

    ``state`` is optional for backward compatibility — callers that do
    not maintain a failure counter behave exactly as before. When a
    state is supplied, the circuit breaker can veto compaction even
    when the usage threshold is crossed.
    """
    if max_tokens <= 0:
        return False
    if len(messages) < thresholds.min_messages:
        return False
    if _count_text_blocks(messages) < thresholds.min_text_blocks:
        return False
    if state is not None and state.is_blocked(thresholds.max_consecutive_failures):
        return False
    return (used_tokens / max_tokens) >= thresholds.trigger_pct


def target_token_count(max_tokens: int, thresholds: CompactionThresholds) -> int:
    """The token budget we want the compacted conversation to fit into.

    Reserves ``output_token_reserve`` tokens for the response that
    triggered the compaction so the model still has headroom to emit
    a full turn without hitting ``max_tokens`` mid-reply.
    """
    effective = max(0, max_tokens - thresholds.output_token_reserve)
    return int(effective * thresholds.target_pct)


def compact_messages(session: Any, target_tokens: int) -> Any:
    """Compact ``session`` toward ``target_tokens``.

    Delegates to the existing ``compact_session`` helper, choosing a
    ``keep_recent`` window proportional to the target.
    """
    from llm_code.runtime.compaction import compact_session

    # Heuristic: keep ~1 message per 2k target tokens, bounded [4, 32].
    keep_recent = max(4, min(32, target_tokens // 2000))
    return compact_session(session, keep_recent=keep_recent, summary="auto-compacted")

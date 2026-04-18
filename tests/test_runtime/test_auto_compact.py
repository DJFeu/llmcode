"""Tests for auto-compaction policy."""
from __future__ import annotations

from llm_code.api.types import (
    Message,
    StreamCompactionDone,
    StreamCompactionStart,
    TextBlock,
)
from llm_code.runtime.auto_compact import (
    CompactionThresholds,
    compact_messages,
    should_compact,
    target_token_count,
)
from llm_code.runtime.session import Session


def _msgs(n: int) -> list[Message]:
    return [
        Message(role="user", content=(TextBlock(text=f"msg {i} " * 50),))
        for i in range(n)
    ]


def test_should_compact_skips_small_conversations():
    th = CompactionThresholds()
    assert should_compact(_msgs(5), used_tokens=900_000, max_tokens=1_000_000, thresholds=th) is False


def test_should_compact_skips_when_under_threshold():
    th = CompactionThresholds()
    assert should_compact(_msgs(40), used_tokens=10_000, max_tokens=1_000_000, thresholds=th) is False


def test_should_compact_fires_at_85_pct():
    th = CompactionThresholds()
    assert should_compact(_msgs(40), used_tokens=850_000, max_tokens=1_000_000, thresholds=th) is True


def test_should_compact_zero_max_safe():
    th = CompactionThresholds()
    assert should_compact(_msgs(40), used_tokens=10, max_tokens=0, thresholds=th) is False


def test_should_compact_min_text_blocks():
    th = CompactionThresholds(min_text_blocks=10, min_messages=1)
    few = [Message(role="user", content=()) for _ in range(50)]
    assert should_compact(few, used_tokens=900_000, max_tokens=1_000_000, thresholds=th) is False


def test_target_token_count():
    # Pin output_token_reserve=0 so this case isolates the target_pct math;
    # the reserve behaviour is covered separately in
    # ``test_output_token_reserve_shrinks_target``.
    th = CompactionThresholds(target_pct=0.5, output_token_reserve=0)
    assert target_token_count(200_000, th) == 100_000


def test_compact_messages_delegates_to_compact_session():
    from llm_code.api.types import TokenUsage

    session = Session(
        id="s",
        created_at=0.0,
        updated_at=0.0,
        messages=tuple(_msgs(20)),
        total_usage=TokenUsage(input_tokens=0, output_tokens=0),
        project_path="/tmp",
    )
    compacted = compact_messages(session, target_tokens=10_000)
    assert len(compacted.messages) < len(session.messages)


def test_stream_compaction_events_constructible():
    s = StreamCompactionStart(used_tokens=850_000, max_tokens=1_000_000)
    d = StreamCompactionDone(before_messages=40, after_messages=6)
    assert s.used_tokens == 850_000
    assert d.after_messages == 6


# ---------- C4: Circuit breaker + output-token reserve ----------


def test_output_token_reserve_shrinks_target():
    """Reserving output tokens should reduce the compaction target budget.

    Without a reserve the target for a 200K context at target_pct=0.5 is 100K.
    Reserving 20K should leave room for ~90K post-compact content so the
    model still has 20K of headroom for the response that triggered the
    compaction in the first place.
    """
    from llm_code.runtime.auto_compact import target_token_count

    th_no_reserve = CompactionThresholds(target_pct=0.5, output_token_reserve=0)
    assert target_token_count(200_000, th_no_reserve) == 100_000

    th_with_reserve = CompactionThresholds(target_pct=0.5, output_token_reserve=20_000)
    # (200_000 - 20_000) * 0.5 == 90_000
    assert target_token_count(200_000, th_with_reserve) == 90_000


def test_auto_compact_state_starts_unblocked():
    from llm_code.runtime.auto_compact import AutoCompactState

    state = AutoCompactState()
    assert state.failure_count == 0
    assert state.is_blocked(max_consecutive_failures=3) is False


def test_auto_compact_state_records_failures():
    from llm_code.runtime.auto_compact import AutoCompactState

    state = AutoCompactState()
    state.record_failure()
    state.record_failure()
    assert state.failure_count == 2
    assert state.is_blocked(max_consecutive_failures=3) is False
    state.record_failure()
    assert state.failure_count == 3
    assert state.is_blocked(max_consecutive_failures=3) is True


def test_auto_compact_state_resets_on_success():
    from llm_code.runtime.auto_compact import AutoCompactState

    state = AutoCompactState()
    state.record_failure()
    state.record_failure()
    state.record_success()
    assert state.failure_count == 0
    assert state.is_blocked(max_consecutive_failures=3) is False


def test_should_compact_honors_circuit_breaker():
    """After too many consecutive failures, should_compact must return False
    even when the usage threshold is crossed — the model is clearly stuck and
    retrying compaction just burns context window."""
    from llm_code.runtime.auto_compact import AutoCompactState

    th = CompactionThresholds(max_consecutive_failures=3)
    state = AutoCompactState()
    for _ in range(3):
        state.record_failure()

    assert state.is_blocked(max_consecutive_failures=th.max_consecutive_failures) is True
    assert (
        should_compact(
            _msgs(40),
            used_tokens=900_000,
            max_tokens=1_000_000,
            thresholds=th,
            state=state,
        )
        is False
    )


def test_should_compact_still_fires_below_breaker():
    """Two failures is below the breaker threshold — a third compaction
    attempt is still allowed."""
    from llm_code.runtime.auto_compact import AutoCompactState

    th = CompactionThresholds(max_consecutive_failures=3)
    state = AutoCompactState()
    state.record_failure()
    state.record_failure()

    assert (
        should_compact(
            _msgs(40),
            used_tokens=900_000,
            max_tokens=1_000_000,
            thresholds=th,
            state=state,
        )
        is True
    )


def test_should_compact_accepts_none_state_for_backward_compat():
    """Existing callers that don't pass `state` must keep working."""
    th = CompactionThresholds(max_consecutive_failures=3)
    assert (
        should_compact(
            _msgs(40),
            used_tokens=900_000,
            max_tokens=1_000_000,
            thresholds=th,
        )
        is True
    )

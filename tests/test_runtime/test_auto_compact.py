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
    th = CompactionThresholds(target_pct=0.5)
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

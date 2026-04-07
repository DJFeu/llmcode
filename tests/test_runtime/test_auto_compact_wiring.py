"""Wiring tests for auto-compaction into the conversation turn loop.

These tests don't spin up a full ConversationRuntime — instead they verify
the wiring contract: the config flag exists, the stream event types exist
and carry the right fields, and the should_compact policy fires as expected
for a synthetic conversation that crosses the threshold (and doesn't fire
below it or while a compaction is already in-flight).
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from llm_code.api.types import StreamCompactionDone, StreamCompactionStart
from llm_code.runtime.auto_compact import (
    CompactionThresholds,
    compact_messages,
    should_compact,
)
from llm_code.runtime.config import CompactionConfig, CompactionThresholdsConfig


# ---------- synthetic message helpers -----------------------------------

@dataclass
class _Text:
    text: str


@dataclass
class _Msg:
    content: tuple


def _msgs(n: int) -> list[_Msg]:
    return [_Msg(content=(_Text(text=f"hello {i}"),)) for i in range(n)]


# ---------- stream event wiring -----------------------------------------

def test_stream_compaction_events_exist():
    s = StreamCompactionStart(used_tokens=100, max_tokens=200)
    d = StreamCompactionDone(before_messages=40, after_messages=10)
    assert s.used_tokens == 100 and s.max_tokens == 200
    assert d.before_messages == 40 and d.after_messages == 10


# ---------- config wiring -----------------------------------------------

def test_runtime_config_has_compaction_field():
    from llm_code.runtime.config import RuntimeConfig
    cfg = RuntimeConfig()
    assert hasattr(cfg, "compaction")
    assert isinstance(cfg.compaction, CompactionConfig)
    assert cfg.compaction.auto_enabled is False  # default off
    assert isinstance(cfg.compaction.thresholds, CompactionThresholdsConfig)


# ---------- policy -------------------------------------------------------

def test_should_compact_fires_over_threshold():
    t = CompactionThresholds(trigger_pct=0.8, min_messages=30, min_text_blocks=10)
    assert should_compact(_msgs(40), used_tokens=90, max_tokens=100, thresholds=t)


def test_should_compact_skips_below_threshold():
    t = CompactionThresholds(trigger_pct=0.8, min_messages=30, min_text_blocks=10)
    assert not should_compact(_msgs(40), used_tokens=50, max_tokens=100, thresholds=t)


def test_should_compact_skips_tiny_conversations():
    t = CompactionThresholds(trigger_pct=0.8, min_messages=30, min_text_blocks=10)
    # crosses % threshold but too few messages → skip
    assert not should_compact(_msgs(5), used_tokens=99, max_tokens=100, thresholds=t)


# ---------- in-flight guard ---------------------------------------------

def test_conversation_runtime_has_compaction_inflight_flag():
    """The turn loop must expose a guard so auto-compact can't double-fire."""
    from llm_code.runtime.conversation import ConversationRuntime
    import inspect
    src = inspect.getsource(ConversationRuntime)
    assert "_compaction_in_flight" in src
    assert "StreamCompactionStart" in src
    assert "StreamCompactionDone" in src


# ---------- compact_messages delegates to compact_session ---------------

def test_compact_messages_delegates_to_compact_session():
    from llm_code.runtime.session import Session
    from llm_code.api.types import Message, TextBlock
    msgs = tuple(
        Message(role="user", content=(TextBlock(text=f"hi {i}"),)) for i in range(20)
    )
    from datetime import datetime, timezone
    from llm_code.api.types import TokenUsage
    now = datetime.now(timezone.utc)
    session = Session(
        id="s", messages=msgs, created_at=now, updated_at=now,
        total_usage=TokenUsage(0, 0), project_path="/",
    )
    result = compact_messages(session, target_tokens=8000)
    # compact_session returns a session with summary + keep_recent tail
    assert len(result.messages) < len(session.messages)

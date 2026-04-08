"""Tests for the context_window_monitor builtin hook."""
from __future__ import annotations

import pytest

from llm_code.runtime.builtin_hooks import context_window_monitor
from llm_code.runtime.hooks import HookRunner


@pytest.fixture(autouse=True)
def _clear_state():
    context_window_monitor._WARNED_SESSIONS.clear()
    yield
    context_window_monitor._WARNED_SESSIONS.clear()


def _ctx(used: int, mx: int, sid: str = "s1") -> dict:
    return {
        "tool_name": "bash",
        "tool_output": "ok",
        "session_id": sid,
        "tokens_used": used,
        "tokens_max": mx,
    }


def test_below_threshold_returns_none() -> None:
    out = context_window_monitor.handle("post_tool_use", _ctx(used=10, mx=100))
    assert out is None


def test_at_threshold_emits_warning_in_extra_output() -> None:
    out = context_window_monitor.handle("post_tool_use", _ctx(used=80, mx=100))
    assert out is not None
    assert "Context Status" in out.extra_output
    assert "80%" in out.extra_output


def test_warning_only_fires_once_per_session() -> None:
    first = context_window_monitor.handle("post_tool_use", _ctx(used=80, mx=100, sid="s1"))
    second = context_window_monitor.handle("post_tool_use", _ctx(used=90, mx=100, sid="s1"))
    assert first is not None and first.extra_output
    assert second is None


def test_separate_sessions_track_independently() -> None:
    a = context_window_monitor.handle("post_tool_use", _ctx(used=80, mx=100, sid="s1"))
    b = context_window_monitor.handle("post_tool_use", _ctx(used=80, mx=100, sid="s2"))
    assert a is not None and b is not None


def test_session_end_clears_state() -> None:
    context_window_monitor.handle("post_tool_use", _ctx(used=80, mx=100, sid="s1"))
    context_window_monitor.handle("session_end", {"session_id": "s1"})
    out = context_window_monitor.handle("post_tool_use", _ctx(used=80, mx=100, sid="s1"))
    assert out is not None


def test_zero_max_does_not_divide_by_zero() -> None:
    out = context_window_monitor.handle("post_tool_use", _ctx(used=10, mx=0))
    assert out is None


def test_register_subscribes_to_post_tool_use_and_session_end() -> None:
    runner = HookRunner()
    context_window_monitor.register(runner)
    assert "post_tool_use" in runner._subscribers
    assert "session_end" in runner._subscribers

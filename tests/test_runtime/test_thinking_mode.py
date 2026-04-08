"""Tests for the thinking_mode builtin hook."""
from __future__ import annotations

import pytest

from llm_code.runtime.builtin_hooks import thinking_mode
from llm_code.runtime.hooks import HookRunner


@pytest.fixture(autouse=True)
def _reset_state():
    thinking_mode._SESSION_REQUESTS.clear()
    yield
    thinking_mode._SESSION_REQUESTS.clear()


def test_detects_english_keyword_think_harder() -> None:
    ctx = {"prompt": "please think harder about this", "session_id": "s1"}
    out = thinking_mode.handle("prompt_submit", ctx)
    assert out is not None
    assert ctx["thinking_requested"] is True
    assert any("thinking_mode" in m for m in out.messages)


def test_detects_ultrathink() -> None:
    ctx = {"prompt": "ultrathink and propose a fix", "session_id": "s1"}
    assert thinking_mode.handle("prompt_submit", ctx) is not None
    assert ctx.get("thinking_requested") is True


def test_detects_chinese_keyword() -> None:
    ctx = {"prompt": "請深入思考一下這個 bug", "session_id": "s1"}
    assert thinking_mode.handle("prompt_submit", ctx) is not None


def test_no_keyword_returns_none() -> None:
    ctx = {"prompt": "list files", "session_id": "s1"}
    assert thinking_mode.handle("prompt_submit", ctx) is None
    assert "thinking_requested" not in ctx


def test_state_tracks_session() -> None:
    ctx = {"prompt": "think hard", "session_id": "s1"}
    thinking_mode.handle("prompt_submit", ctx)
    assert thinking_mode.was_requested("s1") is True
    assert thinking_mode.was_requested("s2") is False


def test_session_end_clears_state() -> None:
    thinking_mode.handle("prompt_submit", {"prompt": "think hard", "session_id": "s1"})
    thinking_mode.handle("session_end", {"session_id": "s1"})
    assert thinking_mode.was_requested("s1") is False


def test_register_subscribes_both_events() -> None:
    runner = HookRunner()
    thinking_mode.register(runner)
    assert "prompt_submit" in runner._subscribers
    assert "session_end" in runner._subscribers


def test_keyword_match_is_case_insensitive() -> None:
    ctx = {"prompt": "ULTRATHINK", "session_id": "s1"}
    assert thinking_mode.handle("prompt_submit", ctx) is not None

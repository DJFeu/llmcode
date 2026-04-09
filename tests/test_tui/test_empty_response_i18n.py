"""Empty-response diagnostic messages must match the user's input language."""
from __future__ import annotations

import pytest

from llm_code.tui.app import (
    _empty_response_message,
    _is_cjk_dominant,
)


# ---------- _is_cjk_dominant ----------


@pytest.mark.parametrize("text", [
    "今日熱門新聞三則",
    "解釋 quicksort 演算法",
    "為什麼 Python 的 list 是 O(1) append",
    "測試這個檔案",
    "你好,請問怎麼用?",
])
def test_cjk_dominant_detected(text: str) -> None:
    assert _is_cjk_dominant(text) is True


@pytest.mark.parametrize("text", [
    "what is a hash table",
    "explain how async/await works",
    "read foo.py and summarize",
    "grep for 'TODO'",
    "hi",
    "",
])
def test_non_cjk_detected(text: str) -> None:
    assert _is_cjk_dominant(text) is False


def test_empty_string_is_non_cjk() -> None:
    assert _is_cjk_dominant("") is False


def test_whitespace_only_is_non_cjk() -> None:
    assert _is_cjk_dominant("   \n\t ") is False


def test_mixed_heavy_english_with_any_cjk_is_cjk() -> None:
    """Any CJK character in the input flips the verdict to CJK, on the
    theory that if the user typed even one Chinese character they will
    understand a Chinese diagnostic message. Prevents English-biased
    misclassification for multilingual users."""
    text = "please read foo.py and explain the 函式 structure"
    assert _is_cjk_dominant(text) is True


def test_mixed_heavy_cjk_is_cjk() -> None:
    """A mostly-Chinese message with technical English terms stays CJK."""
    text = "解釋 quicksort 演算法的時間複雜度"
    assert _is_cjk_dominant(text) is True


def test_pure_english_ascii_stays_non_cjk() -> None:
    """Pure English with no CJK characters anywhere is non-CJK."""
    text = "please read foo.py and explain the function structure in detail"
    assert _is_cjk_dominant(text) is False


# ---------- _empty_response_message ----------


def test_message_tool_call_english() -> None:
    msg = _empty_response_message(saw_tool_call=True, user_input="what is rest")
    assert "tried to invoke a tool" in msg.lower()
    assert "模型" not in msg


def test_message_tool_call_chinese() -> None:
    msg = _empty_response_message(saw_tool_call=True, user_input="今日熱門新聞三則")
    assert "模型嘗試呼叫工具" in msg
    assert "tried to invoke" not in msg.lower()


def test_message_thinking_english() -> None:
    msg = _empty_response_message(saw_tool_call=False, user_input="explain quicksort")
    assert "thinking may have exhausted" in msg.lower()
    assert "模型" not in msg


def test_message_thinking_chinese() -> None:
    msg = _empty_response_message(saw_tool_call=False, user_input="解釋 quicksort")
    assert "thinking 用光輸出 token" in msg
    assert "exhausted" not in msg.lower()


def test_message_default_for_empty_input_is_english() -> None:
    """Edge case: empty user_input falls through to non-CJK → English."""
    msg = _empty_response_message(saw_tool_call=True, user_input="")
    assert "tried to invoke a tool" in msg.lower()


# ---------- session-aware language detection ----------


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMsg:
    def __init__(self, role: str, text: str) -> None:
        self.role = role
        self.content = (_FakeBlock(text),)


def test_session_chinese_short_followup_stays_chinese() -> None:
    """A user who said 今日熱門新聞三則 then types '1' as a follow-up
    should still see a Chinese diagnostic. Without session-awareness the
    bare '1' would flip back to English."""
    history = (_FakeMsg("user", "今日熱門新聞三則"),)
    msg = _empty_response_message(
        saw_tool_call=True, user_input="1", session_messages=history
    )
    assert "模型嘗試呼叫工具" in msg
    assert "tried to invoke" not in msg.lower()


def test_session_english_only_stays_english() -> None:
    """A user with only English session history typing '1' stays English."""
    history = (
        _FakeMsg("user", "what is rest"),
        _FakeMsg("assistant", "REST is a protocol style for..."),
    )
    msg = _empty_response_message(
        saw_tool_call=True, user_input="1", session_messages=history
    )
    assert "tried to invoke a tool" in msg.lower()
    assert "模型" not in msg


def test_session_assistant_only_chinese_does_not_count() -> None:
    """If only the assistant emitted Chinese (e.g. CLI status messages
    happen to translate), don't infer the user is Chinese."""
    history = (
        _FakeMsg("user", "hello"),
        _FakeMsg("assistant", "你好,有什麼可以幫您"),
    )
    msg = _empty_response_message(
        saw_tool_call=True, user_input="ok", session_messages=history
    )
    assert "tried to invoke a tool" in msg.lower()


def test_session_messages_none_falls_back_to_input_only() -> None:
    """When no session is available, behavior matches the previous
    user_input-only mode."""
    msg = _empty_response_message(
        saw_tool_call=True, user_input="今日新聞", session_messages=None
    )
    assert "模型嘗試呼叫工具" in msg


def test_session_handles_non_iterable_gracefully() -> None:
    """Defensive: a malformed session_messages object should not crash."""
    msg = _empty_response_message(
        saw_tool_call=True, user_input="1", session_messages=42  # type: ignore[arg-type]
    )
    # Should fall through to English (no CJK detected, no session walk)
    assert "tried to invoke a tool" in msg.lower()


# ----- Unclassified variant (empty-response-diagnostics) -----


def test_unclassified_variant_english_with_token_count() -> None:
    """When tokens came back but nothing landed in thinking or visible
    buffers, the message should name the token count so the user can
    sanity-check their max_tokens cap."""
    msg = _empty_response_message(
        saw_tool_call=False,
        user_input="what is 2+2",
        turn_output_tokens=24,
        thinking_buffer_len=0,
    )
    assert "24" in msg
    assert "visible text" in msg or "thinking" in msg
    assert "max_tokens" in msg or "budget" in msg


def test_unclassified_variant_chinese_with_token_count() -> None:
    msg = _empty_response_message(
        saw_tool_call=False,
        user_input="今日熱門新聞三則",
        turn_output_tokens=24,
        thinking_buffer_len=0,
    )
    assert "24" in msg
    assert "max_tokens" in msg or "thinking_budget" in msg
    # Must NOT be the classic thinking-exhausted message
    assert "模型沒有產生任何回應" not in msg


def test_thinking_exhausted_variant_fires_when_buffer_has_content() -> None:
    """If thinking buffer has any content, we're in the classic
    'exhausted' case, not unclassified — even if token count is positive."""
    msg = _empty_response_message(
        saw_tool_call=False,
        user_input="今日熱門新聞三則",
        turn_output_tokens=100,
        thinking_buffer_len=250,
    )
    assert "thinking 用光" in msg or "thinking may have exhausted" in msg
    # Must NOT be the unclassified variant
    assert "24" not in msg  # no token count in this variant


def test_thinking_exhausted_variant_fires_when_zero_tokens() -> None:
    """Zero output tokens + empty thinking = classic 'empty response'
    diagnostic. This is the pre-wave2 default behavior preserved for
    sessions where the turn really produced nothing."""
    msg = _empty_response_message(
        saw_tool_call=False,
        user_input="hello",
        turn_output_tokens=0,
        thinking_buffer_len=0,
    )
    # Falls through to the thinking-exhausted message (default)
    assert "thinking" in msg.lower()


def test_tool_call_variant_takes_precedence_over_unclassified() -> None:
    """Even if tokens came back with no classification, a dispatched
    tool call wins — that's the more actionable diagnostic."""
    msg = _empty_response_message(
        saw_tool_call=True,
        user_input="what is 2+2",
        turn_output_tokens=24,
        thinking_buffer_len=0,
    )
    assert "tool" in msg.lower()
    # The unclassified variant's token count shouldn't leak through
    assert "emitted 24 output" not in msg


def test_unclassified_variant_defaults_preserve_legacy_behavior() -> None:
    """Callers that don't pass the new kwargs (e.g. an old test) must
    still get the classic thinking-exhausted message."""
    msg = _empty_response_message(
        saw_tool_call=False,
        user_input="hello",
    )
    assert "thinking" in msg.lower()
    # Legacy callers don't know about token counts, so no number
    assert "emitted" not in msg.lower()

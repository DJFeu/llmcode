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

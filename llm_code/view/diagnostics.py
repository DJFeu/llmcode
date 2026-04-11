"""Empty-response + truncation diagnostics with CJK-aware language picking.

Relocated from ``tui/app.py`` as part of M11 cutover. These are pure
helpers — no widget dependencies — used by ``ViewStreamRenderer`` to
surface targeted diagnostic messages at the end of a turn that produced
no visible reply (or was truncated mid-generation).

Language picking walks the current input plus recent session messages
for any CJK character; if found, the Chinese message is used. Pure
English users never emit CJK and stay in English.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "_is_cjk_dominant",
    "_session_is_cjk",
    "_empty_response_message",
    "_truncation_warning_message",
]


def _is_cjk_dominant(text: str) -> bool:
    """Return True if the text contains any CJK character.

    Uses an "any CJK present" rule rather than a percentage threshold
    because multilingual users routinely mix English technical terms
    into otherwise-Chinese prompts. If the user typed even one Chinese
    character, they'll understand a Chinese diagnostic message.
    """
    if not text:
        return False
    for ch in text:
        code = ord(ch)
        if (
            0x3000 <= code <= 0x303F
            or 0x3040 <= code <= 0x309F
            or 0x30A0 <= code <= 0x30FF
            or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xAC00 <= code <= 0xD7AF
            or 0xFF00 <= code <= 0xFFEF
        ):
            return True
    return False


_EMPTY_RESPONSE_TOOL_CALL_EN = (
    "(The model tried to invoke a tool to answer this but produced no "
    "visible reply. If this is a general-knowledge or chitchat query, "
    "try rephrasing to ask for a direct answer — e.g. add "
    "\"answer directly\" or \"don't use tools\".)"
)
_EMPTY_RESPONSE_TOOL_CALL_ZH = (
    "(模型嘗試呼叫工具回答這個問題,但沒有產生可見回覆。"
    "如果這是一般知識/閒聊查詢,請試著更明確地表達你想要直接的回答,"
    "例如加上「請直接回答」或「不要用工具」。)"
)
_EMPTY_RESPONSE_THINKING_EN = (
    "(The model produced no visible reply — thinking may have exhausted "
    "the output token budget. Try rephrasing or increasing the context "
    "window.)"
)
_EMPTY_RESPONSE_THINKING_ZH = (
    "(模型沒有產生任何回應 — 可能 thinking 用光輸出 token。"
    "試試重新表達或加長 context window。)"
)
_EMPTY_RESPONSE_UNCLASSIFIED_EN = (
    "(The model emitted {n} output token(s) but none were visible text, "
    "thinking, or a dispatched tool call. This is usually a truncated "
    "response — check max_tokens / thinking_budget or rerun with -v to "
    "capture the raw stream.)"
)
_EMPTY_RESPONSE_UNCLASSIFIED_ZH = (
    "(模型輸出了 {n} 個 token,但全部都不是可見文字、thinking 內容,"
    "也不是成功派發的工具呼叫。通常是輸出被截斷 — 檢查 max_tokens / "
    "thinking_budget 設定,或用 -v 重跑以擷取 raw stream。)"
)


def _session_is_cjk(user_input: str, session_messages: Any = None) -> bool:
    """Decide whether to use CJK messages based on current input + session.

    A user who said "今日熱門新聞三則" earlier and then types "1" or
    "ok" is still a CJK user — we shouldn't flip back to English just
    because the latest input has no CJK characters. Scan the latest
    input first; if not CJK, scan up to the last 20 session messages
    for any CJK character on the user side.
    """
    if _is_cjk_dominant(user_input):
        return True
    if session_messages is None:
        return False
    try:
        recent = list(session_messages)[-20:]
    except TypeError:
        return False
    for msg in recent:
        if getattr(msg, "role", None) != "user":
            continue
        content = getattr(msg, "content", None) or ()
        for block in content:
            text = getattr(block, "text", None)
            if text and _is_cjk_dominant(text):
                return True
    return False


def _truncation_warning_message(
    *,
    stop_reason: str,
    turn_output_tokens: int,
    user_input: str,
    session_messages: Any = None,
) -> str:
    """Warning shown when the provider reports ``length``/``max_tokens``
    and some visible content was already emitted. Pure helper so tests
    can exercise the i18n logic without a full TUI.
    """
    zh = _session_is_cjk(user_input, session_messages)
    if zh:
        return (
            f"(⚠ 回應被截斷 — 模型達到輸出上限 ({stop_reason})。"
            f"實際輸出 {turn_output_tokens} tokens。"
            f"試試加長 max_tokens 或 context window,或重新提問。)"
        )
    return (
        f"(⚠ Response truncated — the model hit its output "
        f"token cap ({stop_reason}) after {turn_output_tokens} "
        f"tokens. Try increasing max_tokens / context window "
        f"or rephrasing.)"
    )


def _empty_response_message(
    *,
    saw_tool_call: bool,
    user_input: str,
    session_messages: Any = None,
    turn_output_tokens: int = 0,
    thinking_buffer_len: int = 0,
) -> str:
    """Pick the right empty-response diagnostic, matching language (CJK
    vs non-CJK) and the *reason* the visible buffer is empty.

    Decision tree:

    1. Tool dispatch (``saw_tool_call=True``) but no visible reply → the
       tool-call variant. Common when the model tries a tool for a
       query that didn't need one.
    2. Output tokens > 0 AND thinking buffer empty → the
       ``unclassified`` variant. Tokens came back but we couldn't
       classify them — usually a truncated response. The message
       includes the token count so the user can compare to
       ``max_tokens`` / ``thinking_budget``.
    3. Otherwise → the classic "thinking exhausted the budget" variant.
    """
    zh = _session_is_cjk(user_input, session_messages)
    if saw_tool_call:
        return (
            _EMPTY_RESPONSE_TOOL_CALL_ZH if zh
            else _EMPTY_RESPONSE_TOOL_CALL_EN
        )
    if turn_output_tokens > 0 and thinking_buffer_len == 0:
        template = (
            _EMPTY_RESPONSE_UNCLASSIFIED_ZH if zh
            else _EMPTY_RESPONSE_UNCLASSIFIED_EN
        )
        return template.format(n=turn_output_tokens)
    return (
        _EMPTY_RESPONSE_THINKING_ZH if zh
        else _EMPTY_RESPONSE_THINKING_EN
    )

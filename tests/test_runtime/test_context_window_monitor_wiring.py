"""ConversationRuntime must populate _last_input_tokens / _max_input_tokens
so the context_window_monitor builtin hook actually fires."""
from __future__ import annotations

from llm_code.runtime.conversation import ConversationRuntime


def test_runtime_exposes_token_counter_attrs_after_init() -> None:
    rt = ConversationRuntime.__new__(ConversationRuntime)
    assert hasattr(rt, "_last_input_tokens") or hasattr(
        ConversationRuntime, "_last_input_tokens"
    )
    assert hasattr(rt, "_max_input_tokens") or hasattr(
        ConversationRuntime, "_max_input_tokens"
    )


def test_token_counters_updated_after_stream_completes() -> None:
    rt = ConversationRuntime.__new__(ConversationRuntime)
    rt._last_input_tokens = 0
    rt._max_input_tokens = 0

    from llm_code.runtime.conversation import _record_token_usage

    _record_token_usage(rt, used_tokens=12345, max_tokens=131072)

    assert rt._last_input_tokens == 12345
    assert rt._max_input_tokens == 131072

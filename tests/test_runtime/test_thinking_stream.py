"""Tests for thinking stream events and conversation runtime thinking config."""
from __future__ import annotations

import dataclasses

from llm_code.api.types import StreamThinkingDelta, StreamEvent


class TestStreamThinkingDelta:
    def test_is_stream_event(self):
        evt = StreamThinkingDelta(text="considering options...")
        assert isinstance(evt, StreamEvent)

    def test_text_field(self):
        evt = StreamThinkingDelta(text="step 1")
        assert evt.text == "step 1"

    def test_frozen(self):
        import pytest
        evt = StreamThinkingDelta(text="hi")
        with pytest.raises(dataclasses.FrozenInstanceError):
            evt.text = "bye"  # type: ignore[misc]


class TestThinkingExtraBody:
    """Verify that thinking config produces correct extra_body for MessageRequest."""

    def test_adaptive_mode_extra_body(self):
        from llm_code.runtime.config import ThinkingConfig
        cfg = ThinkingConfig(mode="adaptive", budget_tokens=10000)
        # adaptive: let the provider decide — no thinking override
        body = _build_thinking_extra_body(cfg)
        assert body is None

    def test_enabled_mode_extra_body(self):
        from llm_code.runtime.config import ThinkingConfig
        cfg = ThinkingConfig(mode="enabled", budget_tokens=25000)
        body = _build_thinking_extra_body(cfg)
        assert body == {"chat_template_kwargs": {"enable_thinking": True, "thinking_budget": 25000}}

    def test_disabled_mode_extra_body(self):
        from llm_code.runtime.config import ThinkingConfig
        cfg = ThinkingConfig(mode="disabled")
        body = _build_thinking_extra_body(cfg)
        assert body == {"chat_template_kwargs": {"enable_thinking": False}}


def _build_thinking_extra_body(cfg) -> dict | None:
    """Import the helper from conversation module."""
    from llm_code.runtime.conversation import build_thinking_extra_body
    return build_thinking_extra_body(cfg)

"""Wave2-1a P4: outbound _convert_message explicit thinking drop + warn-once.

P3 pinned that ``_convert_message`` does not crash on a ThinkingBlock.
P4 makes the drop explicit: instead of silently falling through the
has_multiple branch, we count the dropped blocks and fire a one-shot
debug log the first time any request sends thinking to an openai-compat
server. The drop is expected behaviour (protocol mismatch, not user-
actionable) so the log lives at DEBUG — loud enough for bug reports,
quiet enough to stay out of the user-visible WARNING stream.
"""
from __future__ import annotations

import logging

import pytest

import llm_code.api.openai_compat as openai_compat_module
from llm_code.api.openai_compat import OpenAICompatProvider
from llm_code.api.types import Message, TextBlock, ThinkingBlock


@pytest.fixture(autouse=True)
def _reset_warn_once() -> None:
    """The drop warning fires exactly once per process — reset it
    between tests so each test gets a clean slate."""
    openai_compat_module._thinking_drop_warned = False


def _make_provider() -> OpenAICompatProvider:
    return OpenAICompatProvider(base_url="http://localhost:0", api_key="")


def test_outbound_drop_logs_warning_on_first_occurrence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = _make_provider()
    msg = Message(
        role="assistant",
        content=(
            ThinkingBlock(content="hidden reasoning"),
            TextBlock(text="visible answer"),
        ),
    )
    with caplog.at_level(logging.DEBUG, logger="llm_code.api.openai_compat"):
        provider._convert_message(msg)
    warnings = [r for r in caplog.records if "dropping" in r.message and "thinking" in r.message]
    assert len(warnings) == 1
    assert "1 thinking block" in warnings[0].message


def test_outbound_drop_warns_only_once_across_many_requests(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A multi-turn session with a reasoning model would otherwise
    emit this warning on every request — the one-shot flag prevents
    the log from drowning in duplicates."""
    provider = _make_provider()
    msg = Message(
        role="assistant",
        content=(
            ThinkingBlock(content="reasoning"),
            TextBlock(text="answer"),
        ),
    )
    with caplog.at_level(logging.DEBUG, logger="llm_code.api.openai_compat"):
        for _ in range(10):
            provider._convert_message(msg)
    warnings = [r for r in caplog.records if "dropping" in r.message]
    assert len(warnings) == 1


def test_outbound_drop_count_reflects_multiple_thinking_blocks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Anthropic may split a long reasoning trace across consecutive
    thinking blocks. The warning should report the accurate count."""
    provider = _make_provider()
    msg = Message(
        role="assistant",
        content=(
            ThinkingBlock(content="first"),
            ThinkingBlock(content="second"),
            ThinkingBlock(content="third"),
            TextBlock(text="answer"),
        ),
    )
    with caplog.at_level(logging.DEBUG, logger="llm_code.api.openai_compat"):
        provider._convert_message(msg)
    warnings = [r for r in caplog.records if "dropping" in r.message]
    assert len(warnings) == 1
    assert "3 thinking block" in warnings[0].message


def test_outbound_message_without_thinking_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-reasoning-provider sessions should produce zero thinking
    drop warnings — the hot path stays silent."""
    provider = _make_provider()
    msg = Message(
        role="assistant",
        content=(
            TextBlock(text="just text"),
        ),
    )
    with caplog.at_level(logging.DEBUG, logger="llm_code.api.openai_compat"):
        provider._convert_message(msg)
    warnings = [r for r in caplog.records if "dropping" in r.message]
    assert warnings == []


def test_outbound_drop_preserves_text_content_in_payload() -> None:
    """The visible text must still be sent to the provider — the P4
    warning is purely observability, not a behavior change."""
    provider = _make_provider()
    msg = Message(
        role="assistant",
        content=(
            ThinkingBlock(content="thinking"),
            TextBlock(text="visible"),
        ),
    )
    result = provider._convert_message(msg)
    parts = result.get("content")
    assert isinstance(parts, list)
    text_parts = [p for p in parts if p.get("type") == "text"]
    assert len(text_parts) == 1
    assert text_parts[0]["text"] == "visible"

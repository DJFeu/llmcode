"""Tests for Wave2-1a thinking_order recovery."""
from __future__ import annotations

import pytest

from llm_code.api.types import TextBlock, ThinkingBlock, ToolUseBlock
from llm_code.runtime.recovery.thinking_order import (
    repair_assistant_content_order,
)


# ── happy path ─────────────────────────────────────────────────────────


def test_empty_tuple_unchanged() -> None:
    result = repair_assistant_content_order(())
    assert result.blocks == ()
    assert result.changed is False
    assert result.dropped == 0


def test_no_thinking_unchanged() -> None:
    blocks = (TextBlock(text="hello"), TextBlock(text="world"))
    result = repair_assistant_content_order(blocks)
    assert result.blocks is blocks  # identity preserved
    assert result.changed is False


def test_well_ordered_unchanged() -> None:
    blocks = (
        ThinkingBlock(content="plan"),
        ThinkingBlock(content="refine"),
        TextBlock(text="done"),
        ToolUseBlock(id="t1", name="bash", input={}),
    )
    result = repair_assistant_content_order(blocks)
    assert result.blocks is blocks
    assert result.changed is False


def test_single_thinking_at_front_unchanged() -> None:
    blocks = (ThinkingBlock(content="only"),)
    result = repair_assistant_content_order(blocks)
    assert result.blocks is blocks
    assert result.changed is False


# ── reorder mode ───────────────────────────────────────────────────────


def test_reorder_single_late_thinking() -> None:
    blocks = (
        TextBlock(text="hello"),
        ThinkingBlock(content="late"),
    )
    result = repair_assistant_content_order(blocks, mode="reorder")
    assert result.changed is True
    assert result.mode == "reorder"
    assert result.dropped == 0
    assert len(result.blocks) == 2
    assert isinstance(result.blocks[0], ThinkingBlock)
    assert isinstance(result.blocks[1], TextBlock)
    assert result.blocks[0].content == "late"
    assert result.blocks[1].text == "hello"


def test_reorder_preserves_intra_partition_order() -> None:
    blocks = (
        ThinkingBlock(content="first"),
        TextBlock(text="a"),
        ThinkingBlock(content="second"),
        ToolUseBlock(id="t1", name="bash", input={}),
        ThinkingBlock(content="third"),
    )
    result = repair_assistant_content_order(blocks, mode="reorder")
    assert result.changed is True
    # Thinking blocks in original order.
    thinking_texts = [
        b.content for b in result.blocks if isinstance(b, ThinkingBlock)
    ]
    assert thinking_texts == ["first", "second", "third"]
    # Non-thinking blocks in original order too.
    non_thinking = [
        b for b in result.blocks if not isinstance(b, ThinkingBlock)
    ]
    assert isinstance(non_thinking[0], TextBlock)
    assert non_thinking[0].text == "a"
    assert isinstance(non_thinking[1], ToolUseBlock)


def test_reorder_preserves_signature_bytes() -> None:
    """Anthropic extended thinking requires verbatim signature round-trip;
    reorder must never modify a ThinkingBlock's fields."""
    blocks = (
        TextBlock(text="premature"),
        ThinkingBlock(content="reasoning", signature="sig-abc-123"),
    )
    result = repair_assistant_content_order(blocks, mode="reorder")
    assert result.changed is True
    tb = result.blocks[0]
    assert isinstance(tb, ThinkingBlock)
    assert tb.content == "reasoning"
    assert tb.signature == "sig-abc-123"


# ── strip mode ─────────────────────────────────────────────────────────


def test_strip_drops_late_thinking() -> None:
    blocks = (
        ThinkingBlock(content="early"),
        TextBlock(text="hello"),
        ThinkingBlock(content="late"),
    )
    result = repair_assistant_content_order(blocks, mode="strip")
    assert result.changed is True
    assert result.mode == "strip"
    assert result.dropped == 1
    assert len(result.blocks) == 2
    assert isinstance(result.blocks[0], ThinkingBlock)
    assert result.blocks[0].content == "early"
    assert isinstance(result.blocks[1], TextBlock)


def test_strip_keeps_all_if_already_ordered() -> None:
    blocks = (
        ThinkingBlock(content="a"),
        TextBlock(text="ok"),
    )
    result = repair_assistant_content_order(blocks, mode="strip")
    assert result.changed is False
    assert result.dropped == 0
    assert result.blocks is blocks


def test_strip_multiple_late() -> None:
    blocks = (
        TextBlock(text="head"),
        ThinkingBlock(content="late1"),
        ToolUseBlock(id="t", name="n", input={}),
        ThinkingBlock(content="late2"),
        TextBlock(text="tail"),
    )
    result = repair_assistant_content_order(blocks, mode="strip")
    assert result.changed is True
    assert result.dropped == 2
    # Only non-thinking blocks remain (and nothing else).
    assert all(not isinstance(b, ThinkingBlock) for b in result.blocks)
    assert len(result.blocks) == 3


# ── error paths ────────────────────────────────────────────────────────


def test_unknown_mode_raises() -> None:
    blocks = (TextBlock(text="x"), ThinkingBlock(content="late"))
    with pytest.raises(ValueError, match="unknown repair mode"):
        repair_assistant_content_order(blocks, mode="explode")  # type: ignore[arg-type]


def test_logger_warns_on_repair(caplog: pytest.LogCaptureFixture) -> None:
    blocks = (TextBlock(text="a"), ThinkingBlock(content="late"))
    with caplog.at_level("WARNING"):
        repair_assistant_content_order(blocks, mode="reorder")
    assert any("reordered" in rec.message for rec in caplog.records)

"""Wave2-1a P1: ThinkingBlock data model + order validator.

Pins the invariant that all ThinkingBlocks precede the first
non-thinking block in an assistant Message.content tuple, plus the
basic dataclass shape (frozen, signature defaults to empty, Union
membership). Nothing in this phase actually constructs a ThinkingBlock
at runtime — P2 is where the provider parser starts producing them —
so any regression in the existing 1660-test sweep would have to come
from the Union widening alone.
"""
from __future__ import annotations

import dataclasses
from typing import get_args

import pytest

from llm_code.api.content_order import (
    ThinkingOrderError,
    validate_assistant_content_order,
)
from llm_code.api.types import (
    ContentBlock,
    ImageBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)


# ---------- Dataclass shape ----------

def test_thinking_block_is_frozen_dataclass() -> None:
    block = ThinkingBlock(content="let me think about this", signature="")
    assert block.content == "let me think about this"
    assert block.signature == ""
    with pytest.raises(dataclasses.FrozenInstanceError):
        block.content = "mutated"  # type: ignore[misc]


def test_thinking_block_signature_defaults_to_empty() -> None:
    """Unsigned providers (Qwen, DeepSeek, OpenAI o-series) omit
    signature. The default must be empty string, not None, so the
    type stays a plain str for round-trip code."""
    block = ThinkingBlock(content="reasoning")
    assert block.signature == ""
    assert isinstance(block.signature, str)


def test_thinking_block_preserves_signature_bytes_opaquely() -> None:
    """Anthropic signs thinking blocks with base64-encoded bytes that
    contain unicode and padding characters. The signature must not be
    normalized, decoded, or trimmed — any byte change breaks the
    server-side verification and causes a 400 on the next request."""
    tricky = "abc123+/=\n  \u00e9\u00a0trailing"
    block = ThinkingBlock(content="x", signature=tricky)
    assert block.signature == tricky
    assert len(block.signature) == len(tricky)


# ---------- Union membership ----------

def test_content_block_union_includes_thinking_first() -> None:
    """ThinkingBlock should be a member of the ContentBlock Union so
    every isinstance(block, ContentBlock) check continues to work."""
    members = get_args(ContentBlock)
    assert ThinkingBlock in members
    # P1 intentionally puts thinking at the front of the Union for
    # readability — this isn't load-bearing for typing, but pinning it
    # documents the intended ordering convention.
    assert members[0] is ThinkingBlock


def test_existing_content_blocks_still_in_union() -> None:
    """Widening must be additive: no existing block type should be
    removed or reordered out of the Union."""
    members = get_args(ContentBlock)
    for existing in (TextBlock, ToolUseBlock, ToolResultBlock, ImageBlock):
        assert existing in members


# ---------- Order validator: happy paths ----------

def test_empty_content_tuple_passes() -> None:
    validate_assistant_content_order(())


def test_single_text_block_passes() -> None:
    validate_assistant_content_order((TextBlock(text="hello"),))


def test_single_thinking_block_passes() -> None:
    validate_assistant_content_order((ThinkingBlock(content="reasoning"),))


def test_thinking_before_text_passes() -> None:
    validate_assistant_content_order((
        ThinkingBlock(content="reasoning"),
        TextBlock(text="hello"),
    ))


def test_thinking_before_tool_use_passes() -> None:
    validate_assistant_content_order((
        ThinkingBlock(content="I need to search"),
        ToolUseBlock(id="t1", name="search", input={"q": "x"}),
    ))


def test_multiple_consecutive_thinking_blocks_pass() -> None:
    """Providers may split a long reasoning trace into multiple blocks
    — the invariant only requires that all thinking precedes the first
    non-thinking block, not that there be exactly one."""
    validate_assistant_content_order((
        ThinkingBlock(content="first pass"),
        ThinkingBlock(content="second pass"),
        ThinkingBlock(content="third pass"),
        TextBlock(text="final answer"),
    ))


def test_no_thinking_blocks_at_all_passes() -> None:
    """The entire existing codebase produces content tuples with zero
    thinking blocks — that must stay valid so P1 can land without
    wiring any downstream consumer."""
    validate_assistant_content_order((
        TextBlock(text="response"),
        ToolUseBlock(id="t1", name="read", input={"path": "a"}),
    ))


# ---------- Order validator: violations ----------

def test_text_before_thinking_raises() -> None:
    with pytest.raises(ThinkingOrderError) as excinfo:
        validate_assistant_content_order((
            TextBlock(text="hello"),
            ThinkingBlock(content="reasoning"),
        ))
    assert excinfo.value.index == 1
    assert excinfo.value.offending_type == "ThinkingBlock"
    assert excinfo.value.preceding_type == "TextBlock"


def test_tool_use_before_thinking_raises() -> None:
    with pytest.raises(ThinkingOrderError) as excinfo:
        validate_assistant_content_order((
            ToolUseBlock(id="t1", name="read", input={}),
            ThinkingBlock(content="reasoning"),
        ))
    assert excinfo.value.preceding_type == "ToolUseBlock"


def test_interleaved_thinking_raises() -> None:
    """Once any non-thinking block has appeared, no further thinking
    is allowed — even if earlier thinking blocks were in the right
    place. This prevents the sneaky pattern where a provider emits
    (Thinking, Text, Thinking, ToolUse)."""
    with pytest.raises(ThinkingOrderError) as excinfo:
        validate_assistant_content_order((
            ThinkingBlock(content="a"),
            TextBlock(text="partial"),
            ThinkingBlock(content="b"),
            ToolUseBlock(id="t1", name="read", input={}),
        ))
    assert excinfo.value.index == 2


def test_error_message_includes_position_and_types() -> None:
    """A future P3 assembly bug must be debuggable from the traceback
    alone — the message must point at the offending index and the
    neighboring block types."""
    with pytest.raises(ThinkingOrderError) as excinfo:
        validate_assistant_content_order((
            TextBlock(text="x"),
            ThinkingBlock(content="y"),
        ))
    msg = str(excinfo.value)
    assert "index 1" in msg
    assert "ThinkingBlock" in msg
    assert "TextBlock" in msg

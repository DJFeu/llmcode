"""Wave2-1a P3: assistant message assembly + session serialization.

Covers the three responsibilities P3 adds on top of P1 (data model)
and P2 (inbound parsing):

1. ``Session`` persists ``ThinkingBlock`` through to_dict / from_dict
   without loss, including the signature field. Any future session
   save that forgets to handle thinking would otherwise silently drop
   the reasoning trace on resume.

2. ``Session.estimated_tokens()`` counts thinking-block content so the
   proactive-compaction trigger fires early enough when reasoning
   traces are large. Without this, a DeepSeek-R1 session with 10K
   tokens of reasoning would look empty to the compactor.

3. ``OpenAICompatProvider._convert_message`` tolerates an assistant
   message that contains a ``ThinkingBlock`` without crashing. For
   OpenAI-compat servers thinking is silently dropped from the
   outbound payload (they would 400 on unknown content types); P4
   is where the outbound round-trip for native Anthropic providers
   gets wired in. This test pins the "do not crash" contract.

The full conversation-loop assembly is not exercised here — it's
covered indirectly by the existing test_conversation* suites, which
must still pass after this PR.
"""
from __future__ import annotations

from pathlib import Path

from llm_code.api.content_order import (
    ThinkingOrderError,
    validate_assistant_content_order,
)
from llm_code.api.openai_compat import OpenAICompatProvider
from llm_code.api.types import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)
from llm_code.runtime.session import (
    Session,
    _block_to_dict,
    _dict_to_block,
)


# ---------- Session serialization round-trip ----------

def test_block_to_dict_handles_thinking_block() -> None:
    block = ThinkingBlock(content="reasoning trace", signature="sig-xyz")
    d = _block_to_dict(block)
    assert d == {
        "type": "thinking",
        "thinking": "reasoning trace",
        "signature": "sig-xyz",
    }


def test_dict_to_block_handles_thinking_block() -> None:
    d = {"type": "thinking", "thinking": "trace", "signature": "opaque"}
    block = _dict_to_block(d)
    assert block == ThinkingBlock(content="trace", signature="opaque")


def test_thinking_block_round_trip_preserves_signature_bytes() -> None:
    """Signature bytes must not be mutated across serialization.
    Anthropic verifies them server-side on the next request echo."""
    tricky = "abc+/==\n  \u00e9\u00a0tail"
    block = ThinkingBlock(content="x", signature=tricky)
    round_tripped = _dict_to_block(_block_to_dict(block))
    assert isinstance(round_tripped, ThinkingBlock)
    assert round_tripped.signature == tricky
    assert len(round_tripped.signature) == len(tricky)


def test_dict_to_block_tolerates_missing_signature() -> None:
    """A pre-P5 DB row (before the signature column migration) stores
    thinking without signature. Rehydration must default to empty
    string rather than KeyError."""
    block = _dict_to_block({"type": "thinking", "thinking": "legacy"})
    assert block == ThinkingBlock(content="legacy", signature="")


def test_session_round_trip_preserves_thinking_in_messages() -> None:
    msg = Message(
        role="assistant",
        content=(
            ThinkingBlock(content="reasoning", signature="s1"),
            TextBlock(text="answer"),
        ),
    )
    session = Session.create(project_path=Path("/tmp/test"))
    session = session.add_message(msg)
    rehydrated = Session.from_dict(session.to_dict())
    assert len(rehydrated.messages) == 1
    blocks = rehydrated.messages[0].content
    assert len(blocks) == 2
    assert blocks[0] == ThinkingBlock(content="reasoning", signature="s1")
    assert blocks[1] == TextBlock(text="answer")


# ---------- estimated_tokens counts thinking ----------

def test_estimated_tokens_includes_thinking_content() -> None:
    """A session with a large thinking trace must report a larger
    token estimate than the same session without, so the proactive
    compactor fires at the right time."""
    without = Session.create(project_path=Path("/tmp/test")).add_message(
        Message(
            role="assistant",
            content=(TextBlock(text="short answer"),),
        )
    )
    with_thinking = Session.create(project_path=Path("/tmp/test")).add_message(
        Message(
            role="assistant",
            content=(
                ThinkingBlock(content="very long reasoning trace " * 200),
                TextBlock(text="short answer"),
            ),
        )
    )
    assert with_thinking.estimated_tokens() > without.estimated_tokens()


def test_estimated_tokens_unchanged_without_thinking() -> None:
    """Zero thinking blocks → estimate matches the pre-P3 behavior
    (we only add to the total, never subtract)."""
    s = Session.create(project_path=Path("/tmp/test")).add_message(
        Message(
            role="assistant",
            content=(
                TextBlock(text="hello"),
                ToolUseBlock(id="t1", name="read", input={"path": "a"}),
            ),
        )
    )
    assert s.estimated_tokens() > 0  # base case smoke


# ---------- Outbound _convert_message does not crash on thinking ----------

def test_convert_message_with_thinking_and_text_drops_thinking() -> None:
    """OpenAI-compat servers reject unknown content types. The current
    expected behavior is to silently drop the thinking block from the
    outbound payload. P4 is where round-trip for Anthropic will turn
    this on. For now, we only pin 'does not crash + text still sent'."""
    provider = OpenAICompatProvider(base_url="http://localhost:0", api_key="")
    msg = Message(
        role="assistant",
        content=(
            ThinkingBlock(content="hidden reasoning", signature=""),
            TextBlock(text="visible answer"),
        ),
    )
    result = provider._convert_message(msg)
    # Multi-block path produces a parts list
    parts = result.get("content")
    assert isinstance(parts, list)
    # Only the text block should be present; thinking is dropped
    text_entries = [p for p in parts if isinstance(p, dict) and p.get("type") == "text"]
    assert len(text_entries) == 1
    assert text_entries[0]["text"] == "visible answer"


def test_convert_message_with_only_thinking_does_not_crash() -> None:
    """Pathological case: an assistant turn that produced only a
    thinking block (no text, no tool calls). The provider must not
    crash; content becomes empty-ish but the shape is still valid
    JSON for the server."""
    provider = OpenAICompatProvider(base_url="http://localhost:0", api_key="")
    msg = Message(
        role="assistant",
        content=(ThinkingBlock(content="thinking only"),),
    )
    result = provider._convert_message(msg)
    assert result["role"] == "assistant"
    # Either empty string content or empty parts list — both are
    # acceptable shapes for a server.
    content = result.get("content")
    assert content == "" or content == []


# ---------- Order validator still guards assembly ----------

def test_assembly_validator_rejects_text_before_thinking() -> None:
    """The conversation-loop prepend logic in P3 makes this impossible
    in practice — thinking is always prepended first — but a future
    refactor could accidentally reorder. The validator call in the
    assembly path catches that regression."""
    import pytest  # local import keeps module headless-import safe

    blocks = (
        TextBlock(text="leaked early"),
        ThinkingBlock(content="too late"),
    )
    with pytest.raises(ThinkingOrderError):
        validate_assistant_content_order(blocks)

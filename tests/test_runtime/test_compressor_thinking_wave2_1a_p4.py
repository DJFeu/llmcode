"""Wave2-1a P4: compressor atomicity for (Thinking, ToolUse) pairs.

When ``_micro_compact`` drops a stale ``ToolUseBlock`` (and its paired
``ToolResultBlock``), any ``ThinkingBlock`` that immediately precedes
the dropped tool_use must be dropped together. Signed thinking
(Anthropic extended thinking) must travel with its adjacent tool_use
or the next request round-trip fails signature verification; unsigned
thinking is harmless to drop, but the pairing must be consistent so
the P1 order invariant continues to hold on the compressed session.

These tests exercise the pure Session → compressor.compress() path
with hand-built messages, no provider / no network.
"""
from __future__ import annotations

from pathlib import Path

from llm_code.api.content_order import validate_assistant_content_order
from llm_code.api.types import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from llm_code.runtime.compressor import ContextCompressor
from llm_code.runtime.session import Session


def _make_session(messages: tuple[Message, ...]) -> Session:
    s = Session.create(project_path=Path("/tmp/test"))
    for m in messages:
        s = s.add_message(m)
    return s


def _flat_blocks(session: Session) -> list:
    return [b for m in session.messages for b in m.content]


# ---------- Atomic pair: stale tool_use + preceding thinking ----------

def test_micro_compact_drops_thinking_with_stale_tool_use() -> None:
    """Two read_file calls for the same path; the earlier one is
    stale. The preceding ThinkingBlock for the stale call must be
    dropped alongside the ToolUseBlock itself."""
    session = _make_session((
        Message(
            role="assistant",
            content=(
                ThinkingBlock(content="I should read foo.py first", signature="sig-stale"),
                ToolUseBlock(id="t1", name="read_file", input={"path": "foo.py"}),
            ),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t1", content="stale content"),),
        ),
        Message(
            role="assistant",
            content=(
                ThinkingBlock(content="Let me re-read foo.py", signature="sig-fresh"),
                ToolUseBlock(id="t2", name="read_file", input={"path": "foo.py"}),
            ),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t2", content="fresh content"),),
        ),
    ))
    compressor = ContextCompressor()
    # Call _micro_compact directly to isolate the atomicity fix from
    # other compression levels
    result = compressor._micro_compact(session)
    blocks = _flat_blocks(result)
    # Stale tool_use AND its preceding thinking must both be gone
    assert not any(
        isinstance(b, ToolUseBlock) and b.id == "t1" for b in blocks
    )
    assert not any(
        isinstance(b, ThinkingBlock) and b.signature == "sig-stale" for b in blocks
    )
    # Fresh pair survives intact
    fresh_tool_use = [b for b in blocks if isinstance(b, ToolUseBlock) and b.id == "t2"]
    fresh_thinking = [b for b in blocks if isinstance(b, ThinkingBlock) and b.signature == "sig-fresh"]
    assert len(fresh_tool_use) == 1
    assert len(fresh_thinking) == 1


def test_micro_compact_drops_multiple_preceding_thinking_blocks() -> None:
    """Anthropic can split a long reasoning trace across multiple
    consecutive thinking blocks before a single tool_use. The while
    loop in the fix must pop all of them."""
    session = _make_session((
        Message(
            role="assistant",
            content=(
                ThinkingBlock(content="first reasoning chunk", signature="a"),
                ThinkingBlock(content="second reasoning chunk", signature="b"),
                ThinkingBlock(content="third reasoning chunk", signature="c"),
                ToolUseBlock(id="t1", name="read_file", input={"path": "foo.py"}),
            ),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t1", content="content"),),
        ),
        Message(
            role="assistant",
            content=(
                ToolUseBlock(id="t2", name="read_file", input={"path": "foo.py"}),
            ),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t2", content="fresh"),),
        ),
    ))
    compressor = ContextCompressor()
    result = compressor._micro_compact(session)
    blocks = _flat_blocks(result)
    # All three thinking blocks from the stale group must be gone
    for sig in ("a", "b", "c"):
        assert not any(
            isinstance(b, ThinkingBlock) and b.signature == sig for b in blocks
        )


def test_micro_compact_leaves_thinking_before_kept_tool_use() -> None:
    """When the tool_use is NOT stale, its preceding thinking must
    survive unmodified."""
    session = _make_session((
        Message(
            role="assistant",
            content=(
                ThinkingBlock(content="reasoning", signature="keep-me"),
                ToolUseBlock(id="t1", name="read_file", input={"path": "a.py"}),
            ),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t1", content="x"),),
        ),
        # Different file: t1 is not stale
        Message(
            role="assistant",
            content=(
                ToolUseBlock(id="t2", name="read_file", input={"path": "b.py"}),
            ),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t2", content="y"),),
        ),
    ))
    compressor = ContextCompressor()
    result = compressor._micro_compact(session)
    blocks = _flat_blocks(result)
    assert any(
        isinstance(b, ThinkingBlock) and b.signature == "keep-me" for b in blocks
    )


def test_micro_compact_drops_thinking_only_leftover_messages() -> None:
    """An assistant message that becomes pure-thinking after the
    atomicity fix (its sole tool_use was pruned and the thinking
    had no other sibling blocks) has nothing useful to contribute
    on the next turn and is dropped wholesale — this keeps the P1
    ordering invariant trivially valid on the compressed session."""
    session = _make_session((
        Message(
            role="assistant",
            content=(
                ThinkingBlock(content="stale reasoning", signature="s1"),
                ToolUseBlock(id="t1", name="read_file", input={"path": "a.py"}),
            ),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t1", content="x"),),
        ),
        Message(
            role="assistant",
            content=(
                ToolUseBlock(id="t2", name="read_file", input={"path": "a.py"}),
            ),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t2", content="fresh"),),
        ),
    ))
    compressor = ContextCompressor()
    result = compressor._micro_compact(session)
    # Stale assistant message (sole tool_use + its thinking) is dropped
    # wholesale; its result message is also dropped because the result
    # block was keyed to the stale tool_use_id. Fresh (assistant, user)
    # pair remains. Before P4 this would have been 3 messages with an
    # orphaned thinking-only leftover.
    assert len(result.messages) == 2
    assert any(
        isinstance(b, ToolUseBlock) and b.id == "t2" for b in _flat_blocks(result)
    )
    # No orphaned thinking blocks left behind
    assert not any(isinstance(b, ThinkingBlock) for b in _flat_blocks(result))


def test_micro_compact_preserves_non_thinking_siblings_when_pruning() -> None:
    """If the dropped tool_use had a preceding TextBlock (not
    thinking), the TextBlock must stay. Only the immediately-preceding
    thinking run gets popped — not arbitrary text content."""
    session = _make_session((
        Message(
            role="assistant",
            content=(
                TextBlock(text="narrative commentary"),
                ThinkingBlock(content="reasoning", signature="s1"),
                ToolUseBlock(id="t1", name="read_file", input={"path": "a.py"}),
            ),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t1", content="x"),),
        ),
        Message(
            role="assistant",
            content=(
                ToolUseBlock(id="t2", name="read_file", input={"path": "a.py"}),
            ),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t2", content="fresh"),),
        ),
    ))
    compressor = ContextCompressor()
    result = compressor._micro_compact(session)
    blocks = _flat_blocks(result)
    # TextBlock survives; thinking + tool_use both gone
    assert any(
        isinstance(b, TextBlock) and b.text == "narrative commentary" for b in blocks
    )
    assert not any(
        isinstance(b, ThinkingBlock) and b.signature == "s1" for b in blocks
    )
    assert not any(
        isinstance(b, ToolUseBlock) and b.id == "t1" for b in blocks
    )


# ---------- P1 ordering invariant holds post-compression ----------

def test_compressed_session_still_satisfies_order_invariant() -> None:
    """After _micro_compact, every assistant message must still pass
    validate_assistant_content_order. The atomicity fix is what makes
    this trivially true — any test failure here would indicate the
    compressor left thinking blocks in a broken position."""
    session = _make_session((
        Message(
            role="assistant",
            content=(
                ThinkingBlock(content="first think", signature="a"),
                ToolUseBlock(id="t1", name="read_file", input={"path": "a.py"}),
            ),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t1", content="x"),),
        ),
        Message(
            role="assistant",
            content=(
                ThinkingBlock(content="second think", signature="b"),
                TextBlock(text="mid-turn narrative"),
                ToolUseBlock(id="t2", name="read_file", input={"path": "a.py"}),
            ),
        ),
        Message(
            role="user",
            content=(ToolResultBlock(tool_use_id="t2", content="fresh"),),
        ),
    ))
    compressor = ContextCompressor()
    result = compressor._micro_compact(session)
    for msg in result.messages:
        if msg.role == "assistant":
            validate_assistant_content_order(msg.content)

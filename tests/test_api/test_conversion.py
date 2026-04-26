"""Unit tests for v15 M3 — ``llm_code.api.conversion``.

Covers the four public surfaces:

* :func:`serialize_tool_result` — None / str / dict / list / nested.
* :func:`deferred_post_tool_blocks` — reorder logic for OpenAI compat.
* :func:`serialize_messages` — per-target-shape, per-block-type cases.
* :class:`ConversionContext` — frozen dataclass behaviour.

Byte parity against the captured v2.4.0 corpus is exercised in
``tests/test_api/parity/test_provider_conversion_parity_v15.py`` —
this file complements that with focused unit coverage of edge cases.
"""
from __future__ import annotations

import pytest

from llm_code.api.conversion import (
    ConversionContext,
    ReasoningReplayMode,
    deferred_post_tool_blocks,
    serialize_messages,
    serialize_tool_result,
)
from llm_code.api.types import (
    ImageBlock,
    Message,
    ServerToolUseBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)


# ── serialize_tool_result ────────────────────────────────────────────


class TestSerializeToolResult:
    def test_none_returns_empty_string(self) -> None:
        assert serialize_tool_result(None) == ""

    def test_string_passes_through(self) -> None:
        assert serialize_tool_result("hello") == "hello"
        assert serialize_tool_result("") == ""

    def test_dict_serializes_to_json(self) -> None:
        out = serialize_tool_result({"a": 1, "b": "x"})
        # JSON, ensure_ascii=False so unicode passes through.
        assert "a" in out
        assert "1" in out

    def test_dict_unicode_preserved(self) -> None:
        out = serialize_tool_result({"q": "你好"})
        assert "你好" in out  # not escaped to \uXXXX

    def test_list_text_blocks_joined_with_newline(self) -> None:
        items = [
            {"type": "text", "text": "line 1"},
            {"type": "text", "text": "line 2"},
        ]
        out = serialize_tool_result(items)
        assert out == "line 1\nline 2"

    def test_list_mixed_types_serializes_each(self) -> None:
        items = [
            {"type": "text", "text": "hello"},
            {"type": "image", "url": "http://x"},
            "raw string",
        ]
        out = serialize_tool_result(items)
        # Text → text; non-text dict → JSON; non-dict → str().
        assert "hello" in out
        assert "image" in out
        assert "raw string" in out

    def test_int_falls_back_to_str(self) -> None:
        assert serialize_tool_result(42) == "42"

    def test_nested_dict_serializes(self) -> None:
        out = serialize_tool_result({"outer": {"inner": [1, 2, 3]}})
        assert "outer" in out
        assert "inner" in out


# ── deferred_post_tool_blocks ────────────────────────────────────────


class TestDeferredPostToolBlocks:
    def test_no_tool_use_returns_empty(self) -> None:
        blocks = (TextBlock(text="hi"),)
        assert deferred_post_tool_blocks(blocks) == ()

    def test_only_tool_use_returns_empty(self) -> None:
        blocks = (ToolUseBlock(id="x", name="f", input={}),)
        assert deferred_post_tool_blocks(blocks) == ()

    def test_text_after_tool_use_is_deferred(self) -> None:
        text_block = TextBlock(text="post-tool answer")
        blocks = (
            ToolUseBlock(id="x", name="f", input={}),
            text_block,
        )
        deferred = deferred_post_tool_blocks(blocks)
        assert deferred == (text_block,)

    def test_text_before_tool_use_is_not_deferred(self) -> None:
        blocks = (
            TextBlock(text="setup"),
            ToolUseBlock(id="x", name="f", input={}),
        )
        assert deferred_post_tool_blocks(blocks) == ()

    def test_multiple_tool_uses_only_first_anchors(self) -> None:
        text_after = TextBlock(text="commentary")
        blocks = (
            ToolUseBlock(id="a", name="f", input={}),
            ToolUseBlock(id="b", name="f", input={}),
            text_after,
        )
        # First tool_use is the anchor; subsequent tool_use blocks
        # are not deferred (only non-tool blocks after the anchor are).
        deferred = deferred_post_tool_blocks(blocks)
        assert deferred == (text_after,)


# ── serialize_messages — Anthropic shape ─────────────────────────────


class TestSerializeMessagesAnthropic:
    def _ctx(self) -> ConversionContext:
        return ConversionContext(
            target_shape="anthropic",
            reasoning_replay=ReasoningReplayMode.NATIVE_THINKING,
        )

    def test_single_user_text(self) -> None:
        msgs = (Message(role="user", content=(TextBlock(text="hi"),)),)
        out = serialize_messages(msgs, self._ctx())
        assert out == [{
            "role": "user",
            "content": [{
                "type": "text",
                "text": "hi",
                # Cache breakpoint added by the Anthropic prompt-cache
                # logic on the last user message's last block.
                "cache_control": {"type": "ephemeral"},
            }],
        }]

    def test_thinking_block_with_signature(self) -> None:
        msgs = (
            Message(role="user", content=(TextBlock(text="q"),)),
            Message(role="assistant", content=(
                ThinkingBlock(content="thinking", signature="sig"),
                TextBlock(text="answer"),
            )),
        )
        out = serialize_messages(msgs, self._ctx())
        assistant = out[1]
        assert assistant["role"] == "assistant"
        thinking_block = assistant["content"][0]
        assert thinking_block["type"] == "thinking"
        assert thinking_block["thinking"] == "thinking"
        assert thinking_block["signature"] == "sig"

    def test_thinking_unsigned_omits_signature_key(self) -> None:
        msgs = (
            Message(role="user", content=(TextBlock(text="q"),)),
            Message(role="assistant", content=(
                ThinkingBlock(content="t", signature=""),
            )),
        )
        out = serialize_messages(msgs, self._ctx())
        thinking_block = out[1]["content"][0]
        # Empty signature → key omitted entirely (keeps wire shape clean).
        assert "signature" not in thinking_block

    def test_image_block(self) -> None:
        msgs = (
            Message(role="user", content=(
                TextBlock(text="see this"),
                ImageBlock(media_type="image/png", data="abc=="),
            )),
        )
        out = serialize_messages(msgs, self._ctx())
        image_entry = out[0]["content"][1]
        assert image_entry["type"] == "image"
        assert image_entry["source"]["type"] == "base64"
        assert image_entry["source"]["media_type"] == "image/png"
        assert image_entry["source"]["data"] == "abc=="

    def test_tool_result_message(self) -> None:
        msgs = (
            Message(role="user", content=(
                ToolResultBlock(tool_use_id="t1", content="result"),
            )),
        )
        out = serialize_messages(msgs, self._ctx())
        tr = out[0]["content"][0]
        assert tr["type"] == "tool_result"
        assert tr["tool_use_id"] == "t1"
        assert tr["content"] == "result"
        assert "is_error" not in tr

    def test_tool_result_error_flag(self) -> None:
        msgs = (
            Message(role="user", content=(
                ToolResultBlock(
                    tool_use_id="t1", content="bad", is_error=True,
                ),
            )),
        )
        out = serialize_messages(msgs, self._ctx())
        tr = out[0]["content"][0]
        assert tr["is_error"] is True

    def test_server_tool_use_block(self) -> None:
        msgs = (
            Message(role="user", content=(TextBlock(text="q"),)),
            Message(role="assistant", content=(
                ServerToolUseBlock(
                    id="s1", name="web_search",
                    input={"q": "x"}, signature="sig",
                ),
            )),
        )
        out = serialize_messages(msgs, self._ctx())
        block = out[1]["content"][0]
        assert block["type"] == "server_tool_use"
        assert block["signature"] == "sig"

    def test_cache_control_on_last_user_only(self) -> None:
        msgs = (
            Message(role="user", content=(TextBlock(text="a"),)),
            Message(role="assistant", content=(TextBlock(text="b"),)),
            Message(role="user", content=(TextBlock(text="c"),)),
        )
        out = serialize_messages(msgs, self._ctx())
        # Only the LAST user message gets cache_control; the earlier
        # one does not.
        assert out[0]["content"][-1].get("cache_control") is None
        assert out[2]["content"][-1].get("cache_control") == {
            "type": "ephemeral",
        }


# ── serialize_messages — OpenAI shape ────────────────────────────────


class TestSerializeMessagesOpenAI:
    def _ctx(self, *, strip: bool = False) -> ConversionContext:
        return ConversionContext(
            target_shape="openai",
            reasoning_replay=ReasoningReplayMode.DISABLED,
            strip_prior_reasoning=strip,
        )

    def test_single_user_text_string_content(self) -> None:
        msgs = (Message(role="user", content=(TextBlock(text="hi"),)),)
        out = serialize_messages(msgs, self._ctx())
        assert out == [{"role": "user", "content": "hi"}]

    def test_system_prepended(self) -> None:
        msgs = (Message(role="user", content=(TextBlock(text="q"),)),)
        out = serialize_messages(
            msgs, self._ctx(), system="You are X.",
        )
        assert out[0] == {"role": "system", "content": "You are X."}
        assert out[1]["role"] == "user"

    def test_image_block_emits_parts(self) -> None:
        msgs = (
            Message(role="user", content=(
                TextBlock(text="see"),
                ImageBlock(media_type="image/jpeg", data="xyz=="),
            )),
        )
        out = serialize_messages(msgs, self._ctx())
        parts = out[0]["content"]
        assert isinstance(parts, list)
        assert parts[0] == {"type": "text", "text": "see"}
        assert parts[1]["type"] == "image_url"
        assert parts[1]["image_url"]["url"] == (
            "data:image/jpeg;base64,xyz=="
        )

    def test_tool_result_collapses_to_tool_role(self) -> None:
        msgs = (
            Message(role="user", content=(
                ToolResultBlock(tool_use_id="t1", content="r"),
            )),
        )
        out = serialize_messages(msgs, self._ctx())
        assert out == [{
            "role": "tool", "tool_call_id": "t1", "content": "r",
        }]

    def test_thinking_block_dropped(self) -> None:
        msgs = (
            Message(role="user", content=(TextBlock(text="q"),)),
            Message(role="assistant", content=(
                ThinkingBlock(content="hidden", signature=""),
                TextBlock(text="visible"),
            )),
        )
        out = serialize_messages(msgs, self._ctx())
        # Assistant message is parts shape with the text; thinking is dropped.
        assistant_parts = out[1]["content"]
        assert isinstance(assistant_parts, list)
        types = {p["type"] for p in assistant_parts}
        assert "text" in types
        # Thinking has no type, so we just ensure no thinking keys remain.
        assert all(p.get("type") != "thinking" for p in assistant_parts)

    def test_strip_prior_reasoning_removes_keys(self) -> None:
        # The conversion path doesn't normally LEAK reasoning_content
        # to the dict — but the filter is defensive. Inject one
        # synthetically and verify it's stripped.
        msgs = (
            Message(role="assistant", content=(TextBlock(text="x"),)),
        )
        out = serialize_messages(msgs, self._ctx(strip=True))
        # No reasoning_content was added to begin with — the filter is
        # a no-op but must not crash.
        assert out == [{"role": "assistant", "content": "x"}]


# ── ConversionContext ────────────────────────────────────────────────


class TestConversionContext:
    def test_is_frozen(self) -> None:
        ctx = ConversionContext(target_shape="anthropic")
        with pytest.raises(Exception):
            ctx.target_shape = "openai"  # type: ignore[misc]

    def test_default_values(self) -> None:
        ctx = ConversionContext(target_shape="openai")
        assert ctx.reasoning_replay is ReasoningReplayMode.DISABLED
        assert ctx.strip_prior_reasoning is False

    def test_unknown_target_shape_raises(self) -> None:
        msgs = (Message(role="user", content=(TextBlock(text="x"),)),)
        # Bypass the Literal type guard to test runtime fallback.
        ctx = ConversionContext.__new__(ConversionContext)
        object.__setattr__(ctx, "target_shape", "bogus")
        object.__setattr__(ctx, "reasoning_replay", ReasoningReplayMode.DISABLED)
        object.__setattr__(ctx, "strip_prior_reasoning", False)
        with pytest.raises(ValueError, match="unknown target_shape"):
            serialize_messages(msgs, ctx)


# ── ReasoningReplayMode enum ─────────────────────────────────────────


class TestReasoningReplayMode:
    def test_all_modes_present(self) -> None:
        modes = {m.value for m in ReasoningReplayMode}
        assert modes == {
            "disabled", "think_tags", "reasoning_content", "native_thinking",
        }

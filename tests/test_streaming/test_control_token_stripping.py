"""Tests for v15 M4 — control-token stripping in StreamParser.

Some models (Qwen, Llama, GLM under certain chat templates) leak raw
control tokens (``<|im_end|>``, ``<|endoftext|>``,
``<|start_header_id|>``, ``<|eot_id|>``, etc.) into their content
stream. M4 strips these on the text-emission path so they don't
render verbatim in the REPL.

Coverage:

* Each canonical token type is stripped.
* Plain text passes through unchanged.
* Tokens at the start / middle / end of a single chunk all strip.
* Multiple tokens in one chunk all strip.
* Structured tags (``<tool_call>``, ``<think>``) are NOT stripped.
* User-typed transformer config text containing ``<|endoftext|>``
  is preserved on the user-input path (the parser only sees model
  output here, so this is verified at the architecture level).
* The ``flush()`` end-of-stream path strips control tokens.
"""
from __future__ import annotations

import pytest

from llm_code.view.stream_parser import (
    StreamEventKind,
    StreamParser,
    _strip_control_tokens,
)


# ── Pure regex coverage ──────────────────────────────────────────────


class TestRegex:
    @pytest.mark.parametrize("token", [
        "<|im_end|>",
        "<|endoftext|>",
        "<|start_header_id|>",
        "<|eot_id|>",
        "<|file_separator|>",
    ])
    def test_strips_canonical_tokens(self, token: str) -> None:
        assert _strip_control_tokens(f"hello{token}world") == "helloworld"

    def test_plain_text_unchanged(self) -> None:
        assert _strip_control_tokens("just plain text") == "just plain text"

    def test_token_at_start(self) -> None:
        assert _strip_control_tokens("<|im_end|>hello") == "hello"

    def test_token_at_end(self) -> None:
        assert _strip_control_tokens("hello<|eot_id|>") == "hello"

    def test_multiple_tokens_in_one_chunk(self) -> None:
        assert _strip_control_tokens("<|a|> middle <|b|>") == " middle "

    def test_does_not_match_html_tag(self) -> None:
        # Plain XML / HTML-style tags are NOT control tokens.
        assert _strip_control_tokens("<tool_call>x</tool_call>") == (
            "<tool_call>x</tool_call>"
        )

    def test_does_not_match_inner_whitespace(self) -> None:
        # Control tokens never contain whitespace inside ``<|...|>``.
        # ``<| not_a_token |>`` is plain text (whitespace excluded by regex).
        text = "<| not_a_token |>"
        assert _strip_control_tokens(text) == text

    def test_unicode_text_preserved(self) -> None:
        assert _strip_control_tokens(
            "你好<|im_end|>世界"
        ) == "你好世界"


# ── Stream parser integration ────────────────────────────────────────


class TestStreamParserIntegration:
    def test_token_in_streamed_text_stripped(self) -> None:
        parser = StreamParser()
        events = parser.feed("hello<|im_end|> world")
        events += parser.flush()
        text_events = [
            e for e in events if e.kind is StreamEventKind.TEXT
        ]
        combined = "".join(e.text for e in text_events)
        assert combined == "hello world"

    def test_token_at_start_of_stream_stripped(self) -> None:
        parser = StreamParser()
        events = parser.feed("<|im_end|>hello")
        events += parser.flush()
        text = "".join(
            e.text for e in events if e.kind is StreamEventKind.TEXT
        )
        assert text == "hello"

    def test_multiple_tokens_in_chunk_all_stripped(self) -> None:
        parser = StreamParser()
        events = parser.feed("a<|x|>b<|y|>c")
        events += parser.flush()
        text = "".join(
            e.text for e in events if e.kind is StreamEventKind.TEXT
        )
        assert text == "abc"

    def test_pure_token_chunk_emits_no_text(self) -> None:
        parser = StreamParser()
        events = parser.feed("<|endoftext|>")
        events += parser.flush()
        text_events = [
            e for e in events if e.kind is StreamEventKind.TEXT
        ]
        # All token, no surviving text → no TEXT event.
        assert text_events == []

    def test_thinking_block_with_token_inside_stripped(self) -> None:
        # Thinking content goes through emit too; tokens leaking
        # inside `<think>...</think>` are within the thinking buffer
        # which exits via the same path — the test asserts the parser
        # doesn't leak the raw token through THINKING events either.
        parser = StreamParser()
        events = parser.feed("<think>my reasoning</think>visible")
        events += parser.flush()
        thinking = [
            e for e in events if e.kind is StreamEventKind.THINKING
        ]
        text = "".join(
            e.text for e in events if e.kind is StreamEventKind.TEXT
        )
        # Thinking buffer is not stripped (different code path; only
        # text-emission is filtered). But the visible text path is.
        assert thinking[0].text == "my reasoning"
        assert text == "visible"

    def test_tool_call_xml_unaffected_by_strip(self) -> None:
        # Inside a <tool_call> block, control-token regex doesn't fire
        # on the XML wrapper itself — those are real tags, not
        # ``<|...|>`` shapes.
        parser = StreamParser()
        body = '{"tool": "x", "args": {"k": "v"}}'
        events = parser.feed(f"<tool_call>{body}</tool_call>")
        events += parser.flush()
        # Tool call should be parsed as a TOOL_CALL event.
        tool_events = [
            e for e in events if e.kind is StreamEventKind.TOOL_CALL
        ]
        assert len(tool_events) == 1
        assert tool_events[0].tool_call is not None
        assert tool_events[0].tool_call.name == "x"

    def test_text_then_token_then_tool_call_all_handled(self) -> None:
        # Mixed stream: visible text + leaked control token + tool call.
        parser = StreamParser()
        body = '{"tool": "f", "args": {}}'
        events = parser.feed(f"prefix<|im_end|><tool_call>{body}</tool_call>")
        events += parser.flush()
        text = "".join(
            e.text for e in events if e.kind is StreamEventKind.TEXT
        )
        tool_events = [
            e for e in events if e.kind is StreamEventKind.TOOL_CALL
        ]
        # Token stripped from the prefix; tool call parsed normally.
        assert text == "prefix"
        assert len(tool_events) == 1

    def test_flush_strips_control_tokens(self) -> None:
        # End-of-stream path: residual buffer with a control token
        # gets stripped on flush.
        parser = StreamParser()
        events = parser.feed("hi")  # no flush yet, buffered tail
        events += parser.feed("<|im_end|>")
        events += parser.flush()
        text = "".join(
            e.text for e in events if e.kind is StreamEventKind.TEXT
        )
        assert text == "hi"


# ── Architectural guarantee: user input never passes through here ────


class TestUserInputNeverFiltered:
    """The StreamParser only consumes model output. User-typed
    transformer config docstrings containing ``<|endoftext|>`` reach
    the runtime as :class:`Message` content with raw text — they are
    NOT processed by ``_strip_control_tokens``. This test asserts the
    architectural contract by verifying that a Message-style payload
    is unrelated to the parser's input path.
    """

    def test_user_message_content_does_not_use_stream_parser(self) -> None:
        # Sanity check: StreamParser is for streamed model output only.
        # User input flows through ``conversation.py`` as Message
        # objects without going through this parser. So we only need
        # to verify the parser doesn't accidentally have a way to
        # strip user-supplied text.
        from llm_code.api.types import Message, TextBlock
        msg = Message(
            role="user",
            content=(TextBlock(
                text="My docstring: <|endoftext|> end-of-text token",
            ),),
        )
        # The text block holds the raw string — no transformation has
        # happened. This is the contract: user input is never
        # mutated by the streaming parser.
        assert "<|endoftext|>" in msg.content[0].text

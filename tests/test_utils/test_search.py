"""Tests for search_messages utility."""
from __future__ import annotations

import pytest

from llm_code.api.types import Message, TextBlock, ToolUseBlock, ToolResultBlock
from llm_code.utils.search import SearchResult, search_messages


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(role: str, *texts: str) -> Message:
    """Build a Message with one TextBlock per text argument."""
    return Message(role=role, content=tuple(TextBlock(text=t) for t in texts))


def _msg_mixed(role: str) -> Message:
    """Build a Message with TextBlock, ToolUseBlock, and ToolResultBlock."""
    return Message(
        role=role,
        content=(
            TextBlock(text="before tool call"),
            ToolUseBlock(id="tu_1", name="bash", input={"command": "ls"}),
            ToolResultBlock(tool_use_id="tu_1", content="file.txt"),
            TextBlock(text="after tool call"),
        ),
    )


# ---------------------------------------------------------------------------
# SearchResult dataclass
# ---------------------------------------------------------------------------

class TestSearchResult:
    def test_is_frozen(self):
        r = SearchResult(
            message_index=0,
            line_number=1,
            line_text="hello world",
            match_start=6,
            match_end=11,
        )
        with pytest.raises((AttributeError, TypeError)):
            r.message_index = 99  # type: ignore[misc]

    def test_fields(self):
        r = SearchResult(
            message_index=2,
            line_number=5,
            line_text="foo bar",
            match_start=0,
            match_end=3,
        )
        assert r.message_index == 2
        assert r.line_number == 5
        assert r.line_text == "foo bar"
        assert r.match_start == 0
        assert r.match_end == 3


# ---------------------------------------------------------------------------
# search_messages — basic matching
# ---------------------------------------------------------------------------

class TestSearchMessagesBasic:
    def test_empty_messages_returns_empty(self):
        assert search_messages([], "hello") == []

    def test_no_match_returns_empty(self):
        msgs = [_msg("user", "hello world")]
        assert search_messages(msgs, "xyz") == []

    def test_single_match(self):
        msgs = [_msg("user", "hello world")]
        results = search_messages(msgs, "world")
        assert len(results) == 1
        r = results[0]
        assert r.message_index == 0
        assert r.line_number == 1
        assert "world" in r.line_text
        assert r.line_text[r.match_start:r.match_end] == "world"

    def test_multiple_messages(self):
        msgs = [
            _msg("user", "first message"),
            _msg("assistant", "second message"),
            _msg("user", "third item"),
        ]
        results = search_messages(msgs, "message")
        assert len(results) == 2
        assert results[0].message_index == 0
        assert results[1].message_index == 1

    def test_match_start_and_end_positions(self):
        msgs = [_msg("user", "abc def abc")]
        results = search_messages(msgs, "abc")
        # Two occurrences on line 1
        assert len(results) == 2
        for r in results:
            assert r.line_text[r.match_start:r.match_end] == "abc"

    def test_multiline_content(self):
        msgs = [_msg("assistant", "line one\nline two\nline three")]
        results = search_messages(msgs, "two")
        assert len(results) == 1
        assert results[0].line_number == 2
        assert results[0].line_text == "line two"

    def test_line_numbers_are_one_based(self):
        msgs = [_msg("user", "alpha\nbeta\ngamma")]
        results = search_messages(msgs, "gamma")
        assert results[0].line_number == 3

    def test_only_searches_text_blocks(self):
        msgs = [_msg_mixed("assistant")]
        # "bash" appears in ToolUseBlock.name but NOT in any TextBlock
        results = search_messages(msgs, "bash")
        assert len(results) == 0

    def test_tool_result_content_not_searched(self):
        msgs = [_msg_mixed("assistant")]
        results = search_messages(msgs, "file.txt")
        assert len(results) == 0

    def test_text_block_content_in_mixed_message(self):
        msgs = [_msg_mixed("assistant")]
        results = search_messages(msgs, "before tool")
        assert len(results) == 1
        assert results[0].line_text == "before tool call"


# ---------------------------------------------------------------------------
# search_messages — case sensitivity
# ---------------------------------------------------------------------------

class TestSearchMessagesCaseSensitivity:
    def test_case_insensitive_by_default(self):
        msgs = [_msg("user", "Hello World")]
        results = search_messages(msgs, "hello")
        assert len(results) == 1

    def test_case_insensitive_explicit(self):
        msgs = [_msg("user", "Hello World")]
        results = search_messages(msgs, "WORLD", case_sensitive=False)
        assert len(results) == 1

    def test_case_sensitive_match(self):
        msgs = [_msg("user", "Hello World")]
        results = search_messages(msgs, "World", case_sensitive=True)
        assert len(results) == 1

    def test_case_sensitive_no_match(self):
        msgs = [_msg("user", "Hello World")]
        results = search_messages(msgs, "hello", case_sensitive=True)
        assert len(results) == 0

    def test_case_sensitive_match_positions(self):
        msgs = [_msg("user", "Hello World")]
        results = search_messages(msgs, "World", case_sensitive=True)
        r = results[0]
        assert r.line_text[r.match_start:r.match_end] == "World"


# ---------------------------------------------------------------------------
# search_messages — edge cases
# ---------------------------------------------------------------------------

class TestSearchMessagesEdgeCases:
    def test_empty_query_returns_empty(self):
        msgs = [_msg("user", "hello")]
        assert search_messages(msgs, "") == []

    def test_message_with_empty_text_block(self):
        msgs = [Message(role="user", content=(TextBlock(text=""),))]
        results = search_messages(msgs, "hello")
        assert results == []

    def test_message_with_no_content(self):
        msgs = [Message(role="user", content=())]
        results = search_messages(msgs, "hello")
        assert results == []

    def test_multiple_matches_same_line(self):
        msgs = [_msg("user", "aa aa aa")]
        results = search_messages(msgs, "aa")
        assert len(results) == 3

    def test_message_index_correct_for_many_messages(self):
        msgs = [_msg("user", f"message {i}") for i in range(5)]
        results = search_messages(msgs, "message 3")
        assert len(results) == 1
        assert results[0].message_index == 3

    def test_returns_list_of_search_result(self):
        msgs = [_msg("user", "hello")]
        results = search_messages(msgs, "hello")
        assert isinstance(results, list)
        assert all(isinstance(r, SearchResult) for r in results)

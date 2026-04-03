"""Tests for llm_code.utils.text_normalize."""
from __future__ import annotations


from llm_code.utils.text_normalize import (
    normalize_for_match,
    normalize_quotes,
    strip_trailing_whitespace,
)


class TestNormalizeQuotes:
    def test_left_single_quotation_mark(self):
        assert normalize_quotes("\u2018hello\u2018") == "'hello'"

    def test_right_single_quotation_mark(self):
        assert normalize_quotes("\u2019hello\u2019") == "'hello'"

    def test_left_double_quotation_mark(self):
        assert normalize_quotes("\u201chello\u201c") == '"hello"'

    def test_right_double_quotation_mark(self):
        assert normalize_quotes("\u201dhello\u201d") == '"hello"'

    def test_modifier_letter_apostrophe(self):
        assert normalize_quotes("it\u02bcs") == "it's"

    def test_all_five_quote_types_together(self):
        text = "\u2018\u2019\u201c\u201d\u02bc"
        assert normalize_quotes(text) == "''\"\"'"

    def test_plain_text_unchanged(self):
        text = "hello 'world' \"test\""
        assert normalize_quotes(text) == text

    def test_empty_string(self):
        assert normalize_quotes("") == ""

    def test_mixed_curly_and_straight(self):
        result = normalize_quotes("it\u2019s a \u201ctest\u201d")
        assert result == "it's a \"test\""


class TestStripTrailingWhitespace:
    def test_removes_trailing_spaces(self):
        assert strip_trailing_whitespace("hello   ") == "hello"

    def test_removes_trailing_tabs(self):
        assert strip_trailing_whitespace("hello\t\t") == "hello"

    def test_removes_mixed_trailing_whitespace(self):
        assert strip_trailing_whitespace("hello \t ") == "hello"

    def test_multiline_strips_each_line(self):
        text = "line one   \nline two\t\nline three"
        expected = "line one\nline two\nline three"
        assert strip_trailing_whitespace(text) == expected

    def test_leading_whitespace_preserved(self):
        assert strip_trailing_whitespace("  hello  ") == "  hello"

    def test_empty_string(self):
        assert strip_trailing_whitespace("") == ""

    def test_blank_lines_preserved(self):
        text = "a\n\nb"
        assert strip_trailing_whitespace(text) == "a\n\nb"

    def test_line_with_only_spaces_becomes_empty(self):
        assert strip_trailing_whitespace("   ") == ""


class TestNormalizeForMatch:
    def test_applies_both_normalizations(self):
        text = "it\u2019s nice   "
        result = normalize_for_match(text)
        assert result == "it's nice"

    def test_curly_quotes_normalized(self):
        result = normalize_for_match("\u201chello\u201d")
        assert result == '"hello"'

    def test_trailing_whitespace_stripped(self):
        result = normalize_for_match("foo   \nbar\t")
        assert result == "foo\nbar"

    def test_combined_multiline(self):
        text = "def foo(\u2019arg\u2019):   \n    pass\t"
        result = normalize_for_match(text)
        assert result == "def foo('arg'):\n    pass"

    def test_idempotent_on_plain_text(self):
        text = "plain text\nno changes needed"
        assert normalize_for_match(text) == text

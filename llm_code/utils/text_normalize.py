"""Text normalization utilities for fuzzy matching."""
from __future__ import annotations


_QUOTE_TABLE = str.maketrans(
    {
        "\u2018": "'",  # LEFT SINGLE QUOTATION MARK
        "\u2019": "'",  # RIGHT SINGLE QUOTATION MARK
        "\u201c": '"',  # LEFT DOUBLE QUOTATION MARK
        "\u201d": '"',  # RIGHT DOUBLE QUOTATION MARK
        "\u02bc": "'",  # MODIFIER LETTER APOSTROPHE
    }
)


def normalize_quotes(text: str) -> str:
    """Convert curly/smart quotes and modifier apostrophes to straight quotes."""
    return text.translate(_QUOTE_TABLE)


def strip_trailing_whitespace(text: str) -> str:
    """Remove trailing spaces and tabs from each line."""
    return "\n".join(line.rstrip(" \t") for line in text.split("\n"))


def normalize_for_match(text: str) -> str:
    """Apply quote normalization and trailing-whitespace stripping."""
    return strip_trailing_whitespace(normalize_quotes(text))

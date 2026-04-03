"""OSC8 terminal hyperlink utilities."""
from __future__ import annotations

import os
import re

# Regex to detect http/https URLs; excludes trailing punctuation like . ) > " <
_URL_RE = re.compile(r"https?://[^\s)<>\"]+")

# Terminals (TERM_PROGRAM values) known to support OSC8 hyperlinks
_SUPPORTED_TERM_PROGRAMS: frozenset[str] = frozenset({"iTerm.app", "WezTerm"})


def make_hyperlink(url: str, text: str | None = None) -> str:
    """Return an OSC8 hyperlink escape sequence.

    Args:
        url:  The target URL.
        text: Display text; defaults to *url* when ``None`` or empty.

    Returns:
        A string containing the OSC8 escape sequences so terminals that
        support them render a clickable hyperlink.
    """
    display = text if text else url
    return f"\033]8;;{url}\033\\{display}\033]8;;\033\\"


def auto_link(text: str) -> str:
    """Detect URLs in *text* and wrap each one in an OSC8 hyperlink.

    Only ``http://`` and ``https://`` URLs are matched.  Trailing
    punctuation characters that are unlikely to be part of the URL
    (i.e. ``.``, ``,``, ``)``, ``]``) are excluded from the link target
    so that sentences such as "See https://example.com." render
    correctly.

    Args:
        text: Plain text that may contain URLs.

    Returns:
        Text with any detected URLs replaced by OSC8 hyperlink sequences.
    """
    _TRAILING_PUNCT = frozenset(".),]")

    def _replace(match: re.Match) -> str:  # type: ignore[type-arg]
        url = match.group(0)
        # Strip trailing punctuation that is very likely not part of the URL.
        while url and url[-1] in _TRAILING_PUNCT:
            url = url[:-1]
        suffix = match.group(0)[len(url):]
        return make_hyperlink(url) + suffix

    return _URL_RE.sub(_replace, text)


def supports_hyperlinks() -> bool:
    """Return ``True`` when the current terminal is known to support OSC8.

    Checks the following environment variables (in order):

    * ``TERM_PROGRAM`` — ``iTerm.app`` or ``WezTerm``
    * ``WT_SESSION``   — set by Windows Terminal
    * ``VTE_VERSION``  — set by VTE-based terminals (GNOME Terminal, etc.)
    """
    term_program = os.environ.get("TERM_PROGRAM", "")
    if term_program in _SUPPORTED_TERM_PROGRAMS:
        return True
    if os.environ.get("WT_SESSION"):
        return True
    if os.environ.get("VTE_VERSION"):
        return True
    return False

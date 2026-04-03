"""Tests for OSC8 terminal hyperlink utilities."""
from __future__ import annotations

import os
from unittest.mock import patch


from llm_code.utils.hyperlink import auto_link, make_hyperlink, supports_hyperlinks


# ---------------------------------------------------------------------------
# TestMakeHyperlink
# ---------------------------------------------------------------------------


class TestMakeHyperlink:
    def test_basic_url_produces_osc8_sequence(self) -> None:
        url = "https://example.com"
        result = make_hyperlink(url)
        assert result == f"\033]8;;{url}\033\\{url}\033]8;;\033\\"

    def test_custom_text_used_as_display(self) -> None:
        url = "https://example.com"
        text = "click here"
        result = make_hyperlink(url, text)
        assert result == f"\033]8;;{url}\033\\{text}\033]8;;\033\\"

    def test_none_text_falls_back_to_url(self) -> None:
        url = "https://example.com/path"
        result = make_hyperlink(url, None)
        assert result == f"\033]8;;{url}\033\\{url}\033]8;;\033\\"

    def test_empty_text_uses_url(self) -> None:
        url = "https://example.com"
        result = make_hyperlink(url, "")
        # Empty string should fall back to url
        assert result == f"\033]8;;{url}\033\\{url}\033]8;;\033\\"

    def test_escape_sequences_present(self) -> None:
        result = make_hyperlink("https://x.com", "X")
        assert "\033]8;;" in result
        assert "\033\\" in result


# ---------------------------------------------------------------------------
# TestAutoLink
# ---------------------------------------------------------------------------


class TestAutoLink:
    def test_plain_url_gets_wrapped(self) -> None:
        text = "Visit https://example.com for info"
        result = auto_link(text)
        assert "\033]8;;" in result
        assert "https://example.com" in result

    def test_no_url_unchanged(self) -> None:
        text = "No links here at all."
        result = auto_link(text)
        assert result == text

    def test_multiple_urls_all_wrapped(self) -> None:
        text = "See https://one.com and https://two.com"
        result = auto_link(text)
        # Each hyperlink produces 2 OSC8 sequences (open + close), so 2 URLs = 4
        assert result.count("\033]8;;") == 4

    def test_http_url_wrapped(self) -> None:
        text = "http://insecure.example.org/page"
        result = auto_link(text)
        assert "\033]8;;" in result

    def test_surrounding_text_preserved(self) -> None:
        text = "Start https://example.com End"
        result = auto_link(text)
        assert result.startswith("Start ")
        assert result.endswith(" End")

    def test_url_with_path_query(self) -> None:
        url = "https://api.example.com/v1/data?key=val&foo=bar"
        result = auto_link(url)
        assert url in result
        assert "\033]8;;" in result

    def test_trailing_punctuation_excluded(self) -> None:
        # URL followed by a period should not include the period in the URL
        text = "See https://example.com."
        result = auto_link(text)
        # The period should appear outside the hyperlink
        assert result.endswith(".")

    def test_url_in_parentheses_excluded(self) -> None:
        # URL inside parentheses should not include the closing paren
        text = "(https://example.com)"
        result = auto_link(text)
        # Closing paren and opening paren should be outside the hyperlink
        assert result.endswith(")")


# ---------------------------------------------------------------------------
# TestSupportsHyperlinks
# ---------------------------------------------------------------------------


class TestSupportsHyperlinks:
    def _clean_env(self) -> dict[str, str | None]:
        """Return dict of env vars to patch to empty/None for isolation."""
        return {
            "TERM_PROGRAM": None,
            "WT_SESSION": None,
            "VTE_VERSION": None,
        }

    def test_iterm_returns_true(self) -> None:
        env = {**self._clean_env(), "TERM_PROGRAM": "iTerm.app"}
        with patch.dict(os.environ, {k: v for k, v in env.items() if v is not None}, clear=False):
            # Also remove keys we want absent
            for k, v in env.items():
                if v is None and k in os.environ:
                    del os.environ[k]
            assert supports_hyperlinks() is True

    def test_wezterm_returns_true(self) -> None:
        env = {**self._clean_env(), "TERM_PROGRAM": "WezTerm"}
        with patch.dict(os.environ, {k: v for k, v in env.items() if v is not None}, clear=False):
            for k, v in env.items():
                if v is None and k in os.environ:
                    del os.environ[k]
            assert supports_hyperlinks() is True

    def test_wt_session_returns_true(self) -> None:
        env = {**self._clean_env(), "WT_SESSION": "some-uuid"}
        with patch.dict(os.environ, {k: v for k, v in env.items() if v is not None}, clear=False):
            for k, v in env.items():
                if v is None and k in os.environ:
                    del os.environ[k]
            assert supports_hyperlinks() is True

    def test_vte_version_returns_true(self) -> None:
        env = {**self._clean_env(), "VTE_VERSION": "6500"}
        with patch.dict(os.environ, {k: v for k, v in env.items() if v is not None}, clear=False):
            for k, v in env.items():
                if v is None and k in os.environ:
                    del os.environ[k]
            assert supports_hyperlinks() is True

    def test_no_supported_env_returns_false(self) -> None:
        env = self._clean_env()
        with patch.dict(os.environ, {}, clear=False):
            for k in env:
                if k in os.environ:
                    del os.environ[k]
            assert supports_hyperlinks() is False

    def test_unknown_term_program_returns_false(self) -> None:
        env = {**self._clean_env(), "TERM_PROGRAM": "xterm-256color"}
        with patch.dict(os.environ, {k: v for k, v in env.items() if v is not None}, clear=False):
            for k, v in env.items():
                if v is None and k in os.environ:
                    del os.environ[k]
            assert supports_hyperlinks() is False

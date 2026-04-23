"""M6 Task 6.3 — PII + secret redaction tests.

Covers:

* Every entry in the synthetic ``leak_corpus.txt`` is scrubbed by the
  default :class:`Redactor`.
* ``_placeholder`` preserves length + first/last 3 chars for
  debuggability while never leaking the original value.
* :class:`RedactingFilter` scrubs ``LogRecord.msg`` and ``LogRecord.args``.
* JSON payloads remain parseable after redaction (i.e. we don't break
  structured logs).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest

_CORPUS_PATH = (
    Path(__file__).parent / "fixtures" / "leak_corpus.txt"
)


def _load_corpus() -> list[str]:
    """Load the synthetic leak corpus; skip blanks and comments."""
    lines: list[str] = []
    for raw in _CORPUS_PATH.read_text().splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


CORPUS = _load_corpus()


class TestCorpusFixture:
    def test_corpus_exists(self) -> None:
        assert _CORPUS_PATH.exists()

    def test_corpus_has_at_least_50_entries(self) -> None:
        assert len(CORPUS) >= 50

    def test_corpus_entries_all_obviously_synthetic(self) -> None:
        """Catch accidental real-looking creds sneaking into the corpus.
        Every entry must contain one of the synthetic markers."""
        markers = ("FAKE", "DUMMY", "EXAMPLE", "AAAA", "ZZZZ", "0000")
        for entry in CORPUS:
            assert any(m in entry for m in markers), (
                f"corpus entry {entry!r} lacks a synthetic marker; "
                f"add FAKE / DUMMY / EXAMPLE to avoid scanner trips"
            )


class TestRedactorBasics:
    def test_redactor_constructs_with_defaults(self) -> None:
        from llm_code.engine.observability.redaction import Redactor

        r = Redactor()
        assert r is not None

    def test_default_patterns_is_non_empty(self) -> None:
        from llm_code.engine.observability.redaction import DEFAULT_PATTERNS

        assert isinstance(DEFAULT_PATTERNS, (list, tuple))
        assert len(DEFAULT_PATTERNS) > 0

    def test_scrub_leaves_plain_text_alone(self) -> None:
        from llm_code.engine.observability.redaction import Redactor

        r = Redactor()
        assert r.scrub("hello world") == "hello world"

    def test_scrub_empty_string(self) -> None:
        from llm_code.engine.observability.redaction import Redactor

        r = Redactor()
        assert r.scrub("") == ""

    def test_scrub_accepts_custom_patterns(self) -> None:
        from llm_code.engine.observability.redaction import Redactor

        custom = [re.compile(r"SECRET\d+")]
        r = Redactor(patterns=custom)
        out = r.scrub("value=SECRET12345 end")
        assert "SECRET12345" not in out


class TestPlaceholderShape:
    def test_short_value_becomes_redacted_literal(self) -> None:
        from llm_code.engine.observability.redaction import Redactor

        out = Redactor._placeholder("short")
        assert out == "[REDACTED]"

    def test_long_value_keeps_len_and_first_last_3(self) -> None:
        from llm_code.engine.observability.redaction import Redactor

        value = "sk-FAKEFAKEFAKEFAKEFAKEFAKEFAKE0000"
        out = Redactor._placeholder(value)
        assert out.startswith("[REDACTED:")
        assert out.endswith(f"len={len(value)}]")
        assert "sk-" in out           # first 3 preserved
        assert "000" in out           # last 3 preserved
        assert value not in out       # full secret never survives

    def test_placeholder_shape_regex(self) -> None:
        from llm_code.engine.observability.redaction import Redactor

        value = "0" * 40
        out = Redactor._placeholder(value)
        # [REDACTED:XXX…YYY:len=N]
        assert re.fullmatch(r"\[REDACTED:.{3}….{3}:len=\d+\]", out), out


@pytest.mark.parametrize("secret", CORPUS)
def test_corpus_entry_is_scrubbed(secret: str) -> None:
    """Parametrised: every entry in the corpus must no longer appear
    verbatim after ``Redactor.scrub``."""
    from llm_code.engine.observability.redaction import Redactor

    r = Redactor()
    context = f"prefix {secret} suffix"
    scrubbed = r.scrub(context)
    assert secret not in scrubbed, (
        f"secret survived scrub: original={secret!r}, result={scrubbed!r}"
    )


class TestScrubMapping:
    def test_scrub_mapping_redacts_string_values(self) -> None:
        from llm_code.engine.observability.redaction import Redactor

        r = Redactor()
        inp = {"auth": "Bearer FAKEFAKEFAKEFAKEFAKEFAKEFAKE", "other": "plain"}
        out = r.scrub_mapping(inp)
        assert "Bearer FAKEFAKEFAKEFAKEFAKEFAKEFAKE" not in out["auth"]
        assert out["other"] == "plain"

    def test_scrub_mapping_leaves_non_strings_alone(self) -> None:
        from llm_code.engine.observability.redaction import Redactor

        r = Redactor()
        inp = {"count": 5, "ratio": 0.5, "flag": True, "items": [1, 2, 3]}
        out = r.scrub_mapping(inp)
        assert out == inp

    def test_scrub_mapping_returns_new_dict(self) -> None:
        from llm_code.engine.observability.redaction import Redactor

        r = Redactor()
        inp = {"k": "v"}
        out = r.scrub_mapping(inp)
        assert out is not inp


class TestStructuredTextPreservation:
    def test_scrub_does_not_break_json_shape(self) -> None:
        """A JSON payload without any secret-like content must remain
        byte-equal. A payload with a secret must still parse (we only
        replace the secret substring)."""
        from llm_code.engine.observability.redaction import Redactor

        r = Redactor()
        payload = json.dumps({"k": "plain", "n": 3})
        assert r.scrub(payload) == payload

    def test_scrub_preserves_surrounding_json(self) -> None:
        from llm_code.engine.observability.redaction import Redactor

        r = Redactor()
        secret = "sk-FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE"
        payload = json.dumps({"token": secret, "n": 3})
        scrubbed = r.scrub(payload)
        # structural chars preserved
        assert scrubbed.startswith("{") and scrubbed.endswith("}")
        assert '"token"' in scrubbed
        assert '"n": 3' in scrubbed
        assert secret not in scrubbed


class TestRedactingFilter:
    def test_filter_scrubs_record_msg(self) -> None:
        from llm_code.engine.observability.redaction import (
            Redactor,
            RedactingFilter,
        )

        f = RedactingFilter(Redactor())
        rec = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="using Bearer FAKEFAKEFAKEFAKEFAKEFAKEFAKE here",
            args=None,
            exc_info=None,
        )
        f.filter(rec)
        assert "Bearer FAKEFAKEFAKEFAKEFAKEFAKEFAKE" not in rec.msg

    def test_filter_scrubs_record_args_tuple(self) -> None:
        from llm_code.engine.observability.redaction import (
            Redactor,
            RedactingFilter,
        )

        f = RedactingFilter(Redactor())
        rec = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="token=%s",
            args=("ghp_FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE",),
            exc_info=None,
        )
        f.filter(rec)
        assert "ghp_FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE" not in rec.args[0]

    def test_filter_returns_true_so_record_passes(self) -> None:
        from llm_code.engine.observability.redaction import (
            Redactor,
            RedactingFilter,
        )

        f = RedactingFilter(Redactor())
        rec = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="plain",
            args=None,
            exc_info=None,
        )
        assert f.filter(rec) is True

    def test_filter_leaves_non_string_args_alone(self) -> None:
        from llm_code.engine.observability.redaction import (
            Redactor,
            RedactingFilter,
        )

        f = RedactingFilter(Redactor())
        rec = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="count=%d",
            args=(42,),
            exc_info=None,
        )
        f.filter(rec)
        assert rec.args == (42,)


class TestLengthPreservation:
    def test_long_base64_dump_gets_collapsed(self) -> None:
        from llm_code.engine.observability.redaction import Redactor

        secret = "A" * 150
        r = Redactor()
        out = r.scrub(f"data={secret} end")
        assert secret not in out


class TestEmailRedaction:
    """Emails are scrubbed by default — placeholder preserves first/last
    chars when long enough, otherwise falls back to ``[REDACTED]``."""

    def test_email_like_string_is_scrubbed(self) -> None:
        from llm_code.engine.observability.redaction import Redactor

        r = Redactor()
        email = "fakedev-user-fake@fake-example.com"
        out = r.scrub(f"contact: {email} please")
        assert email not in out

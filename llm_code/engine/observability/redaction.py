"""PII + secret scrubber for log records and span attributes.

The ``Redactor`` class owns a list of compiled regexes (``DEFAULT_PATTERNS``
covers OpenAI / Anthropic / GitHub / JWT / Bearer / AWS / GCP / Slack
tokens and long base64 dumps). :meth:`Redactor.scrub` rewrites matching
substrings to a placeholder that preserves length + first-3 and last-3
chars for debuggability, but never leaks the raw secret.

``RedactingFilter`` is a :class:`logging.Filter` that runs the redactor
on ``record.msg`` and ``record.args``, so the normal ``logging``
pipeline can't accidentally leak secrets.

This module has no third-party imports — it uses only :mod:`re` and
:mod:`logging` — so it loads whether or not OpenTelemetry /
``prometheus_client`` are installed.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Pattern


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
# Ordering matters: more-specific patterns come first so the placeholder
# replacement happens at the specific-match granularity (a Bearer token
# shouldn't additionally match the generic long-base64 pattern).
DEFAULT_PATTERNS: list[Pattern[str]] = [
    # Anthropic — must be before generic sk-* so we match the specific prefix.
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    # OpenAI-style keys (sk-...).
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    # GitHub classic PAT.
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    # GitHub fine-grained PAT.
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    # JWTs — three dot-separated base64url segments.
    re.compile(r"eyJ[A-Za-z0-9_\-]{5,}\.eyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}"),
    # Bearer headers (case-insensitive prefix, runs of token chars).
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-]{15,}"),
    # AWS access key IDs.
    re.compile(r"AKIA[A-Z0-9]{10,}"),
    # GCP API keys.
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),
    # Slack tokens.
    re.compile(r"xox[abpr]-[A-Za-z0-9\-]{10,}"),
    # Email addresses (redact by default; tests can override patterns).
    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    # Long base64-ish blobs (>= 120 chars of base64/url-safe chars) —
    # catches dumped credentials, private keys, etc. Kept last so the
    # specific patterns above win when they overlap.
    re.compile(r"[A-Za-z0-9+/=_\-]{120,}"),
]


class Redactor:
    """Run a set of regex patterns over text, replacing matches with a
    shape-preserving placeholder."""

    __slots__ = ("_patterns",)

    def __init__(
        self,
        patterns: Iterable[Pattern[str]] | None = None,
    ) -> None:
        self._patterns: tuple[Pattern[str], ...] = tuple(
            patterns if patterns is not None else DEFAULT_PATTERNS
        )

    # ----- public api ------------------------------------------------------
    def scrub(self, text: str) -> str:
        """Return ``text`` with every pattern match replaced by the
        :meth:`_placeholder` output."""
        if not text:
            return text
        out = text
        for pattern in self._patterns:
            out = pattern.sub(lambda m: self._placeholder(m.group(0)), out)
        return out

    def scrub_mapping(self, d: dict) -> dict:
        """Return a new dict where each string value has been scrubbed.
        Non-string values pass through unchanged."""
        return {
            k: (self.scrub(v) if isinstance(v, str) else v)
            for k, v in d.items()
        }

    # ----- helpers ---------------------------------------------------------
    @staticmethod
    def _placeholder(value: str) -> str:
        """Return a redaction marker for ``value``.

        * Short values (<16 chars) collapse to the bare ``[REDACTED]``.
        * Longer values keep the first 3 + last 3 chars plus the length
          so humans debugging a trace can recognise roughly *which*
          secret it was without being able to reconstruct it.
        """
        if len(value) < 16:
            return "[REDACTED]"
        return f"[REDACTED:{value[:3]}\u2026{value[-3:]}:len={len(value)}]"


# ---------------------------------------------------------------------------
# logging.Filter adapter
# ---------------------------------------------------------------------------
class RedactingFilter(logging.Filter):
    """Scrubs ``LogRecord.msg`` and any string entries in ``record.args``
    before the record reaches a handler/formatter.

    Attach this filter to the root logger (or any logger/handler) to
    protect the whole logging pipeline. Non-string ``args`` values (ints,
    booleans, dicts, etc.) are passed through untouched — we only rewrite
    strings because only strings can carry raw credentials from our call
    sites.
    """

    def __init__(self, redactor: Redactor | None = None) -> None:
        super().__init__()
        self._r = redactor if redactor is not None else Redactor()

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.msg = self._r.scrub(str(record.msg))
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    self._r.scrub(a) if isinstance(a, str) else a
                    for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: (self._r.scrub(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
        return True

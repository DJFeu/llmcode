"""Scan text for leaked secrets and redact them."""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_pat", re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}")),
    ("generic_api_key", re.compile(
        r"(?:api[_-]?key|api[_-]?secret|access[_-]?token|auth[_-]?token)"
        r"\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{32,})['\"]?",
        re.IGNORECASE,
    )),
    ("jwt", re.compile(
        r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"
    )),
    ("private_key", re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE KEY-----")),
    ("slack_token", re.compile(r"xox[bpas]-[A-Za-z0-9\-]{10,}")),
)


def scan_output(text: str) -> tuple[str, list[str]]:
    """Scan text for secrets, redact them.

    Returns (cleaned_text, findings). Findings is empty if no secrets found.
    Original text returned unchanged if clean.
    """
    if not text:
        return text, []

    findings: list[str] = []
    cleaned = text

    for name, pattern in _SECRET_PATTERNS:
        matches = list(pattern.finditer(cleaned))
        for match in reversed(matches):
            secret = match.group(0)
            preview = f"{secret[:4]}...{secret[-2:]}" if len(secret) > 8 else "***"
            findings.append(f"Redacted {name}: {preview}")
            cleaned = (
                cleaned[: match.start()]
                + f"[REDACTED:{name}]"
                + cleaned[match.end() :]
            )

    return cleaned, findings

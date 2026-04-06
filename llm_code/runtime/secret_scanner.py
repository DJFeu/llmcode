"""Scan text for leaked secrets and redact them."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_BUILTIN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
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
    ("stripe_key", re.compile(r"[sr]k_(live|test)_[A-Za-z0-9]{20,}")),
    ("twilio_key", re.compile(r"SK[a-f0-9]{32}")),
    ("sendgrid_key", re.compile(r"SG\.[A-Za-z0-9_-]{22,}\.[A-Za-z0-9_-]{22,}")),
    ("gcp_service_account", re.compile(
        r'"type"\s*:\s*"service_account"',
    )),
    ("npm_token", re.compile(r"npm_[A-Za-z0-9]{36}")),
    ("pypi_token", re.compile(r"pypi-[A-Za-z0-9_-]{50,}")),
)

# Cached combined patterns (builtin + user-defined)
_cached_patterns: tuple[tuple[str, re.Pattern[str]], ...] | None = None


def load_custom_patterns(config_dir: Path | None = None) -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Load user-defined patterns from ~/.llmcode/security-rules.json.

    Expected format::

        {
          "patterns": [
            {"name": "internal_key", "regex": "INTERNAL_[A-Z0-9]{32}"},
            {"name": "corp_token",   "regex": "corp_tk_[a-f0-9]{40}"}
          ]
        }

    Returns compiled pattern tuples.  Invalid entries are logged and skipped.
    """
    if config_dir is None:
        config_dir = Path.home() / ".llmcode"
    rules_path = config_dir / "security-rules.json"
    if not rules_path.exists():
        return ()

    try:
        data = json.loads(rules_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load security rules from %s: %s", rules_path, exc)
        return ()

    result: list[tuple[str, re.Pattern[str]]] = []
    for entry in data.get("patterns", []):
        name = entry.get("name", "")
        regex = entry.get("regex", "")
        if not name or not regex:
            logger.warning("Skipping invalid security rule (missing name or regex): %s", entry)
            continue
        try:
            result.append((name, re.compile(regex)))
        except re.error as exc:
            logger.warning("Invalid regex in security rule %r: %s", name, exc)
    return tuple(result)


def get_patterns(config_dir: Path | None = None) -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Return builtin + user-defined patterns, cached after first call."""
    global _cached_patterns
    if _cached_patterns is not None:
        return _cached_patterns
    custom = load_custom_patterns(config_dir)
    _cached_patterns = _BUILTIN_PATTERNS + custom
    return _cached_patterns


def reset_pattern_cache() -> None:
    """Clear the pattern cache (useful for testing)."""
    global _cached_patterns
    _cached_patterns = None


def scan_output(
    text: str,
    *,
    patterns: tuple[tuple[str, re.Pattern[str]], ...] | None = None,
) -> tuple[str, list[str]]:
    """Scan text for secrets, redact them.

    Returns (cleaned_text, findings). Findings is empty if no secrets found.
    Original text returned unchanged if clean.

    If *patterns* is not provided, uses builtin + user-defined patterns.
    """
    if not text:
        return text, []

    if patterns is None:
        patterns = get_patterns()

    findings: list[str] = []
    cleaned = text

    for name, pattern in patterns:
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

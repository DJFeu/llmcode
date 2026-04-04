"""FileProtector — guards sensitive files from accidental reads and writes."""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


# Glob patterns for dangerous / credential files.
# Patterns are matched against the basename as well as the full path string.
SENSITIVE_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.key",
    "*.pem",
    "*.p12",
    "credentials.*",
    "*secret*",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "token.json",
    "*.keystore",
    ".netrc",
    ".pgpass",
)

# Patterns that are always blocked (never allow write)
_BLOCK_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.key",
    "*.pem",
    "*.p12",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "*.keystore",
    ".netrc",
    ".pgpass",
)

# Path prefixes (expanded) that are always blocked
_BLOCK_PATH_PREFIXES: tuple[str, ...] = (
    os.path.expanduser("~/.ssh/"),
    os.path.expanduser("~/.aws/"),
    os.path.expanduser("~/.config/gcloud/"),
)


@dataclass(frozen=True)
class FileProtectionResult:
    """Result of a file-protection check."""

    allowed: bool
    reason: str
    severity: Literal["block", "warn", "allow"]


def _matches_any(name: str, patterns: tuple[str, ...]) -> bool:
    """Return True if *name* matches at least one glob pattern."""
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def _is_under_blocked_prefix(path: str) -> bool:
    """Return True if the resolved path sits under a always-blocked directory."""
    try:
        resolved = str(Path(path).resolve())
    except Exception:
        resolved = path
    for prefix in _BLOCK_PATH_PREFIXES:
        if resolved.startswith(prefix):
            return True
    return False


def is_sensitive(path: str) -> bool:
    """Return True if *path* matches any sensitive pattern.

    Resolves symlinks before checking to prevent bypass via symlink indirection.
    """
    resolved = str(Path(path).resolve())
    basename = Path(resolved).name
    if _matches_any(basename, SENSITIVE_PATTERNS):
        return True
    if _is_under_blocked_prefix(resolved):
        return True
    return False


def check_write(path: str) -> FileProtectionResult:
    """Check whether writing to *path* should be allowed, warned about, or blocked.

    Resolves symlinks before checking to prevent bypass via symlink indirection.

    Rules
    -----
    - ``.env`` files and SSH/AWS/GCloud keys → **block** (never write secrets)
    - Other sensitive patterns (``credentials.*``, ``*secret*``, ``token.json``, …)
      → **warn** (needs user confirmation)
    - Everything else → **allow**
    """
    resolved = str(Path(path).resolve())
    basename = Path(resolved).name

    # Blocked: critical credential / key files
    if _matches_any(basename, _BLOCK_PATTERNS) or _is_under_blocked_prefix(resolved):
        return FileProtectionResult(
            allowed=False,
            reason=f"Writing to '{path}' is blocked: file matches a sensitive credential pattern.",
            severity="block",
        )

    # Warn: other sensitive patterns
    if is_sensitive(path):
        return FileProtectionResult(
            allowed=True,
            reason=(
                f"Writing to '{path}' requires confirmation: "
                "the file matches a sensitive data pattern."
            ),
            severity="warn",
        )

    return FileProtectionResult(allowed=True, reason="", severity="allow")


def check_read(path: str) -> FileProtectionResult:
    """Check whether reading *path* should be allowed or warned about.

    Sensitive file reads are warned (content may leak to LLM context).
    """
    if is_sensitive(path):
        return FileProtectionResult(
            allowed=True,
            reason=(
                f"Reading '{path}' may expose sensitive data to the LLM context. "
                "Proceed with caution."
            ),
            severity="warn",
        )

    return FileProtectionResult(allowed=True, reason="", severity="allow")

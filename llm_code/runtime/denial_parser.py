"""Sandbox denial parser — learns from permission errors.

Borrowed from Gemini CLI's ``sandboxDenialUtils.ts``.

When a command fails with a permission error, this module parses
stderr to extract the blocked resource (path, port, etc.) and
suggests the specific permission needed.

Design:
    - ``parse_denial()`` is LRU-cached (same error → same result)
    - Returns ``DenialInfo`` or None (not every error is a denial)
    - Does NOT auto-grant — caller decides how to act on the info
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class DenialInfo:
    """Parsed information about a permission denial."""
    blocked_path: str | None
    permission_type: str   # "read" | "write" | "execute" | "network"
    raw_error: str
    suggestion: str        # Human-readable suggestion for the user


# Compiled patterns for common denial errors
_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # Docker permission (must be before generic Permission denied)
    (
        re.compile(r"(?:Got permission denied.*?Docker|docker:\s*permission).*?(/[^\s'\"]*)", re.IGNORECASE),
        "execute",
        "Grant Docker socket access or run with sudo",
    ),
    # npm EACCES (must be before generic EACCES)
    (
        re.compile(r"EACCES.*?mkdir\s+['\"]?(/[^\s'\"]+)['\"]?", re.IGNORECASE),
        "write",
        "Grant write access to npm directory: {path}",
    ),
    # Python PermissionError (specific format, before generic)
    (
        re.compile(r"PermissionError:.*?['\"](/[^\s'\"]+)['\"]", re.IGNORECASE),
        "write",
        "Grant write access to: {path}",
    ),
    # POSIX permission denied (path before or after the error keyword)
    (
        re.compile(r"(/[^\s'\":]+).*?Permission denied|Permission denied.*?['\"]?(/[^\s'\":]+)['\"]?|EACCES.*?['\"]?(/[^\s'\":]+)['\"]?", re.IGNORECASE),
        "write",
        "Grant write access to: {path}",
    ),
    # Read permission
    (
        re.compile(r"(?:cannot (?:read|open)|No such file).*?['\"]?(/[^\s'\"]+)['\"]?", re.IGNORECASE),
        "read",
        "Grant read access to: {path}",
    ),
    # macOS sandbox deny
    (
        re.compile(r"(?:sandbox|Operation not permitted).*?(/[^\s'\"]+)", re.IGNORECASE),
        "write",
        "Grant access to: {path}",
    ),
    # Network errors
    (
        re.compile(r"(?:Connection refused|Network (?:is )?unreachable|ECONNREFUSED).*?(\d+\.\d+\.\d+\.\d+|\w+:\d+)", re.IGNORECASE),
        "network",
        "Allow network access to: {path}",
    ),
]


@lru_cache(maxsize=64)
def parse_denial(stderr: str) -> DenialInfo | None:
    """Parse a stderr string for permission denial patterns.

    Returns DenialInfo if a known denial pattern is found, None otherwise.
    The result is LRU-cached so identical errors don't re-parse.

    Parameters
    ----------
    stderr:
        The stderr output from a failed command.

    Returns
    -------
    DenialInfo | None
        Parsed denial information, or None if not a denial.
    """
    if not stderr:
        return None

    for pattern, perm_type, suggestion_template in _PATTERNS:
        match = pattern.search(stderr)
        if match:
            # Find first non-None capture group (patterns may have alternatives)
            path = next((g for g in match.groups() if g is not None), None)
            suggestion = suggestion_template.format(path=path or "(unknown)")
            return DenialInfo(
                blocked_path=path,
                permission_type=perm_type,
                raw_error=stderr[:500],
                suggestion=suggestion,
            )

    return None


def format_denial_hint(info: DenialInfo) -> str:
    """Format a DenialInfo into a hint message for the model.

    This is appended to the tool_result so the model can explain
    the failure and suggest a fix to the user.
    """
    parts = [f"\n[Permission denied: {info.permission_type}]"]
    if info.blocked_path:
        parts.append(f"Blocked resource: {info.blocked_path}")
    parts.append(f"Suggestion: {info.suggestion}")
    return "\n".join(parts)

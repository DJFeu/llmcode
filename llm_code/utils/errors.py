"""Human-friendly error message formatting for tool execute() methods."""
from __future__ import annotations

import json
import subprocess


def friendly_error(error: Exception, context: str = "") -> str:
    """Return a user-friendly error message for the given exception.

    Parameters
    ----------
    error:
        The exception to format.
    context:
        Optional extra context (e.g. the file path being operated on).
    """
    prefix = f"[{context}] " if context else ""

    if isinstance(error, FileNotFoundError):
        path = error.filename or str(error)
        return f"{prefix}File not found: {path}. Check the working directory with /cd"

    if isinstance(error, PermissionError):
        path = error.filename or str(error)
        return (
            f"{prefix}Permission denied: {path}. "
            "The file may be read-only or owned by another user"
        )

    if isinstance(error, json.JSONDecodeError):
        return (
            f"{prefix}Invalid JSON at line {error.lineno}: {error.msg}"
        )

    if isinstance(error, subprocess.TimeoutExpired):
        timeout = error.timeout
        return (
            f"{prefix}Command timed out after {timeout}s. "
            "Try increasing timeout or simplifying the command"
        )

    if isinstance(error, ConnectionError):
        # Try to extract target from the message
        target = str(error).split("'")[1] if "'" in str(error) else str(error)
        return (
            f"{prefix}Connection failed: {target}. Check if the server is running"
        )

    return f"{prefix}Error: {type(error).__name__}: {error}"


def suggest_fix(error: Exception) -> str | None:
    """Return an actionable suggestion for the given exception, or None."""
    if isinstance(error, FileNotFoundError):
        return "Use /cd to navigate to the correct directory, or verify the file path."

    if isinstance(error, PermissionError):
        return "Try running with elevated permissions, or check file ownership with `ls -la`."

    if isinstance(error, json.JSONDecodeError):
        return f"Check JSON syntax around line {error.lineno}. Common issues: trailing commas, missing quotes."

    if isinstance(error, subprocess.TimeoutExpired):
        return "Increase the timeout parameter, or break the command into smaller steps."

    if isinstance(error, ConnectionError):
        return "Verify the server is running and the URL/port is correct."

    return None

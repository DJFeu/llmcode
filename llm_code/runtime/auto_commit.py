"""Auto-commit checkpoint -- git commit individual file changes after tool edits."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_TIMEOUT_S = 5


def auto_commit_file(path: Path, tool_name: str) -> bool:
    """Stage and commit a single file as a checkpoint.

    Returns True on successful commit, False on any failure (silently).
    """
    if not path.exists():
        return False

    try:
        # Stage the specific file only
        add_result = subprocess.run(
            ["git", "add", "--", str(path)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            cwd=path.parent,
        )
        if add_result.returncode != 0:
            logger.debug("git add failed (rc=%d): %s", add_result.returncode, add_result.stderr)
            return False

        # Commit with checkpoint message
        filename = path.name
        message = f"checkpoint: {tool_name} {filename}"
        commit_result = subprocess.run(
            ["git", "commit", "-m", message, "--no-verify"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            cwd=path.parent,
        )
        if commit_result.returncode != 0:
            logger.debug("git commit failed (rc=%d): %s", commit_result.returncode, commit_result.stderr)
            return False

        logger.info("Auto-committed checkpoint: %s", message)
        return True

    except subprocess.TimeoutExpired:
        logger.warning("Auto-commit timed out for %s", path)
        return False
    except (OSError, FileNotFoundError):
        logger.debug("Auto-commit skipped -- git not available or not a repo")
        return False

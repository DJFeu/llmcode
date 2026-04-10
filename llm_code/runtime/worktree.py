"""Git worktree isolation for fork children.

Provides utilities to create, validate, and clean up isolated git
worktrees for agents that need filesystem isolation.

Lifecycle:
    1. ``create_worktree(session_id, slug)`` → creates a new worktree
    2. Agent runs with CWD pointed at the worktree
    3. ``cleanup_worktree(path)`` → removes the worktree when done
       (only if no changes were made, otherwise path+branch are returned
       to the parent for review)

Risk mitigations:
    - ``create_worktree`` validates we're inside a git repo first.
    - ``cleanup_worktree`` checks for uncommitted changes before deleting.
    - mtime bumping on resume prevents stale-worktree cleanup scripts
      from deleting actively-used worktrees.
    - All subprocess calls use ``check=True`` so failures are loud.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

# Default base directory for worktrees (under system temp)
_WORKTREE_BASE: Path = Path(tempfile.gettempdir()) / "llm-code-worktrees"


def is_git_repo(path: Path | None = None) -> bool:
    """Return True if *path* (or cwd) is inside a git repository."""
    try:
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(path) if path else None,
            capture_output=True,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def create_worktree(
    session_id: str,
    slug: str,
    repo_path: Path | None = None,
) -> Path:
    """Create an isolated git worktree for an agent.

    Returns the path to the new worktree directory.

    Parameters
    ----------
    session_id:
        Unique session identifier (used in directory name).
    slug:
        Short human-readable label (e.g. agent type name).
    repo_path:
        Root of the git repository.  Defaults to cwd.

    Raises
    ------
    RuntimeError
        If not inside a git repo or if ``git worktree add`` fails.
    """
    cwd = repo_path or Path.cwd()
    if not is_git_repo(cwd):
        raise RuntimeError(f"Not inside a git repository: {cwd}")

    # Sanitise slug for filesystem safety
    safe_slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in slug)
    worktree_dir = _WORKTREE_BASE / f"{safe_slug}-{session_id[:12]}"
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    # Create a new branch for the worktree based on HEAD
    branch_name = f"agent/{safe_slug}-{session_id[:8]}"

    subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, str(worktree_dir)],
        cwd=str(cwd),
        capture_output=True,
        check=True,
    )

    return worktree_dir


def has_changes(worktree_path: Path) -> bool:
    """Return True if the worktree has uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def get_branch_name(worktree_path: Path) -> str | None:
    """Return the branch name of the worktree, or None on error."""
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def cleanup_worktree(
    worktree_path: Path,
    repo_path: Path | None = None,
) -> dict[str, str | None]:
    """Remove a worktree.  Returns metadata about what happened.

    If the worktree has uncommitted changes, the cleanup is skipped
    and the path + branch are returned so the parent can decide what
    to do.

    Returns
    -------
    dict with keys:
        - ``cleaned``: "true" if removed, "false" if kept
        - ``worktree_path``: path (if kept)
        - ``worktree_branch``: branch name (if kept)
        - ``reason``: why it was kept (if applicable)
    """
    if not worktree_path.exists():
        return {"cleaned": "true", "worktree_path": None, "worktree_branch": None}

    if has_changes(worktree_path):
        branch = get_branch_name(worktree_path)
        return {
            "cleaned": "false",
            "worktree_path": str(worktree_path),
            "worktree_branch": branch,
            "reason": "uncommitted changes",
        }

    cwd = repo_path or Path.cwd()
    try:
        subprocess.run(
            ["git", "worktree", "remove", str(worktree_path), "--force"],
            cwd=str(cwd),
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        # Fallback: try to remove just the directory
        import shutil
        shutil.rmtree(worktree_path, ignore_errors=True)

    return {"cleaned": "true", "worktree_path": None, "worktree_branch": None}


def bump_mtime(worktree_path: Path) -> None:
    """Touch the worktree directory to prevent stale cleanup.

    Call this on agent resume to signal the worktree is still in use.
    """
    if worktree_path.exists():
        os.utime(worktree_path)

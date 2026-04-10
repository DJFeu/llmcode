"""Tests for git worktree isolation utilities."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from llm_code.runtime.worktree import (
    bump_mtime,
    cleanup_worktree,
    has_changes,
    is_git_repo,
)


class TestIsGitRepo:
    def test_non_git_dir(self, tmp_path: Path) -> None:
        assert is_git_repo(tmp_path) is False

    def test_git_repo(self, tmp_path: Path) -> None:
        subprocess.run(
            ["git", "init"], cwd=str(tmp_path),
            capture_output=True, check=True,
        )
        assert is_git_repo(tmp_path) is True


class TestHasChanges:
    def test_clean_repo(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
        (tmp_path / "f.txt").write_text("init")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)
        assert has_changes(tmp_path) is False

    def test_dirty_repo(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
        (tmp_path / "f.txt").write_text("init")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)
        (tmp_path / "new.txt").write_text("uncommitted")
        assert has_changes(tmp_path) is True

    def test_non_git_dir(self, tmp_path: Path) -> None:
        assert has_changes(tmp_path) is False


class TestCleanupWorktree:
    def test_nonexistent_path(self, tmp_path: Path) -> None:
        fake = tmp_path / "nonexistent"
        result = cleanup_worktree(fake)
        assert result["cleaned"] == "true"

    def test_dirty_worktree_kept(self, tmp_path: Path) -> None:
        # Simulate a dirty worktree (a directory with git porcelain output)
        worktree = tmp_path / "wt"
        worktree.mkdir()

        with patch("llm_code.runtime.worktree.has_changes", return_value=True):
            with patch("llm_code.runtime.worktree.get_branch_name", return_value="agent/test"):
                result = cleanup_worktree(worktree)

        assert result["cleaned"] == "false"
        assert result["worktree_branch"] == "agent/test"
        assert result["reason"] == "uncommitted changes"


class TestBumpMtime:
    def test_bumps_existing(self, tmp_path: Path) -> None:
        import time

        d = tmp_path / "wt"
        d.mkdir()
        old_mtime = d.stat().st_mtime
        time.sleep(0.05)
        bump_mtime(d)
        new_mtime = d.stat().st_mtime
        assert new_mtime >= old_mtime

    def test_noop_nonexistent(self, tmp_path: Path) -> None:
        # Should not raise
        bump_mtime(tmp_path / "nonexistent")

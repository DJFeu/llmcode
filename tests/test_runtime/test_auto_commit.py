"""Tests for llm_code.runtime.auto_commit -- automatic git checkpoint after edits."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock


from llm_code.runtime.auto_commit import auto_commit_file


class TestAutoCommitSuccess:
    def test_commits_with_correct_message(self, tmp_path: Path) -> None:
        file_path = tmp_path / "src" / "utils.py"
        file_path.parent.mkdir(parents=True)
        file_path.write_text("# content")

        mock_run = MagicMock()
        mock_run.return_value.returncode = 0

        with patch("subprocess.run", mock_run):
            result = auto_commit_file(file_path, "write_file")

        assert result is True
        calls = mock_run.call_args_list
        # First call: git add
        assert "add" in calls[0].args[0]
        # Second call: git commit
        assert "commit" in calls[1].args[0]
        commit_cmd = calls[1].args[0]
        assert "checkpoint: write_file" in " ".join(commit_cmd)

    def test_commit_message_includes_filename(self, tmp_path: Path) -> None:
        file_path = tmp_path / "app.py"
        file_path.write_text("# code")

        mock_run = MagicMock()
        mock_run.return_value.returncode = 0

        with patch("subprocess.run", mock_run):
            auto_commit_file(file_path, "edit_file")

        commit_cmd = mock_run.call_args_list[1].args[0]
        assert "app.py" in " ".join(commit_cmd)


class TestAutoCommitSkips:
    def test_not_a_git_repo(self, tmp_path: Path) -> None:
        file_path = tmp_path / "file.py"
        file_path.write_text("# code")

        mock_run = MagicMock()
        mock_run.side_effect = [
            MagicMock(returncode=128),  # git add fails (not a repo)
        ]

        with patch("subprocess.run", mock_run):
            result = auto_commit_file(file_path, "write_file")

        assert result is False

    def test_file_not_found(self) -> None:
        result = auto_commit_file(Path("/nonexistent/file.py"), "write_file")
        assert result is False

    def test_subprocess_timeout(self, tmp_path: Path) -> None:
        import subprocess
        file_path = tmp_path / "slow.py"
        file_path.write_text("# slow")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            result = auto_commit_file(file_path, "write_file")

        assert result is False

    def test_commit_hook_failure_returns_false(self, tmp_path: Path) -> None:
        file_path = tmp_path / "hook_fail.py"
        file_path.write_text("# code")

        mock_run = MagicMock()
        mock_run.side_effect = [
            MagicMock(returncode=0),   # git add succeeds
            MagicMock(returncode=1),   # git commit fails (pre-commit hook)
        ]

        with patch("subprocess.run", mock_run):
            result = auto_commit_file(file_path, "edit_file")

        assert result is False

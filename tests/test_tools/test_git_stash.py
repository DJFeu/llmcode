"""Tests for git stash helpers and GitBranchTool auto-stash integration."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from llm_code.tools.git_tools import GitBranchTool, _auto_stash, _auto_unstash
from llm_code.tools.base import ToolResult


# ---------------------------------------------------------------------------
# _auto_stash
# ---------------------------------------------------------------------------


class TestAutoStash:
    def test_returns_false_when_clean(self):
        """No stash created when working tree is clean."""
        clean_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=clean_result) as mock_run:
            result = _auto_stash("/tmp/repo")
        assert result is False
        # Only status check, no stash push
        assert mock_run.call_count == 1

    def test_returns_true_when_dirty(self):
        """Stash is created when there are uncommitted changes."""
        status_result = MagicMock(returncode=0, stdout=" M file.py\n", stderr="")
        stash_result = MagicMock(returncode=0, stdout="Saved working directory", stderr="")

        with patch("subprocess.run", side_effect=[status_result, stash_result]) as mock_run:
            result = _auto_stash("/tmp/repo")

        assert result is True
        # Second call should be the stash push
        stash_call = mock_run.call_args_list[1]
        assert "stash" in stash_call[0][0]
        assert "push" in stash_call[0][0]
        assert "llm-code auto-stash" in stash_call[0][0]

    def test_returns_false_when_stash_fails(self):
        """Returns False if stash push command fails."""
        status_result = MagicMock(returncode=0, stdout=" M file.py\n", stderr="")
        stash_result = MagicMock(returncode=1, stdout="", stderr="error")

        with patch("subprocess.run", side_effect=[status_result, stash_result]):
            result = _auto_stash("/tmp/repo")

        assert result is False

    def test_returns_false_when_status_fails(self):
        """Returns False if git status fails (not a git repo)."""
        status_result = MagicMock(returncode=128, stdout="", stderr="not a git repo")
        with patch("subprocess.run", return_value=status_result):
            result = _auto_stash("/tmp/repo")
        assert result is False

    def test_uses_cwd_default(self):
        """Uses os.getcwd() when cwd is None."""
        import os
        clean = MagicMock(returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=clean) as mock_run:
            _auto_stash()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["cwd"] == os.getcwd()


# ---------------------------------------------------------------------------
# _auto_unstash
# ---------------------------------------------------------------------------


class TestAutoUnstash:
    def test_calls_stash_pop(self):
        """Calls git stash pop."""
        pop_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=pop_result) as mock_run:
            _auto_unstash("/tmp/repo")
        args = mock_run.call_args[0][0]
        assert args == ["git", "stash", "pop"]

    def test_uses_cwd_default(self):
        """Uses os.getcwd() when cwd is None."""
        import os
        pop_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=pop_result) as mock_run:
            _auto_unstash()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["cwd"] == os.getcwd()


# ---------------------------------------------------------------------------
# GitBranchTool — switch action auto-stash integration
# ---------------------------------------------------------------------------


class TestGitBranchToolAutoStash:
    def test_switch_auto_stashes_and_unstashes(self):
        """Branch switch auto-stashes dirty tree and unstashes after checkout."""
        tool = GitBranchTool()

        with (
            patch("llm_code.tools.git_tools._auto_stash", return_value=True) as mock_stash,
            patch("llm_code.tools.git_tools._auto_unstash") as mock_unstash,
            patch(
                "llm_code.tools.git_tools._run_git",
                return_value=ToolResult(output="Switched to branch 'feat'"),
            ) as mock_run,
        ):
            result = tool.execute({"action": "switch", "name": "feat"})

        mock_stash.assert_called_once()
        mock_run.assert_called_once_with(["checkout", "feat"])
        mock_unstash.assert_called_once()
        assert not result.is_error

    def test_switch_no_unstash_when_nothing_stashed(self):
        """No unstash call when auto-stash returned False (clean tree)."""
        tool = GitBranchTool()

        with (
            patch("llm_code.tools.git_tools._auto_stash", return_value=False),
            patch("llm_code.tools.git_tools._auto_unstash") as mock_unstash,
            patch(
                "llm_code.tools.git_tools._run_git",
                return_value=ToolResult(output="Switched to branch 'main'"),
            ),
        ):
            result = tool.execute({"action": "switch", "name": "main"})

        mock_unstash.assert_not_called()
        assert not result.is_error

    def test_switch_requires_name(self):
        """Returns error when branch name is missing for switch."""
        tool = GitBranchTool()
        result = tool.execute({"action": "switch", "name": ""})
        assert result.is_error
        assert "Branch name required" in result.output

    def test_list_does_not_stash(self):
        """List action does not trigger auto-stash."""
        tool = GitBranchTool()
        with (
            patch("llm_code.tools.git_tools._auto_stash") as mock_stash,
            patch(
                "llm_code.tools.git_tools._run_git",
                return_value=ToolResult(output="* main"),
            ),
        ):
            tool.execute({"action": "list"})
        mock_stash.assert_not_called()

    def test_switch_unstashes_even_on_checkout_error(self):
        """Unstash is called even when the checkout itself fails."""
        tool = GitBranchTool()

        with (
            patch("llm_code.tools.git_tools._auto_stash", return_value=True),
            patch("llm_code.tools.git_tools._auto_unstash") as mock_unstash,
            patch(
                "llm_code.tools.git_tools._run_git",
                return_value=ToolResult(output="error: pathspec not found", is_error=True),
            ),
        ):
            result = tool.execute({"action": "switch", "name": "nonexistent"})

        mock_unstash.assert_called_once()
        assert result.is_error

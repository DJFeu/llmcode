"""Tests for SwarmManager worktree backend integration."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_code.swarm.manager import SwarmManager
from llm_code.swarm.types import SwarmStatus


@pytest.fixture
def manager_auto(tmp_path):
    return SwarmManager(
        swarm_dir=tmp_path / "swarm",
        max_members=5,
        backend_preference="auto",
    )


@pytest.fixture
def manager_worktree(tmp_path):
    return SwarmManager(
        swarm_dir=tmp_path / "swarm",
        max_members=5,
        backend_preference="worktree",
    )


class TestResolveBackendWorktree:
    def test_explicit_worktree_returns_worktree(self, manager_auto):
        """Requesting 'worktree' explicitly always returns 'worktree'."""
        result = manager_auto._resolve_backend("worktree")
        assert result == "worktree"

    def test_auto_prefers_worktree_when_git_available(self, manager_auto):
        """In auto mode, worktree is preferred when git is available."""
        with patch.object(manager_auto, "_is_git_repo", return_value=True):
            with patch.object(manager_auto, "_git_supports_worktree", return_value=True):
                result = manager_auto._resolve_backend("auto")
        assert result == "worktree"

    def test_auto_fallback_to_tmux_when_not_in_git(self, manager_auto):
        """In auto mode, falls back to tmux when not in a git repo."""
        with patch.object(manager_auto, "_is_git_repo", return_value=False):
            with patch("llm_code.swarm.manager.is_tmux_available", return_value=True):
                result = manager_auto._resolve_backend("auto")
        assert result == "tmux"

    def test_auto_fallback_to_subprocess_when_no_git_no_tmux(self, manager_auto):
        """In auto mode, falls back to subprocess when no git and no tmux."""
        with patch.object(manager_auto, "_is_git_repo", return_value=False):
            with patch("llm_code.swarm.manager.is_tmux_available", return_value=False):
                result = manager_auto._resolve_backend("auto")
        assert result == "subprocess"

    def test_auto_fallback_when_git_no_worktree_support(self, manager_auto):
        """In auto mode, falls back if git doesn't support worktrees."""
        with patch.object(manager_auto, "_is_git_repo", return_value=True):
            with patch.object(manager_auto, "_git_supports_worktree", return_value=False):
                with patch("llm_code.swarm.manager.is_tmux_available", return_value=False):
                    result = manager_auto._resolve_backend("auto")
        assert result == "subprocess"

    def test_preference_worktree_resolved_as_worktree(self, manager_worktree):
        """Backend preference 'worktree' resolves to worktree in auto mode."""
        result = manager_worktree._resolve_backend("auto")
        assert result == "worktree"


class TestGitHelpers:
    def test_is_git_repo_delegates_to_subprocess(self, manager_auto):
        """_is_git_repo runs git rev-parse and checks return code."""
        with patch("llm_code.swarm.manager.sp.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert manager_auto._is_git_repo() is True

        with patch("llm_code.swarm.manager.sp.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128)
            assert manager_auto._is_git_repo() is False

    def test_git_supports_worktree_version_check(self, manager_auto):
        """_git_supports_worktree returns True for git >= 2.15."""
        with patch("llm_code.swarm.manager.sp.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="git version 2.39.2")
            assert manager_auto._git_supports_worktree() is True

    def test_git_supports_worktree_old_version(self, manager_auto):
        """_git_supports_worktree returns False for git < 2.15."""
        with patch("llm_code.swarm.manager.sp.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="git version 2.14.0")
            assert manager_auto._git_supports_worktree() is False

    def test_git_supports_worktree_failure(self, manager_auto):
        """_git_supports_worktree returns False when git command fails."""
        with patch("llm_code.swarm.manager.sp.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            assert manager_auto._git_supports_worktree() is False


class TestCreateMemberWorktree:
    @pytest.mark.asyncio
    async def test_create_member_worktree_backend(self, tmp_path):
        """create_member with worktree backend uses WorktreeBackend.spawn."""
        from llm_code.swarm.backend_worktree import WorktreeBackend

        mgr = SwarmManager(
            swarm_dir=tmp_path / "swarm",
            max_members=5,
            backend_preference="subprocess",  # default pref doesn't matter
        )

        mock_wt_backend = MagicMock(spec=WorktreeBackend)
        mock_wt_backend.spawn = AsyncMock(return_value=777)
        mgr._worktree_backend = mock_wt_backend

        with patch.object(mgr, "_resolve_backend", return_value="worktree"):
            member = await mgr.create_member(role="analyst", task="analyze code")

        assert member.role == "analyst"
        assert member.backend == "worktree"
        assert member.status == SwarmStatus.RUNNING
        mock_wt_backend.spawn.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_member_worktree_lazy_init(self, tmp_path):
        """WorktreeBackend is lazily initialised on first worktree create_member."""
        mgr = SwarmManager(
            swarm_dir=tmp_path / "swarm",
            max_members=5,
            backend_preference="subprocess",
        )

        assert mgr._worktree_backend is None  # not yet initialised

        with patch.object(mgr, "_resolve_backend", return_value="worktree"):
            with patch(
                "llm_code.swarm.manager.WorktreeBackend"
            ) as mock_cls:
                mock_instance = MagicMock()
                mock_instance.spawn = AsyncMock(return_value=1)
                mock_cls.return_value = mock_instance

                await mgr.create_member(role="r", task="t")

        mock_cls.assert_called_once()

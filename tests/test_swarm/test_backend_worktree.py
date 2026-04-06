"""Tests for the git worktree backend."""
from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_code.runtime.config import WorktreeConfig
from llm_code.swarm.backend_worktree import WorktreeBackend, WorktreeResult


class TestWorktreeResult:
    def test_result_defaults(self):
        r = WorktreeResult(member_id="abc", status="success")
        assert r.member_id == "abc"
        assert r.status == "success"
        assert r.diff == ""
        assert r.branch_name == ""
        assert r.conflict_files == ()
        assert r.message == ""

    def test_result_frozen(self):
        r = WorktreeResult(member_id="abc", status="success")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            r.status = "error"  # type: ignore[misc]

    def test_result_with_all_fields(self):
        r = WorktreeResult(
            member_id="xyz",
            status="conflict",
            diff="--- a\n+++ b",
            branch_name="agent/xyz",
            conflict_files=("file.py", "other.py"),
            message="Merge conflict",
        )
        assert r.status == "conflict"
        assert r.branch_name == "agent/xyz"
        assert len(r.conflict_files) == 2
        assert r.message == "Merge conflict"

    def test_result_error_status(self):
        r = WorktreeResult(member_id="abc", status="error", message="something failed")
        assert r.status == "error"
        assert r.message == "something failed"


class TestWorktreeBackendInit:
    def test_init(self, tmp_path):
        cfg = WorktreeConfig()
        backend = WorktreeBackend(project_dir=tmp_path, config=cfg)
        assert backend is not None

    def test_is_running_unknown(self, tmp_path):
        cfg = WorktreeConfig()
        backend = WorktreeBackend(project_dir=tmp_path, config=cfg)
        assert backend.is_running("nonexistent") is False


class TestWorktreeBackendSpawn:
    @pytest.mark.asyncio
    async def test_spawn_creates_worktree(self, tmp_path):
        """spawn calls git worktree add and starts a subprocess."""
        cfg = WorktreeConfig()
        backend = WorktreeBackend(project_dir=tmp_path, config=cfg)

        mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout=""))
        mock_proc = MagicMock()
        mock_proc.pid = 9999
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()

        with patch("llm_code.swarm.backend_worktree.subprocess.run", mock_run):
            with patch(
                "llm_code.swarm.backend_worktree.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=mock_proc),
            ):
                pid = await backend.spawn(
                    member_id="abc123",
                    role="coder",
                    task="write tests",
                )

        assert pid == 9999
        # git worktree add should have been called
        git_calls = [c for c in mock_run.call_args_list if "worktree" in str(c)]
        assert len(git_calls) >= 1

    @pytest.mark.asyncio
    async def test_spawn_marks_running(self, tmp_path):
        cfg = WorktreeConfig()
        backend = WorktreeBackend(project_dir=tmp_path, config=cfg)

        mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout=""))
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.returncode = None  # process still running
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()

        with patch("llm_code.swarm.backend_worktree.subprocess.run", mock_run):
            with patch(
                "llm_code.swarm.backend_worktree.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=mock_proc),
            ):
                await backend.spawn(member_id="member1", role="r", task="t")

        assert backend.is_running("member1") is True


class TestWorktreeBackendComplete:
    @pytest.mark.asyncio
    async def test_complete_unknown_returns_error(self, tmp_path):
        cfg = WorktreeConfig()
        backend = WorktreeBackend(project_dir=tmp_path, config=cfg)
        result = await backend.complete("unknown_member")
        assert result.status == "error"
        assert result.member_id == "unknown_member"

    @pytest.mark.asyncio
    async def test_complete_diff_mode(self, tmp_path):
        """complete with on_complete=diff commits, diffs, cleans up."""
        cfg = WorktreeConfig(on_complete="diff", cleanup_on_success=True)
        backend = WorktreeBackend(project_dir=tmp_path, config=cfg)

        # Pre-seed a member entry
        backend._worktrees["member99"] = tmp_path / "wt-member99"
        backend._procs["member99"] = MagicMock(returncode=0)
        backend._branch_names["member99"] = "agent/member99"

        run_results = [
            MagicMock(returncode=0, stdout=""),  # git add
            MagicMock(returncode=0, stdout=""),  # git commit
            MagicMock(returncode=0, stdout="diff --git..."),  # git diff
            MagicMock(returncode=0, stdout=""),  # git worktree remove
            MagicMock(returncode=0, stdout=""),  # git branch -d
        ]

        with patch(
            "llm_code.swarm.backend_worktree.subprocess.run",
            side_effect=run_results,
        ):
            result = await backend.complete("member99")

        assert result.member_id == "member99"
        assert result.status == "success"
        assert result.branch_name == "agent/member99"

    @pytest.mark.asyncio
    async def test_complete_branch_mode(self, tmp_path):
        """complete with on_complete=branch keeps the branch."""
        cfg = WorktreeConfig(on_complete="branch")
        backend = WorktreeBackend(project_dir=tmp_path, config=cfg)

        backend._worktrees["m1"] = tmp_path / "wt-m1"
        backend._procs["m1"] = MagicMock(returncode=0)
        backend._branch_names["m1"] = "agent/m1"

        run_results = [
            MagicMock(returncode=0, stdout=""),  # git add
            MagicMock(returncode=0, stdout=""),  # git commit
            MagicMock(returncode=0, stdout=""),  # git worktree remove
        ]

        with patch(
            "llm_code.swarm.backend_worktree.subprocess.run",
            side_effect=run_results,
        ):
            result = await backend.complete("m1")

        assert result.status == "success"
        assert result.branch_name == "agent/m1"


class TestWorktreeBackendStop:
    @pytest.mark.asyncio
    async def test_stop_unknown_member_noop(self, tmp_path):
        cfg = WorktreeConfig()
        backend = WorktreeBackend(project_dir=tmp_path, config=cfg)
        # Should not raise
        await backend.stop("nonexistent")

    @pytest.mark.asyncio
    async def test_stop_all(self, tmp_path):
        cfg = WorktreeConfig()
        backend = WorktreeBackend(project_dir=tmp_path, config=cfg)
        # Pre-seed two entries
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        backend._procs["a"] = mock_proc
        backend._procs["b"] = mock_proc

        with patch.object(backend, "stop", new=AsyncMock()) as mock_stop:
            await backend.stop_all()

        assert mock_stop.call_count == 2

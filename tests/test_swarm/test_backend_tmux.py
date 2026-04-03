"""Tests for tmux-based swarm backend."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from llm_code.swarm.backend_tmux import TmuxBackend, is_tmux_available


class TestIsTmuxAvailable:
    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("os.environ", {"TMUX": "/tmp/tmux-1001/default,12345,0"})
    def test_available_when_in_tmux(self, mock_which):
        assert is_tmux_available() is True

    @patch("shutil.which", return_value=None)
    def test_unavailable_when_no_binary(self, mock_which):
        assert is_tmux_available() is False

    @patch("shutil.which", return_value="/usr/bin/tmux")
    @patch("os.environ", {})
    def test_unavailable_when_not_in_session(self, mock_which):
        assert is_tmux_available() is False


class TestTmuxBackendSpawn:
    @patch("subprocess.run")
    def test_spawn_calls_split_window(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="42\n")
        backend = TmuxBackend()
        backend.spawn(
            member_id="w1",
            role="analyst",
            task="Review code quality",
        )
        assert mock_run.called
        # The command should contain split-window or new-window
        cmd_str = " ".join(str(a) for call in mock_run.call_args_list for a in call[0][0])
        assert "tmux" in cmd_str

    @patch("subprocess.run")
    def test_spawn_returns_pane_id(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="%5\n")
        backend = TmuxBackend()
        pane_id = backend.spawn(member_id="w1", role="r", task="t")
        assert pane_id is not None


class TestTmuxBackendStop:
    @patch("subprocess.run")
    def test_stop_kills_pane(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="%5\n")
        backend = TmuxBackend()
        backend.spawn(member_id="w1", role="r", task="t")
        backend.stop("w1")
        # Should call tmux kill-pane
        kill_calls = [c for c in mock_run.call_args_list if "kill-pane" in str(c)]
        assert len(kill_calls) >= 1

    @patch("subprocess.run")
    def test_stop_unknown_noop(self, mock_run):
        backend = TmuxBackend()
        backend.stop("nonexistent")  # should not raise

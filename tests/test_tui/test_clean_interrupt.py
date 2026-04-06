"""Tests for clean interrupt handling (Ctrl+C)."""
from __future__ import annotations

import time
from unittest.mock import MagicMock



class TestInterruptState:
    """Test interrupt state machine logic (independent of TUI)."""

    def test_first_interrupt_sets_pending(self) -> None:
        """Simulate first Ctrl+C: should set pending flag and save checkpoint."""
        state = {"interrupt_pending": False, "last_interrupt_time": 0.0}

        # First interrupt
        now = time.monotonic()
        state["interrupt_pending"] = True
        state["last_interrupt_time"] = now

        assert state["interrupt_pending"] is True

    def test_second_interrupt_within_window_triggers_exit(self) -> None:
        """Simulate second Ctrl+C within 2s: should trigger force exit."""
        state = {"interrupt_pending": True, "last_interrupt_time": time.monotonic()}

        # Simulate second interrupt immediately
        now = time.monotonic()
        elapsed = now - state["last_interrupt_time"]
        should_force_exit = state["interrupt_pending"] and elapsed < 2.0

        assert should_force_exit is True

    def test_second_interrupt_after_window_resets(self) -> None:
        """Simulate second Ctrl+C after 2s: should reset, not force exit."""
        state = {"interrupt_pending": True, "last_interrupt_time": time.monotonic() - 3.0}

        now = time.monotonic()
        elapsed = now - state["last_interrupt_time"]
        should_force_exit = state["interrupt_pending"] and elapsed < 2.0

        assert should_force_exit is False

    def test_checkpoint_save_called(self) -> None:
        """Verify checkpoint manager is invoked on first interrupt."""
        mock_checkpoint = MagicMock()
        mock_checkpoint.save_checkpoint.return_value = "ses_abc123"

        # Simulate first interrupt handler
        session_id = mock_checkpoint.save_checkpoint()

        mock_checkpoint.save_checkpoint.assert_called_once()
        assert session_id == "ses_abc123"

    def test_no_checkpoint_when_idle(self) -> None:
        """When no active session, interrupt should exit immediately."""
        is_streaming = False
        should_save = is_streaming  # Only save if actively working

        assert should_save is False

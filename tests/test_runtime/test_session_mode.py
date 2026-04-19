"""M3: SessionMode + headless auto-approve."""
from __future__ import annotations

import pytest

from llm_code.runtime.session_mode import (
    SessionMode,
    auto_approve_safe,
    from_string,
)


class TestEnum:
    def test_values(self) -> None:
        assert SessionMode.INTERACTIVE.value == "interactive"
        assert SessionMode.HEADLESS.value == "headless"
        assert SessionMode.SDK.value == "sdk"

    def test_from_string(self) -> None:
        assert from_string("interactive") is SessionMode.INTERACTIVE
        assert from_string("HEADLESS") is SessionMode.HEADLESS
        assert from_string("sdk") is SessionMode.SDK

    def test_from_string_unknown(self) -> None:
        with pytest.raises(ValueError):
            from_string("gui")


class TestAutoApprove:
    def test_interactive_never_auto_approves(self) -> None:
        """Interactive mode always prompts — user stays in loop."""
        assert auto_approve_safe(SessionMode.INTERACTIVE, "read_file") is False
        assert auto_approve_safe(SessionMode.INTERACTIVE, "bash") is False

    def test_headless_auto_approves_read_only(self) -> None:
        for tool in ("read_file", "grep_search", "glob_search", "git_status"):
            assert auto_approve_safe(SessionMode.HEADLESS, tool) is True

    def test_headless_blocks_destructive(self) -> None:
        for tool in ("bash", "edit_file", "write_file"):
            assert auto_approve_safe(SessionMode.HEADLESS, tool) is False

    def test_sdk_matches_headless(self) -> None:
        """SDK mode has the same auto-approve semantics as headless —
        caller drives the loop and can't be prompted."""
        assert auto_approve_safe(SessionMode.SDK, "read_file") is True
        assert auto_approve_safe(SessionMode.SDK, "bash") is False

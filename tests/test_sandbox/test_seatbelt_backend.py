"""Tests for SeatbeltSandboxBackend (E2 — macOS sandbox-exec)."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from llm_code.sandbox.policy_manager import SandboxPolicy


@pytest.fixture
def seatbelt_available(monkeypatch):
    monkeypatch.setattr(
        "llm_code.sandbox.seatbelt.shutil.which",
        lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None,
    )


@pytest.fixture
def seatbelt_absent(monkeypatch):
    monkeypatch.setattr(
        "llm_code.sandbox.seatbelt.shutil.which",
        lambda name: None,
    )


class TestAvailability:
    def test_constructor_raises_when_sandbox_exec_missing(self, seatbelt_absent) -> None:
        from llm_code.sandbox.seatbelt import SeatbeltSandboxBackend
        with pytest.raises(RuntimeError, match="sandbox-exec"):
            SeatbeltSandboxBackend()

    def test_constructor_succeeds_when_present(self, seatbelt_available) -> None:
        from llm_code.sandbox.seatbelt import SeatbeltSandboxBackend
        backend = SeatbeltSandboxBackend()
        assert backend.name == "seatbelt"


class TestProfileGeneration:
    def _profile_for(self, policy: SandboxPolicy) -> str:
        from llm_code.sandbox.seatbelt import SeatbeltSandboxBackend
        return SeatbeltSandboxBackend()._render_profile(policy)

    def test_profile_version_header(self, seatbelt_available) -> None:
        assert self._profile_for(SandboxPolicy()).startswith("(version 1)")

    def test_deny_default_always_present(self, seatbelt_available) -> None:
        assert "(deny default)" in self._profile_for(SandboxPolicy())

    def test_no_network_omits_allow_network(self, seatbelt_available) -> None:
        p = self._profile_for(SandboxPolicy(allow_network=False))
        assert "(allow network" not in p

    def test_network_allowed_adds_allow_network(self, seatbelt_available) -> None:
        p = self._profile_for(SandboxPolicy(allow_network=True))
        assert "(allow network*)" in p

    def test_read_only_allows_file_read(self, seatbelt_available) -> None:
        p = self._profile_for(SandboxPolicy(allow_read=True, allow_write=False))
        assert "(allow file-read*)" in p
        assert "(allow file-write*" not in p

    def test_writable_allows_file_write_in_workspace(self, seatbelt_available) -> None:
        p = self._profile_for(SandboxPolicy(allow_read=True, allow_write=True))
        assert "(allow file-read*)" in p
        assert "(allow file-write*" in p

    def test_always_allows_process_exec(self, seatbelt_available) -> None:
        p = self._profile_for(SandboxPolicy())
        assert "(allow process-exec" in p or "(allow process-fork)" in p


class TestArgumentConstruction:
    def _run_and_capture(self, policy: SandboxPolicy):
        from llm_code.sandbox.seatbelt import SeatbeltSandboxBackend
        backend = SeatbeltSandboxBackend()
        with patch(
            "llm_code.sandbox.seatbelt.subprocess.run",
            return_value=MagicMock(stdout="", stderr="", returncode=0),
        ) as mock_run:
            backend.execute(["echo", "hi"], policy)
        return mock_run.call_args

    def test_uses_p_flag_with_inline_profile(self, seatbelt_available) -> None:
        call = self._run_and_capture(SandboxPolicy())
        args = call.args[0]
        assert args[0].endswith("sandbox-exec")
        assert "-p" in args
        p_idx = args.index("-p")
        assert args[p_idx + 1].startswith("(version 1)")

    def test_command_tail(self, seatbelt_available) -> None:
        call = self._run_and_capture(SandboxPolicy())
        args = call.args[0]
        assert args[-2:] == ["echo", "hi"]


class TestResultTranslation:
    def test_success(self, seatbelt_available) -> None:
        from llm_code.sandbox.seatbelt import SeatbeltSandboxBackend
        fake = MagicMock(stdout="ok", stderr="", returncode=0)
        with patch(
            "llm_code.sandbox.seatbelt.subprocess.run",
            return_value=fake,
        ):
            result = SeatbeltSandboxBackend().execute(["ls"], SandboxPolicy())
        assert result.exit_code == 0
        assert result.stdout == "ok"

    def test_timeout(self, seatbelt_available) -> None:
        from llm_code.sandbox.seatbelt import SeatbeltSandboxBackend
        with patch(
            "llm_code.sandbox.seatbelt.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="sandbox-exec", timeout=30),
        ):
            result = SeatbeltSandboxBackend().execute(["sleep", "100"], SandboxPolicy())
        assert result.exit_code == 124
        assert "timed out" in result.stderr.lower()

    def test_exec_error(self, seatbelt_available) -> None:
        from llm_code.sandbox.seatbelt import SeatbeltSandboxBackend
        with patch(
            "llm_code.sandbox.seatbelt.subprocess.run",
            side_effect=OSError("cannot start sandbox"),
        ):
            result = SeatbeltSandboxBackend().execute(["echo"], SandboxPolicy())
        assert result.exit_code != 0
        assert "cannot start" in result.stderr or "sandbox" in result.stderr

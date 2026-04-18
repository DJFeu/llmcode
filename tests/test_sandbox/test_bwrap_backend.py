"""Tests for BwrapSandboxBackend (E1 — Linux bubblewrap enforcement).

Bwrap (bubblewrap) is a Linux SUID sandbox helper built on top of
user namespaces + seccomp. Unlike Docker it's per-call — each
invocation runs under its own policy — which makes it a natural fit
for the SandboxPolicy -> OS-enforcement translation.

Tests mock ``subprocess.run`` so the suite stays hermetic; no bwrap
binary is required on the CI host.
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from llm_code.sandbox.policy_manager import SandboxBackend, SandboxPolicy


@pytest.fixture
def bwrap_available(monkeypatch):
    """Pretend /usr/bin/bwrap exists so the constructor succeeds."""
    monkeypatch.setattr(
        "llm_code.sandbox.bwrap.shutil.which",
        lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
    )


@pytest.fixture
def bwrap_absent(monkeypatch):
    monkeypatch.setattr(
        "llm_code.sandbox.bwrap.shutil.which",
        lambda name: None,
    )


# ---------- Availability ----------


class TestAvailability:
    def test_constructor_raises_when_bwrap_missing(self, bwrap_absent) -> None:
        from llm_code.sandbox.bwrap import BwrapSandboxBackend

        with pytest.raises(RuntimeError, match="bwrap"):
            BwrapSandboxBackend()

    def test_constructor_succeeds_when_bwrap_present(self, bwrap_available) -> None:
        from llm_code.sandbox.bwrap import BwrapSandboxBackend

        backend = BwrapSandboxBackend()
        assert backend.name == "bwrap"
        assert isinstance(backend, SandboxBackend) or hasattr(backend, "execute")


# ---------- Argument translation ----------


class TestArgumentTranslation:
    def _run_and_capture(self, policy: SandboxPolicy, *, command=None):
        from llm_code.sandbox.bwrap import BwrapSandboxBackend

        backend = BwrapSandboxBackend()
        fake_proc = MagicMock(
            stdout="ok", stderr="", returncode=0,
        )
        with patch(
            "llm_code.sandbox.bwrap.subprocess.run",
            return_value=fake_proc,
        ) as mock_run:
            backend.execute(command or ["echo", "hi"], policy)
        return mock_run.call_args

    def test_no_network_adds_unshare_net(
        self, bwrap_available,
    ) -> None:
        call = self._run_and_capture(
            SandboxPolicy(allow_read=True, allow_write=True, allow_network=False),
        )
        args = call.args[0]
        assert "--unshare-net" in args

    def test_network_allowed_omits_unshare_net(
        self, bwrap_available,
    ) -> None:
        call = self._run_and_capture(
            SandboxPolicy(allow_network=True, allow_write=True),
        )
        args = call.args[0]
        assert "--unshare-net" not in args
        assert "--share-net" in args or "--unshare-net" not in args

    def test_read_only_uses_ro_bind(
        self, bwrap_available,
    ) -> None:
        call = self._run_and_capture(
            SandboxPolicy(allow_read=True, allow_write=False, allow_network=False),
        )
        args = call.args[0]
        assert "--ro-bind" in args

    def test_writable_uses_bind(
        self, bwrap_available,
    ) -> None:
        call = self._run_and_capture(
            SandboxPolicy(allow_read=True, allow_write=True, allow_network=False),
        )
        args = call.args[0]
        # Writable mount → --bind (not --ro-bind)
        has_writable = any(
            a == "--bind" for a in args
        )
        assert has_writable

    def test_bwrap_binary_comes_first(self, bwrap_available) -> None:
        call = self._run_and_capture(SandboxPolicy())
        args = call.args[0]
        assert args[0].endswith("bwrap")

    def test_command_comes_after_double_dash_or_last(
        self, bwrap_available,
    ) -> None:
        """bwrap flags are terminated by either ``--`` or by the fact
        that every non-flag after bwrap's own args is the command.
        The command must appear as the last items of the argv."""
        call = self._run_and_capture(
            SandboxPolicy(),
            command=["echo", "hello world"],
        )
        args = call.args[0]
        # Last two items are the command
        assert args[-2:] == ["echo", "hello world"]


# ---------- SandboxResult translation ----------


class TestResultTranslation:
    def test_success_exit_code_preserved(self, bwrap_available) -> None:
        from llm_code.sandbox.bwrap import BwrapSandboxBackend

        backend = BwrapSandboxBackend()
        fake = MagicMock(stdout="out", stderr="err", returncode=0)
        with patch(
            "llm_code.sandbox.bwrap.subprocess.run", return_value=fake,
        ):
            result = backend.execute(["ls"], SandboxPolicy())
        assert result.exit_code == 0
        assert result.stdout == "out"
        assert result.stderr == "err"
        assert result.is_success is True

    def test_failure_exit_code_propagates(self, bwrap_available) -> None:
        from llm_code.sandbox.bwrap import BwrapSandboxBackend

        backend = BwrapSandboxBackend()
        fake = MagicMock(stdout="", stderr="bwrap: permission denied", returncode=1)
        with patch(
            "llm_code.sandbox.bwrap.subprocess.run", return_value=fake,
        ):
            result = backend.execute(["rm", "/x"], SandboxPolicy())
        assert result.exit_code == 1
        assert "permission denied" in result.stderr
        assert result.is_success is False

    def test_timeout_maps_to_124(self, bwrap_available) -> None:
        from llm_code.sandbox.bwrap import BwrapSandboxBackend

        backend = BwrapSandboxBackend()
        with patch(
            "llm_code.sandbox.bwrap.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="bwrap", timeout=30),
        ):
            result = backend.execute(["sleep", "100"], SandboxPolicy())
        assert result.exit_code == 124
        assert "timed out" in result.stderr.lower()

    def test_unexpected_exception_becomes_failure(self, bwrap_available) -> None:
        from llm_code.sandbox.bwrap import BwrapSandboxBackend

        backend = BwrapSandboxBackend()
        with patch(
            "llm_code.sandbox.bwrap.subprocess.run",
            side_effect=OSError("/usr/bin/bwrap: cannot execute"),
        ):
            result = backend.execute(["echo"], SandboxPolicy())
        assert result.exit_code != 0
        assert "cannot execute" in result.stderr or "bwrap" in result.stderr


# ---------- choose_backend integration (informational) ----------


class TestBackendInfo:
    def test_policy_stricter_than_native_still_enforced_per_call(
        self, bwrap_available,
    ) -> None:
        """Bwrap is per-call, so any policy the caller supplies is
        enforced fresh on each execute. No need for the Docker-style
        gate; the policy lands directly in the bwrap argv."""
        from llm_code.sandbox.bwrap import BwrapSandboxBackend

        backend = BwrapSandboxBackend()
        with patch(
            "llm_code.sandbox.bwrap.subprocess.run",
            return_value=MagicMock(stdout="", stderr="", returncode=0),
        ) as mock_run:
            backend.execute(
                ["curl", "x"],
                SandboxPolicy(allow_network=False, allow_write=False),
            )
            args = mock_run.call_args.args[0]
            assert "--unshare-net" in args
            assert "--ro-bind" in args

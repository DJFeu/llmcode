"""Tests for LandlockSandboxBackend (L1 — Linux 5.13+ skeleton).

Landlock is a Linux LSM that lets unprivileged processes restrict
their own filesystem / network access. The full ctypes integration
(landlock_create_ruleset / landlock_add_rule / landlock_restrict_self
via syscalls 444-446) is a larger effort; this commit ships a working
skeleton that:

    * Fails construction on non-Linux or kernel < 5.13 so callers know
      the backend is unavailable.
    * Delegates ``execute`` / ``execute_streaming`` to an internal
      BwrapSandboxBackend (which under the hood already uses landlock
      on sufficiently-new kernels).

Future work replaces the bwrap delegate with direct ctypes syscalls.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llm_code.sandbox.policy_manager import (
    SandboxPolicy,
    SandboxResult,
    has_streaming,
)


def _fake_uname(release: str, sysname: str = "Linux"):
    m = MagicMock()
    m.release = release
    m.sysname = sysname
    return m


@pytest.fixture
def landlock_host(monkeypatch):
    """Pretend host is Linux 5.15 with bwrap available."""
    monkeypatch.setattr(
        "llm_code.sandbox.landlock.os.uname",
        lambda: _fake_uname("5.15.0-76-generic"),
    )
    monkeypatch.setattr(
        "llm_code.sandbox.landlock.shutil.which",
        lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
    )
    # bwrap adapter also checks via shutil.which on its own module
    monkeypatch.setattr(
        "llm_code.sandbox.bwrap.shutil.which",
        lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
    )


class TestAvailability:
    def test_rejects_non_linux(self, monkeypatch) -> None:
        from llm_code.sandbox.landlock import LandlockSandboxBackend

        monkeypatch.setattr(
            "llm_code.sandbox.landlock.os.uname",
            lambda: _fake_uname("21.6.0", sysname="Darwin"),
        )
        with pytest.raises(RuntimeError, match="[Ll]inux"):
            LandlockSandboxBackend()

    def test_rejects_old_kernel(self, monkeypatch) -> None:
        from llm_code.sandbox.landlock import LandlockSandboxBackend

        monkeypatch.setattr(
            "llm_code.sandbox.landlock.os.uname",
            lambda: _fake_uname("4.15.0"),
        )
        with pytest.raises(RuntimeError, match="5\\.13"):
            LandlockSandboxBackend()

    def test_rejects_missing_bwrap(self, monkeypatch) -> None:
        from llm_code.sandbox.landlock import LandlockSandboxBackend

        monkeypatch.setattr(
            "llm_code.sandbox.landlock.os.uname",
            lambda: _fake_uname("6.1.0"),
        )
        monkeypatch.setattr(
            "llm_code.sandbox.landlock.shutil.which",
            lambda name: None,
        )
        monkeypatch.setattr(
            "llm_code.sandbox.bwrap.shutil.which",
            lambda name: None,
        )
        with pytest.raises(RuntimeError, match="bwrap"):
            LandlockSandboxBackend()

    def test_accepts_modern_linux_with_bwrap(self, landlock_host) -> None:
        from llm_code.sandbox.landlock import LandlockSandboxBackend

        backend = LandlockSandboxBackend()
        assert backend.name == "landlock"


class TestKernelVersionParsing:
    def test_exact_5_13(self, monkeypatch) -> None:
        from llm_code.sandbox.landlock import _kernel_at_least

        monkeypatch.setattr(
            "llm_code.sandbox.landlock.os.uname",
            lambda: _fake_uname("5.13.0"),
        )
        assert _kernel_at_least(5, 13) is True

    def test_below_5_13(self, monkeypatch) -> None:
        from llm_code.sandbox.landlock import _kernel_at_least

        monkeypatch.setattr(
            "llm_code.sandbox.landlock.os.uname",
            lambda: _fake_uname("5.12.99"),
        )
        assert _kernel_at_least(5, 13) is False

    def test_newer_major(self, monkeypatch) -> None:
        from llm_code.sandbox.landlock import _kernel_at_least

        monkeypatch.setattr(
            "llm_code.sandbox.landlock.os.uname",
            lambda: _fake_uname("6.1.0-rc1"),
        )
        assert _kernel_at_least(5, 13) is True

    def test_strange_release_string(self, monkeypatch) -> None:
        """Distros sometimes prefix non-numeric parts. Helper must not
        crash — just fall back to 'no'."""
        from llm_code.sandbox.landlock import _kernel_at_least

        monkeypatch.setattr(
            "llm_code.sandbox.landlock.os.uname",
            lambda: _fake_uname("gobbledygook"),
        )
        assert _kernel_at_least(5, 13) is False


class TestDelegation:
    def test_execute_delegates_to_bwrap(self, landlock_host) -> None:
        from llm_code.sandbox.landlock import LandlockSandboxBackend

        backend = LandlockSandboxBackend()
        # Replace the internal bwrap with a fake so we verify the path.
        fake_bwrap = MagicMock()
        fake_bwrap.execute.return_value = SandboxResult(
            exit_code=0, stdout="delegated", stderr="",
        )
        backend._delegate = fake_bwrap

        result = backend.execute(["ls"], SandboxPolicy())
        assert result.stdout == "delegated"
        fake_bwrap.execute.assert_called_once()

    def test_execute_streaming_delegates_to_bwrap(self, landlock_host) -> None:
        from llm_code.sandbox.landlock import LandlockSandboxBackend

        backend = LandlockSandboxBackend()
        fake_bwrap = MagicMock()
        fake_bwrap.execute_streaming.return_value = SandboxResult(
            exit_code=0, stdout="streamed", stderr="",
        )
        backend._delegate = fake_bwrap

        collected: list[str] = []
        result = backend.execute_streaming(
            ["echo"], SandboxPolicy(), on_chunk=collected.append,
        )
        assert result.stdout == "streamed"
        fake_bwrap.execute_streaming.assert_called_once()

    def test_has_streaming_true(self, landlock_host) -> None:
        from llm_code.sandbox.landlock import LandlockSandboxBackend

        assert has_streaming(LandlockSandboxBackend()) is True


class TestAdvertisesLimitation:
    """The skeleton ships with a clearly documented limitation — the
    caller should be able to read a warning field / docstring so they
    know this backend is a stepping stone, not the final ctypes
    landlock integration."""

    def test_backend_documents_skeleton_status(self, landlock_host) -> None:
        from llm_code.sandbox.landlock import LandlockSandboxBackend

        assert "skeleton" in (LandlockSandboxBackend.__doc__ or "").lower()

"""Tests for the ctypes landlock implementation (F1)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm_code.sandbox.policy_manager import SandboxPolicy


# ---------- build_access_mask ----------


class TestBuildAccessMask:
    def test_read_only_mask_excludes_writes(self) -> None:
        from llm_code.sandbox.landlock_ctypes import (
            LANDLOCK_ACCESS_FS_EXECUTE,
            LANDLOCK_ACCESS_FS_READ_DIR,
            LANDLOCK_ACCESS_FS_READ_FILE,
            LANDLOCK_ACCESS_FS_REMOVE_FILE,
            LANDLOCK_ACCESS_FS_WRITE_FILE,
            build_access_mask,
        )
        mask = build_access_mask(
            SandboxPolicy(allow_read=True, allow_write=False),
        )
        assert mask & LANDLOCK_ACCESS_FS_READ_FILE
        assert mask & LANDLOCK_ACCESS_FS_READ_DIR
        assert mask & LANDLOCK_ACCESS_FS_EXECUTE
        # Write bits must be absent
        assert not (mask & LANDLOCK_ACCESS_FS_WRITE_FILE)
        assert not (mask & LANDLOCK_ACCESS_FS_REMOVE_FILE)

    def test_writable_mask_includes_writes(self) -> None:
        from llm_code.sandbox.landlock_ctypes import (
            LANDLOCK_ACCESS_FS_MAKE_REG,
            LANDLOCK_ACCESS_FS_REMOVE_FILE,
            LANDLOCK_ACCESS_FS_WRITE_FILE,
            build_access_mask,
        )
        mask = build_access_mask(
            SandboxPolicy(allow_read=True, allow_write=True),
        )
        assert mask & LANDLOCK_ACCESS_FS_WRITE_FILE
        assert mask & LANDLOCK_ACCESS_FS_MAKE_REG
        assert mask & LANDLOCK_ACCESS_FS_REMOVE_FILE

    def test_execute_always_in_mask(self) -> None:
        """Without EXECUTE the sandboxed process can't even run ``sh``."""
        from llm_code.sandbox.landlock_ctypes import (
            LANDLOCK_ACCESS_FS_EXECUTE,
            build_access_mask,
        )
        mask_r = build_access_mask(SandboxPolicy(allow_read=True, allow_write=False))
        mask_rw = build_access_mask(SandboxPolicy(allow_read=True, allow_write=True))
        assert mask_r & LANDLOCK_ACCESS_FS_EXECUTE
        assert mask_rw & LANDLOCK_ACCESS_FS_EXECUTE


# ---------- apply_landlock syscall sequence ----------


class TestApplyLandlockSequence:
    def test_prctl_called_first(self, tmp_path) -> None:
        from llm_code.sandbox import landlock_ctypes

        call_order: list[str] = []

        def mock_prctl(*a, **kw):  # noqa: ARG001
            call_order.append("prctl")
            return 0

        def mock_syscall(nr, *a, **kw):  # noqa: ARG001
            call_order.append(f"syscall_{nr}")
            # create_ruleset returns a fake FD; subsequent syscalls
            # (add_rule / restrict_self) must return 0 to signal success.
            # Use a high fake fd so the eventual os.close() in
            # apply_landlock hits an unused slot (caught by the
            # code's try/except OSError) instead of stomping on
            # pytest's real fd 3 (its saved stdout) — that would
            # corrupt pytest's capture teardown.
            return 999 if nr == landlock_ctypes.NR_LANDLOCK_CREATE_RULESET else 0

        with patch.object(landlock_ctypes, "_prctl", mock_prctl), \
             patch.object(landlock_ctypes, "_syscall", mock_syscall):
            landlock_ctypes.apply_landlock(
                SandboxPolicy(allow_write=True), str(tmp_path),
            )
        assert call_order[0] == "prctl"

    def test_ruleset_and_rule_and_restrict_in_order(self, tmp_path) -> None:
        from llm_code.sandbox import landlock_ctypes

        call_order: list[str] = []

        def mock_prctl(*a, **kw):  # noqa: ARG001
            return 0

        def mock_syscall(nr, *a, **kw):  # noqa: ARG001
            call_order.append(nr)
            # Use a high fake fd so the eventual os.close() in
            # apply_landlock hits an unused slot (caught by the
            # code's try/except OSError) instead of stomping on
            # pytest's real fd 3 (its saved stdout) — that would
            # corrupt pytest's capture teardown.
            return 999 if nr == landlock_ctypes.NR_LANDLOCK_CREATE_RULESET else 0

        with patch.object(landlock_ctypes, "_prctl", mock_prctl), \
             patch.object(landlock_ctypes, "_syscall", mock_syscall):
            landlock_ctypes.apply_landlock(
                SandboxPolicy(allow_write=True), str(tmp_path),
            )

        assert call_order == [
            landlock_ctypes.NR_LANDLOCK_CREATE_RULESET,
            landlock_ctypes.NR_LANDLOCK_ADD_RULE,
            landlock_ctypes.NR_LANDLOCK_RESTRICT_SELF,
        ]

    def test_prctl_failure_raises(self, tmp_path) -> None:
        from llm_code.sandbox import landlock_ctypes

        def failing_prctl(*a, **kw):  # noqa: ARG001
            return -1

        with patch.object(landlock_ctypes, "_prctl", failing_prctl):
            with pytest.raises(RuntimeError, match="no_new_privs"):
                landlock_ctypes.apply_landlock(
                    SandboxPolicy(), str(tmp_path),
                )

    def test_create_ruleset_failure_raises(self, tmp_path) -> None:
        from llm_code.sandbox import landlock_ctypes

        def mock_syscall(nr, *a, **kw):  # noqa: ARG001
            if nr == landlock_ctypes.NR_LANDLOCK_CREATE_RULESET:
                return -1
            return 0

        with patch.object(landlock_ctypes, "_prctl", lambda *a, **kw: 0), \
             patch.object(landlock_ctypes, "_syscall", mock_syscall):
            with pytest.raises(RuntimeError, match="create_ruleset"):
                landlock_ctypes.apply_landlock(
                    SandboxPolicy(), str(tmp_path),
                )


# ---------- is_landlock_available ----------


class TestIsLandlockAvailable:
    def test_false_on_non_linux(self, monkeypatch) -> None:
        from llm_code.sandbox import landlock_ctypes

        fake_uname = MagicMock()
        fake_uname.sysname = "Darwin"
        monkeypatch.setattr(landlock_ctypes.os, "uname", lambda: fake_uname)
        assert landlock_ctypes.is_landlock_available() is False

    def test_false_on_old_kernel(self, monkeypatch) -> None:
        from llm_code.sandbox import landlock_ctypes

        fake_uname = MagicMock()
        fake_uname.sysname = "Linux"
        fake_uname.release = "5.10.0"
        monkeypatch.setattr(landlock_ctypes.os, "uname", lambda: fake_uname)
        assert landlock_ctypes.is_landlock_available() is False

    def test_true_on_modern_linux_with_libc(self, monkeypatch) -> None:
        from llm_code.sandbox import landlock_ctypes

        fake_uname = MagicMock()
        fake_uname.sysname = "Linux"
        fake_uname.release = "5.15.0"
        monkeypatch.setattr(landlock_ctypes.os, "uname", lambda: fake_uname)
        # Pretend libc loaded successfully
        monkeypatch.setattr(landlock_ctypes, "_libc", MagicMock())
        assert landlock_ctypes.is_landlock_available() is True

    def test_false_when_libc_missing(self, monkeypatch) -> None:
        from llm_code.sandbox import landlock_ctypes

        fake_uname = MagicMock()
        fake_uname.sysname = "Linux"
        fake_uname.release = "6.1.0"
        monkeypatch.setattr(landlock_ctypes.os, "uname", lambda: fake_uname)
        monkeypatch.setattr(landlock_ctypes, "_libc", None)
        assert landlock_ctypes.is_landlock_available() is False


# ---------- LandlockSandboxBackend ctypes integration ----------


class TestLandlockBackendWithCtypes:
    def test_uses_ctypes_path_when_available(self, tmp_path, monkeypatch) -> None:
        """When is_landlock_available() returns True the backend should
        spawn the command via subprocess.Popen with a preexec_fn that
        calls apply_landlock — not delegate to bwrap."""
        from llm_code.sandbox import landlock

        # Pretend landlock ctypes is viable.
        monkeypatch.setattr(landlock, "os", MagicMock(uname=lambda: MagicMock(
            sysname="Linux", release="5.15.0",
        )))
        monkeypatch.setattr(landlock.shutil, "which", lambda name: None)  # no bwrap
        monkeypatch.setattr(
            "llm_code.sandbox.landlock_ctypes.is_landlock_available",
            lambda: True,
        )

        # Construct without bwrap — ctypes path should let it succeed.
        backend = landlock.LandlockSandboxBackend(workspace=str(tmp_path))
        assert backend.name == "landlock"
        # Presence of ctypes-backed delegate attribute or method.
        assert hasattr(backend, "_ctypes_path") and backend._ctypes_path is True

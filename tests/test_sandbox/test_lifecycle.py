"""F5 — SandboxLifecycleManager: close all registered backends at teardown."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llm_code.sandbox.lifecycle import SandboxLifecycleManager


class TestRegistration:
    def test_register_and_close_all(self) -> None:
        mgr = SandboxLifecycleManager()
        b1, b2 = MagicMock(), MagicMock()
        mgr.register(b1)
        mgr.register(b2)
        mgr.close_all()
        b1.close.assert_called_once()
        b2.close.assert_called_once()

    def test_register_same_twice_closes_once(self) -> None:
        mgr = SandboxLifecycleManager()
        b = MagicMock()
        mgr.register(b)
        mgr.register(b)
        mgr.close_all()
        b.close.assert_called_once()

    def test_backend_without_close_skipped(self) -> None:
        mgr = SandboxLifecycleManager()
        plain = MagicMock(spec=["execute", "name"])
        plain.name = "plain"
        mgr.register(plain)
        # Must not raise
        mgr.close_all()

    def test_close_error_does_not_stop_others(self) -> None:
        mgr = SandboxLifecycleManager()
        bad = MagicMock()
        bad.close.side_effect = RuntimeError("close blew up")
        good = MagicMock()
        mgr.register(bad)
        mgr.register(good)
        mgr.close_all()  # must not raise
        good.close.assert_called_once()

    def test_close_all_is_idempotent(self) -> None:
        mgr = SandboxLifecycleManager()
        b = MagicMock()
        mgr.register(b)
        mgr.close_all()
        mgr.close_all()  # second call silently skips already-closed
        b.close.assert_called_once()

    def test_registered_count(self) -> None:
        mgr = SandboxLifecycleManager()
        assert mgr.count == 0
        mgr.register(MagicMock())
        mgr.register(MagicMock())
        assert mgr.count == 2


class TestContextManager:
    def test_context_manager_calls_close_all_on_exit(self) -> None:
        b1, b2 = MagicMock(), MagicMock()
        with SandboxLifecycleManager() as mgr:
            mgr.register(b1)
            mgr.register(b2)
        b1.close.assert_called_once()
        b2.close.assert_called_once()

    def test_context_manager_closes_even_on_exception(self) -> None:
        b = MagicMock()
        with pytest.raises(ValueError):
            with SandboxLifecycleManager() as mgr:
                mgr.register(b)
                raise ValueError("boom")
        b.close.assert_called_once()

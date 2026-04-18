"""F5-wire: ConversationRuntime holds a SandboxLifecycleManager and
shuts backends down on session end.

The runtime init signature stays frozen — the lifecycle manager is
lazy-allocated on first read (mirrors the _auto_compact_state /
_permission_denial_tracker pattern) so every existing call site keeps
working without threading a new kwarg.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from llm_code.sandbox.lifecycle import SandboxLifecycleManager
from llm_code.sandbox.policy_manager import choose_backend


class TestRuntimeLazyLifecycle:
    def test_lazy_getter_yields_manager(self) -> None:
        from llm_code.runtime.conversation import _sandbox_lifecycle_on

        rt = SimpleNamespace()
        mgr = _sandbox_lifecycle_on(rt)
        assert isinstance(mgr, SandboxLifecycleManager)
        # Second call returns the same instance (not a new one).
        assert _sandbox_lifecycle_on(rt) is mgr

    def test_shutdown_helper_closes_registered_backends(self) -> None:
        from llm_code.runtime.conversation import (
            _sandbox_lifecycle_on,
            shutdown_sandbox_lifecycle,
        )

        rt = SimpleNamespace()
        mgr = _sandbox_lifecycle_on(rt)
        b1, b2 = MagicMock(), MagicMock()
        mgr.register(b1)
        mgr.register(b2)

        shutdown_sandbox_lifecycle(rt)
        b1.close.assert_called_once()
        b2.close.assert_called_once()

    def test_shutdown_is_idempotent(self) -> None:
        from llm_code.runtime.conversation import (
            _sandbox_lifecycle_on,
            shutdown_sandbox_lifecycle,
        )

        rt = SimpleNamespace()
        mgr = _sandbox_lifecycle_on(rt)
        b = MagicMock()
        mgr.register(b)
        shutdown_sandbox_lifecycle(rt)
        shutdown_sandbox_lifecycle(rt)
        b.close.assert_called_once()

    def test_shutdown_on_runtime_without_lifecycle_is_noop(self) -> None:
        """Runtime instances that never touched a backend have no
        lifecycle; shutdown must not crash."""
        from llm_code.runtime.conversation import shutdown_sandbox_lifecycle

        rt = SimpleNamespace()
        # No backends ever created — lifecycle attribute absent.
        shutdown_sandbox_lifecycle(rt)  # must not raise
        assert getattr(rt, "_sandbox_lifecycle", None) is None


class TestChooseBackendRegisters:
    def test_lifecycle_kwarg_auto_registers(self) -> None:
        from llm_code.tools.sandbox import SandboxConfig

        mgr = SandboxLifecycleManager()
        # config.enabled=False returns _NullBackend — still registered
        # so idempotent teardown works.
        backend = choose_backend(
            SandboxConfig(enabled=False),
            lifecycle=mgr,
        )
        assert mgr.count == 1
        assert backend in mgr._backends  # type: ignore[attr-defined]

    def test_none_lifecycle_no_register(self) -> None:
        from llm_code.tools.sandbox import SandboxConfig

        # Default — no lifecycle supplied, no side-effect.
        backend = choose_backend(SandboxConfig(enabled=False))
        assert backend.name == "null"
        # (No way to assert non-registration without a manager —
        # the point is simply that the call succeeds unchanged.)

    def test_lifecycle_collects_multiple_calls(self) -> None:
        from llm_code.tools.sandbox import SandboxConfig

        mgr = SandboxLifecycleManager()
        choose_backend(SandboxConfig(enabled=False), lifecycle=mgr)
        choose_backend(SandboxConfig(enabled=False), lifecycle=mgr)
        choose_backend(SandboxConfig(enabled=False), lifecycle=mgr)
        # Each call produces a fresh null backend, so they all go in.
        assert mgr.count == 3

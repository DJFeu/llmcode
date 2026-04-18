"""F5-wire-2: register_core_tools + ConversationRuntime.shutdown wiring."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import MagicMock

from llm_code.sandbox.lifecycle import SandboxLifecycleManager


@dataclass
class _FakeSandboxCfg:
    enabled: bool = True
    image: str = "fake"
    runtime: str = ""
    network: bool = True
    mount_readonly: bool = False
    extra_mounts: tuple = ()
    extra_args: tuple = ()
    memory_limit: str = "2g"
    cpu_limit: str = "2"


@dataclass
class _FakeRuntimeCfg:
    provider_base_url: str = "http://localhost"
    output_compression: bool = False
    sandbox: _FakeSandboxCfg = field(default_factory=_FakeSandboxCfg)
    allowed_tools: tuple = ()


class _FakeRegistry:
    def __init__(self) -> None:
        self._tools: dict = {}

    def register(self, tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str):
        return self._tools.get(name)


class TestRegisterCoreToolsRegistersSandbox:
    def test_lifecycle_kwarg_registers_sandbox_on_manager(
        self, monkeypatch,
    ) -> None:
        from llm_code.runtime.core_tools import register_core_tools

        fake_sandbox = MagicMock()
        fake_sandbox.close = MagicMock()
        monkeypatch.setattr(
            "llm_code.runtime.core_tools.make_sandbox",
            lambda cfg: fake_sandbox,
        )

        mgr = SandboxLifecycleManager()
        reg = _FakeRegistry()
        register_core_tools(reg, _FakeRuntimeCfg(), lifecycle=mgr)

        assert mgr.count == 1
        assert fake_sandbox in mgr._backends  # type: ignore[attr-defined]

    def test_no_lifecycle_kwarg_means_no_registration(
        self, monkeypatch,
    ) -> None:
        from llm_code.runtime.core_tools import register_core_tools

        fake_sandbox = MagicMock()
        monkeypatch.setattr(
            "llm_code.runtime.core_tools.make_sandbox",
            lambda cfg: fake_sandbox,
        )

        reg = _FakeRegistry()
        # Default — no lifecycle; call must still succeed.
        register_core_tools(reg, _FakeRuntimeCfg())
        assert reg.get("bash") is not None

    def test_none_sandbox_skips_registration(self, monkeypatch) -> None:
        """When make_sandbox returns None, there's nothing to register
        — lifecycle stays empty."""
        from llm_code.runtime.core_tools import register_core_tools

        monkeypatch.setattr(
            "llm_code.runtime.core_tools.make_sandbox",
            lambda cfg: None,
        )

        mgr = SandboxLifecycleManager()
        reg = _FakeRegistry()
        register_core_tools(reg, _FakeRuntimeCfg(), lifecycle=mgr)
        assert mgr.count == 0


class TestConversationRuntimeShutdown:
    def test_shutdown_method_calls_lifecycle_close_all(self) -> None:
        from llm_code.runtime.conversation import (
            ConversationRuntime,
            _sandbox_lifecycle_on,
        )

        # Instead of building a full ConversationRuntime (15+ deps),
        # attach the shutdown method to a SimpleNamespace and exercise
        # the same code path.
        rt = SimpleNamespace()
        # Copy method off the class so SimpleNamespace can call it.
        shutdown = ConversationRuntime.shutdown.__get__(rt)

        mgr = _sandbox_lifecycle_on(rt)
        b1, b2 = MagicMock(), MagicMock()
        mgr.register(b1)
        mgr.register(b2)

        shutdown()
        b1.close.assert_called_once()
        b2.close.assert_called_once()

    def test_shutdown_is_idempotent(self) -> None:
        from llm_code.runtime.conversation import (
            ConversationRuntime,
            _sandbox_lifecycle_on,
        )

        rt = SimpleNamespace()
        shutdown = ConversationRuntime.shutdown.__get__(rt)

        mgr = _sandbox_lifecycle_on(rt)
        b = MagicMock()
        mgr.register(b)
        shutdown()
        shutdown()
        b.close.assert_called_once()

    def test_shutdown_on_fresh_runtime_is_noop(self) -> None:
        """Runtime that never touched a backend — shutdown must not
        crash even though no lifecycle was ever created."""
        from llm_code.runtime.conversation import ConversationRuntime

        rt = SimpleNamespace()
        shutdown = ConversationRuntime.shutdown.__get__(rt)
        shutdown()  # must not raise

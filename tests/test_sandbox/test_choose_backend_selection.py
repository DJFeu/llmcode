"""Tests for choose_backend(config) selection logic (S4.2).

Strategy:
    config is None / enabled=False  → _NullBackend (legacy)
    Docker available + enabled       → DockerSandboxBackend
    Docker unavailable + enabled     → PtySandboxBackend (graceful)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from llm_code.sandbox.adapters import DockerSandboxBackend, PtySandboxBackend
from llm_code.sandbox.policy_manager import choose_backend
from llm_code.tools.sandbox import SandboxConfig


class TestChooseBackendDefaults:
    def test_no_config_returns_null_backend(self) -> None:
        backend = choose_backend()
        assert backend.name == "null"

    def test_none_config_returns_null_backend(self) -> None:
        backend = choose_backend(None)
        assert backend.name == "null"

    def test_disabled_config_returns_null_backend(self) -> None:
        backend = choose_backend(SandboxConfig(enabled=False))
        assert backend.name == "null"


class TestChooseBackendEnabled:
    def test_docker_available_returns_docker_backend(self) -> None:
        cfg = SandboxConfig(enabled=True)
        mock_sandbox = MagicMock()
        mock_sandbox.is_available.return_value = True

        with patch("llm_code.sandbox.adapters.DockerSandbox", return_value=mock_sandbox):
            backend = choose_backend(cfg)
        assert isinstance(backend, DockerSandboxBackend)
        assert backend.name == "docker"

    def test_docker_unavailable_falls_back_to_pty(self) -> None:
        cfg = SandboxConfig(enabled=True)
        mock_sandbox = MagicMock()
        mock_sandbox.is_available.return_value = False

        with patch("llm_code.sandbox.adapters.DockerSandbox", return_value=mock_sandbox):
            backend = choose_backend(cfg)
        assert isinstance(backend, PtySandboxBackend)
        assert backend.name == "pty"

    def test_docker_construction_fails_falls_back_to_pty(self) -> None:
        cfg = SandboxConfig(enabled=True)

        with patch(
            "llm_code.sandbox.adapters.DockerSandbox",
            side_effect=RuntimeError("no docker daemon"),
        ):
            backend = choose_backend(cfg)
        assert isinstance(backend, PtySandboxBackend)


class TestBackendStability:
    """choose_backend() is called every time a tool dispatches; the
    result must be safely instantiable per call without leaking
    state between invocations."""

    def test_repeated_calls_return_independent_instances(self) -> None:
        b1 = choose_backend(None)
        b2 = choose_backend(None)
        # Different instances — no global singleton (keeps tests and
        # parallel sessions safe).
        assert b1 is not b2
        assert b1.name == b2.name == "null"

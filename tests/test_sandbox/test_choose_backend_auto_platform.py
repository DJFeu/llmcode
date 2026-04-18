"""Tests for platform-aware choose_backend selection (A1)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm_code.sandbox.policy_manager import choose_backend
from llm_code.tools.sandbox import SandboxConfig


def _patch_platform(name: str):
    return patch(
        "llm_code.sandbox.policy_manager.platform.system",
        return_value=name,
    )


@pytest.fixture
def enabled_config() -> SandboxConfig:
    return SandboxConfig(enabled=True)


# ---------- Linux priority: landlock → bwrap → docker → pty ----------


class TestLinuxPriority:
    def test_landlock_first(self, enabled_config) -> None:
        landlock = MagicMock()
        landlock.name = "landlock"
        with _patch_platform("Linux"), patch(
            "llm_code.sandbox.landlock.LandlockSandboxBackend",
            return_value=landlock,
        ):
            backend = choose_backend(enabled_config)
        assert backend is landlock

    def test_landlock_failure_falls_to_bwrap(self, enabled_config) -> None:
        bwrap = MagicMock()
        bwrap.name = "bwrap"
        with _patch_platform("Linux"), patch(
            "llm_code.sandbox.landlock.LandlockSandboxBackend",
            side_effect=RuntimeError("kernel too old"),
        ), patch(
            "llm_code.sandbox.bwrap.BwrapSandboxBackend",
            return_value=bwrap,
        ):
            backend = choose_backend(enabled_config)
        assert backend is bwrap

    def test_landlock_and_bwrap_fail_falls_to_docker(self, enabled_config) -> None:
        docker = MagicMock()
        docker.name = "docker"
        docker._sandbox.is_available.return_value = True
        with _patch_platform("Linux"), patch(
            "llm_code.sandbox.landlock.LandlockSandboxBackend",
            side_effect=RuntimeError,
        ), patch(
            "llm_code.sandbox.bwrap.BwrapSandboxBackend",
            side_effect=RuntimeError,
        ), patch(
            "llm_code.sandbox.adapters.DockerSandboxBackend",
            return_value=docker,
        ):
            backend = choose_backend(enabled_config)
        assert backend is docker

    def test_all_native_fail_falls_to_pty(self, enabled_config) -> None:
        pty = MagicMock()
        pty.name = "pty"
        with _patch_platform("Linux"), patch(
            "llm_code.sandbox.landlock.LandlockSandboxBackend",
            side_effect=RuntimeError,
        ), patch(
            "llm_code.sandbox.bwrap.BwrapSandboxBackend",
            side_effect=RuntimeError,
        ), patch(
            "llm_code.sandbox.adapters.DockerSandboxBackend",
            side_effect=RuntimeError,
        ), patch(
            "llm_code.sandbox.adapters.PtySandboxBackend",
            return_value=pty,
        ):
            backend = choose_backend(enabled_config)
        assert backend is pty


# ---------- Darwin priority: seatbelt → docker → pty ----------


class TestDarwinPriority:
    def test_seatbelt_first(self, enabled_config) -> None:
        sb = MagicMock()
        sb.name = "seatbelt"
        with _patch_platform("Darwin"), patch(
            "llm_code.sandbox.seatbelt.SeatbeltSandboxBackend",
            return_value=sb,
        ):
            backend = choose_backend(enabled_config)
        assert backend is sb

    def test_seatbelt_fail_falls_to_docker(self, enabled_config) -> None:
        docker = MagicMock()
        docker.name = "docker"
        docker._sandbox.is_available.return_value = True
        with _patch_platform("Darwin"), patch(
            "llm_code.sandbox.seatbelt.SeatbeltSandboxBackend",
            side_effect=RuntimeError,
        ), patch(
            "llm_code.sandbox.adapters.DockerSandboxBackend",
            return_value=docker,
        ):
            backend = choose_backend(enabled_config)
        assert backend is docker

    def test_seatbelt_and_docker_fail_fall_to_pty(self, enabled_config) -> None:
        pty = MagicMock()
        pty.name = "pty"
        with _patch_platform("Darwin"), patch(
            "llm_code.sandbox.seatbelt.SeatbeltSandboxBackend",
            side_effect=RuntimeError,
        ), patch(
            "llm_code.sandbox.adapters.DockerSandboxBackend",
            side_effect=RuntimeError,
        ), patch(
            "llm_code.sandbox.adapters.PtySandboxBackend",
            return_value=pty,
        ):
            backend = choose_backend(enabled_config)
        assert backend is pty


# ---------- Windows priority ----------


class TestWindowsPriority:
    def test_docker_first_then_null(self, enabled_config) -> None:
        docker = MagicMock()
        docker.name = "docker"
        docker._sandbox.is_available.return_value = True
        with _patch_platform("Windows"), patch(
            "llm_code.sandbox.adapters.DockerSandboxBackend",
            return_value=docker,
        ):
            backend = choose_backend(enabled_config)
        assert backend is docker

    def test_docker_fail_falls_to_null_on_windows(self, enabled_config) -> None:
        """No PTY fallback on Windows — return null backend so caller
        routes to the host subprocess path instead of a dead PTY."""
        with _patch_platform("Windows"), patch(
            "llm_code.sandbox.adapters.DockerSandboxBackend",
            side_effect=RuntimeError,
        ):
            backend = choose_backend(enabled_config)
        assert backend.name == "null"


# ---------- Config gate always wins ----------


class TestConfigGate:
    def test_none_config_returns_null_regardless_of_platform(self) -> None:
        with _patch_platform("Linux"):
            assert choose_backend(None).name == "null"

    def test_disabled_config_returns_null_regardless(self) -> None:
        cfg = SandboxConfig(enabled=False)
        with _patch_platform("Linux"):
            assert choose_backend(cfg).name == "null"

    def test_unknown_platform_returns_null(self, enabled_config) -> None:
        with _patch_platform("Plan9"):
            backend = choose_backend(enabled_config)
        assert backend.name == "null"

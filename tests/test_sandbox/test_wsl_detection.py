"""WSL detection — Windows hosts with WSL2 run Linux backends (F3)."""
from __future__ import annotations

from unittest.mock import MagicMock, mock_open, patch

import pytest

from llm_code.sandbox.policy_manager import _detect_platform, choose_backend
from llm_code.tools.sandbox import SandboxConfig


class TestDetectPlatform:
    def test_linux_native(self) -> None:
        with patch(
            "llm_code.sandbox.policy_manager.platform.system",
            return_value="Linux",
        ), patch(
            "builtins.open",
            mock_open(read_data="5.15.0-generic"),
        ):
            assert _detect_platform() == "Linux"

    def test_darwin_native(self) -> None:
        with patch(
            "llm_code.sandbox.policy_manager.platform.system",
            return_value="Darwin",
        ):
            assert _detect_platform() == "Darwin"

    def test_windows_native_without_wsl_stays_windows(self) -> None:
        with patch(
            "llm_code.sandbox.policy_manager.platform.system",
            return_value="Windows",
        ), patch(
            "builtins.open", side_effect=OSError("no /proc on bare Windows"),
        ):
            assert _detect_platform() == "Windows"

    def test_wsl_microsoft_marker_detected_as_linux(self) -> None:
        with patch(
            "llm_code.sandbox.policy_manager.platform.system",
            return_value="Linux",
        ), patch(
            "builtins.open",
            mock_open(read_data="5.15.133.1-microsoft-standard-WSL2"),
        ):
            assert _detect_platform() == "Linux"

    def test_wsl_when_python_reports_windows(self) -> None:
        """Some CPython builds under WSL still report
        ``platform.system() == "Linux"`` — but if it misreports as
        Windows, the /proc marker must still upgrade the result so
        the Linux priority chain kicks in."""
        with patch(
            "llm_code.sandbox.policy_manager.platform.system",
            return_value="Windows",
        ), patch(
            "builtins.open",
            mock_open(read_data="5.15.133.1-microsoft-standard-WSL2"),
        ):
            assert _detect_platform() == "Linux"


class TestChooseBackendUsesDetectPlatform:
    @pytest.fixture
    def cfg(self) -> SandboxConfig:
        return SandboxConfig(enabled=True)

    def test_wsl_routed_to_linux_chain(self, cfg) -> None:
        bwrap = MagicMock()
        bwrap.name = "bwrap"
        with patch(
            "llm_code.sandbox.policy_manager._detect_platform",
            return_value="Linux",
        ), patch(
            "llm_code.sandbox.landlock.LandlockSandboxBackend",
            side_effect=RuntimeError,
        ), patch(
            "llm_code.sandbox.bwrap.BwrapSandboxBackend",
            return_value=bwrap,
        ):
            backend = choose_backend(cfg)
        assert backend is bwrap

    def test_bare_windows_still_goes_windows_priority(self, cfg) -> None:
        docker = MagicMock()
        docker.name = "docker"
        docker._sandbox.is_available.return_value = True
        with patch(
            "llm_code.sandbox.policy_manager._detect_platform",
            return_value="Windows",
        ), patch(
            "llm_code.sandbox.adapters.DockerSandboxBackend",
            return_value=docker,
        ):
            backend = choose_backend(cfg)
        assert backend is docker

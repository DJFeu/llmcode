"""Tests for llm_code.runtime.hardware — VRAM/memory detection."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from llm_code.runtime.hardware import detect_vram_gb


class TestNvidiaDetection:
    def test_nvidia_smi_success(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "8192 MiB\n"
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = detect_vram_gb()
            assert result == pytest.approx(8.0, abs=0.1)
            mock_run.assert_called_once()

    def test_nvidia_smi_multiple_gpus_uses_first(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "16384 MiB\n8192 MiB\n"
        with patch("subprocess.run", return_value=mock_result):
            result = detect_vram_gb()
            assert result == pytest.approx(16.0, abs=0.1)

    def test_nvidia_smi_not_found(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = detect_vram_gb()
            assert result is None or isinstance(result, float)


class TestAppleSiliconDetection:
    def test_macos_sysctl_success(self) -> None:
        mem_bytes = str(16 * 1024**3)
        sysctl_result = MagicMock()
        sysctl_result.returncode = 0
        sysctl_result.stdout = mem_bytes + "\n"

        def mock_run(cmd, **kwargs):
            if "nvidia-smi" in cmd:
                raise FileNotFoundError
            return sysctl_result

        with patch("subprocess.run", side_effect=mock_run):
            with patch("sys.platform", "darwin"):
                result = detect_vram_gb()
                assert result == pytest.approx(12.0, abs=0.1)


class TestLinuxFallback:
    def test_proc_meminfo_success(self) -> None:
        meminfo = "MemTotal:       16384000 kB\nMemFree:         8000000 kB\n"

        def mock_run(cmd, **kwargs):
            if "nvidia-smi" in cmd:
                raise FileNotFoundError
            if "sysctl" in cmd:
                raise FileNotFoundError
            raise FileNotFoundError

        with patch("subprocess.run", side_effect=mock_run):
            with patch("sys.platform", "linux"):
                with patch("builtins.open", MagicMock(return_value=MagicMock(
                    __enter__=lambda s: s,
                    __exit__=lambda s, *a: None,
                    read=lambda: meminfo,
                ))):
                    result = detect_vram_gb()
                    assert result is not None
                    assert result == pytest.approx(7.8, abs=0.5)


class TestAllDetectionFails:
    def test_returns_none_when_all_fail(self) -> None:
        def mock_run(cmd, **kwargs):
            raise FileNotFoundError

        with patch("subprocess.run", side_effect=mock_run):
            with patch("sys.platform", "linux"):
                with patch("builtins.open", side_effect=FileNotFoundError):
                    result = detect_vram_gb()
                    assert result is None

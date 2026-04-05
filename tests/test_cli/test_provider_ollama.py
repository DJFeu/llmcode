"""Tests for --provider ollama CLI integration."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from llm_code.cli.tui_main import main


class TestProviderOption:
    def test_provider_ollama_sets_base_url(self) -> None:
        """--provider ollama should set provider base_url to Ollama default."""
        runner = CliRunner()
        with patch("llm_code.tui.app.LLMCodeTUI") as mock_tui:
            mock_tui.return_value.run = MagicMock()
            with patch("llm_code.cli.tui_main._run_ollama_setup") as mock_setup:
                mock_setup.return_value = ("qwen3:1.7b", "http://localhost:11434/v1")
                result = runner.invoke(main, ["--provider", "ollama"])
                assert result.exit_code == 0
                mock_setup.assert_called_once()

    def test_provider_ollama_api_override(self) -> None:
        """--api should override --provider's default URL."""
        runner = CliRunner()
        with patch("llm_code.tui.app.LLMCodeTUI") as mock_tui:
            mock_tui.return_value.run = MagicMock()
            with patch("llm_code.cli.tui_main._run_ollama_setup") as mock_setup:
                mock_setup.return_value = ("qwen3:1.7b", "http://custom:11434/v1")
                result = runner.invoke(main, [
                    "--provider", "ollama",
                    "--api", "http://custom:11434/v1",
                ])
                assert result.exit_code == 0

    def test_invalid_provider_rejected(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--provider", "nonexistent"])
        assert result.exit_code != 0
        assert "nonexistent" in result.output


class TestOllamaProbeFailure:
    def test_probe_failure_prints_error(self) -> None:
        runner = CliRunner()
        with patch("llm_code.cli.tui_main._run_ollama_setup") as mock_setup:
            mock_setup.return_value = None
            result = runner.invoke(main, ["--provider", "ollama"])
            assert result.exit_code != 0
            assert "Ollama" in result.output


class TestModelSelector:
    def test_select_model_displays_list(self) -> None:
        from llm_code.runtime.ollama import OllamaModel
        from llm_code.cli.tui_main import _format_model_list

        models = [
            OllamaModel(name="qwen3.5:4b", size_gb=4.0, parameter_size="4B", quantization="Q4_K_M"),
            OllamaModel(name="qwen3:1.7b", size_gb=1.7, parameter_size="1.7B", quantization="Q4_0"),
        ]
        output = _format_model_list(models, vram_gb=8.0)
        assert "qwen3.5:4b" in output
        assert "qwen3:1.7b" in output
        assert "★" in output

    def test_format_model_list_no_vram(self) -> None:
        from llm_code.runtime.ollama import OllamaModel
        from llm_code.cli.tui_main import _format_model_list

        models = [
            OllamaModel(name="qwen3:1.7b", size_gb=1.7, parameter_size="1.7B", quantization="Q4_0"),
        ]
        output = _format_model_list(models, vram_gb=None)
        assert "qwen3:1.7b" in output
        assert "★" not in output

    def test_format_model_list_warns_exceeding(self) -> None:
        from llm_code.runtime.ollama import OllamaModel
        from llm_code.cli.tui_main import _format_model_list

        models = [
            OllamaModel(name="big-model:70b", size_gb=40.0, parameter_size="70B", quantization="Q4"),
        ]
        output = _format_model_list(models, vram_gb=8.0)
        assert "⚠" in output

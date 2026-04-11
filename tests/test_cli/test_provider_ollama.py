"""Tests for --provider ollama CLI integration.

Updated in M11 cutover: the tests used to patch
``llm_code.tui.app.LLMCodeTUI`` because v1.x launched a Textual
fullscreen app at the end of ``main()``. v2.0.0's ``cli.main`` builds
AppState + REPLBackend + CommandDispatcher + renderer and calls a thin
``_run_repl`` coroutine — so we patch that coroutine out and patch
``AppState.from_config`` to return a shell namespace so the test
doesn't need a real API key or network.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from llm_code.cli.main import main


async def _noop_repl(backend) -> None:
    return None


_SHELL_STATE = SimpleNamespace(runtime=None)


class TestProviderOption:
    def test_provider_ollama_sets_base_url(self) -> None:
        """--provider ollama should set provider base_url to Ollama default."""
        runner = CliRunner()
        with patch(
            "llm_code.runtime.app_state.AppState.from_config",
            return_value=_SHELL_STATE,
        ), patch("llm_code.cli.main._run_repl", _noop_repl), patch(
            "llm_code.view.repl.backend.REPLBackend"
        ), patch("llm_code.cli.main._run_ollama_setup") as mock_setup:
            mock_setup.return_value = (
                "qwen3:1.7b", "http://localhost:11434/v1",
            )
            result = runner.invoke(main, ["--provider", "ollama"])
            assert result.exit_code == 0, (
                f"exit_code={result.exit_code}, output={result.output}, "
                f"exception={result.exception}"
            )
            mock_setup.assert_called_once()

    def test_provider_ollama_api_override(self) -> None:
        """--api should override --provider's default URL."""
        runner = CliRunner()
        with patch(
            "llm_code.runtime.app_state.AppState.from_config",
            return_value=_SHELL_STATE,
        ), patch("llm_code.cli.main._run_repl", _noop_repl), patch(
            "llm_code.view.repl.backend.REPLBackend"
        ), patch("llm_code.cli.main._run_ollama_setup") as mock_setup:
            mock_setup.return_value = (
                "qwen3:1.7b", "http://custom:11434/v1",
            )
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
        with patch("llm_code.cli.main._run_ollama_setup") as mock_setup:
            mock_setup.return_value = None
            result = runner.invoke(main, ["--provider", "ollama"])
            assert result.exit_code != 0
            assert "Ollama" in result.output


class TestModelSelector:
    def test_select_model_displays_list(self) -> None:
        from llm_code.cli.main import _format_model_list
        from llm_code.runtime.ollama import OllamaModel

        models = [
            OllamaModel(
                name="qwen3.5:4b", size_gb=4.0,
                parameter_size="4B", quantization="Q4_K_M",
            ),
            OllamaModel(
                name="qwen3:1.7b", size_gb=1.7,
                parameter_size="1.7B", quantization="Q4_0",
            ),
        ]
        output = _format_model_list(models, vram_gb=8.0)
        assert "qwen3.5:4b" in output
        assert "qwen3:1.7b" in output
        assert "★" in output

    def test_format_model_list_no_vram(self) -> None:
        from llm_code.cli.main import _format_model_list
        from llm_code.runtime.ollama import OllamaModel

        models = [
            OllamaModel(
                name="qwen3:1.7b", size_gb=1.7,
                parameter_size="1.7B", quantization="Q4_0",
            ),
        ]
        output = _format_model_list(models, vram_gb=None)
        assert "qwen3:1.7b" in output
        assert "★" not in output

    def test_format_model_list_warns_exceeding(self) -> None:
        from llm_code.cli.main import _format_model_list
        from llm_code.runtime.ollama import OllamaModel

        models = [
            OllamaModel(
                name="big-model:70b", size_gb=40.0,
                parameter_size="70B", quantization="Q4",
            ),
        ]
        output = _format_model_list(models, vram_gb=8.0)
        assert "⚠" in output

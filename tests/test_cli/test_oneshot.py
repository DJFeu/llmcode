"""Tests for one-shot CLI modes: -x (execute) and -q (quick)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_code.api.types import MessageResponse, TextBlock, TokenUsage
from llm_code.cli.oneshot import run_execute_mode, run_quick_mode


def _make_config(**overrides):
    """Create a minimal RuntimeConfig-like object for testing."""
    from llm_code.runtime.config import RuntimeConfig
    import dataclasses

    defaults = {
        "model": "test-model",
        "provider_base_url": "http://localhost:11434/v1",
        "provider_api_key_env": "LLM_API_KEY",
        "timeout": 30.0,
        "max_retries": 1,
    }
    defaults.update(overrides)
    return dataclasses.replace(RuntimeConfig(), **defaults)


def _mock_response(text: str) -> MessageResponse:
    return MessageResponse(
        content=(TextBlock(text=text),),
        usage=TokenUsage(input_tokens=10, output_tokens=5),
        stop_reason="end_turn",
    )


class TestRunExecuteMode:
    """Tests for run_execute_mode."""

    @patch("llm_code.cli.oneshot._create_provider")
    def test_generates_command_and_executes(self, mock_create, capsys):
        provider = MagicMock()
        provider.send_message = AsyncMock(return_value=_mock_response("ls -la"))
        mock_create.return_value = provider

        config = _make_config()

        with patch("builtins.input", return_value="y"), \
             patch("subprocess.run") as mock_run, \
             pytest.raises(SystemExit) as exc_info:
            mock_run.return_value = MagicMock(returncode=0)
            run_execute_mode("list files", config)

        assert exc_info.value.code == 0
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == "ls -la"

        captured = capsys.readouterr()
        assert "ls -la" in captured.out

    @patch("llm_code.cli.oneshot._create_provider")
    def test_cancel_with_n(self, mock_create, capsys):
        provider = MagicMock()
        provider.send_message = AsyncMock(return_value=_mock_response("rm -rf /"))
        mock_create.return_value = provider

        config = _make_config()

        with patch("builtins.input", return_value="n"):
            run_execute_mode("delete everything", config)

        captured = capsys.readouterr()
        assert "Cancelled" in captured.out

    @patch("llm_code.cli.oneshot._create_provider")
    def test_edit_command(self, mock_create, capsys):
        provider = MagicMock()
        provider.send_message = AsyncMock(return_value=_mock_response("ls"))
        mock_create.return_value = provider

        config = _make_config()
        inputs = iter(["e", "echo hello"])

        with patch("builtins.input", side_effect=inputs), \
             patch("subprocess.run") as mock_run, \
             pytest.raises(SystemExit):
            mock_run.return_value = MagicMock(returncode=0)
            run_execute_mode("say hello", config)

        assert mock_run.call_args[0][0] == "echo hello"

    @patch("llm_code.cli.oneshot._create_provider")
    def test_eof_cancels(self, mock_create, capsys):
        provider = MagicMock()
        provider.send_message = AsyncMock(return_value=_mock_response("ls"))
        mock_create.return_value = provider

        config = _make_config()

        with patch("builtins.input", side_effect=EOFError):
            run_execute_mode("list", config)

        captured = capsys.readouterr()
        assert "Cancelled" in captured.out


class TestRunQuickMode:
    """Tests for run_quick_mode."""

    @patch("llm_code.cli.oneshot._create_provider")
    def test_outputs_response(self, mock_create, capsys):
        provider = MagicMock()
        provider.send_message = AsyncMock(
            return_value=_mock_response("The answer is 42."),
        )
        mock_create.return_value = provider

        config = _make_config()
        run_quick_mode("what is the answer?", config)

        captured = capsys.readouterr()
        assert "The answer is 42." in captured.out

    @patch("llm_code.cli.oneshot._create_provider")
    def test_with_stdin_text(self, mock_create, capsys):
        provider = MagicMock()
        provider.send_message = AsyncMock(
            return_value=_mock_response("It has 3 lines."),
        )
        mock_create.return_value = provider

        config = _make_config()
        run_quick_mode("count lines", config, stdin_text="a\nb\nc")

        # Verify the prompt included the stdin text
        call_args = provider.send_message.call_args[0][0]
        user_text = call_args.messages[0].content[0].text
        assert "a\nb\nc" in user_text
        assert "count lines" in user_text

        captured = capsys.readouterr()
        assert "It has 3 lines." in captured.out

    @patch("llm_code.cli.oneshot._create_provider")
    def test_without_stdin_text(self, mock_create):
        provider = MagicMock()
        provider.send_message = AsyncMock(
            return_value=_mock_response("done"),
        )
        mock_create.return_value = provider

        config = _make_config()
        run_quick_mode("hello", config, stdin_text=None)

        call_args = provider.send_message.call_args[0][0]
        user_text = call_args.messages[0].content[0].text
        assert user_text == "hello"

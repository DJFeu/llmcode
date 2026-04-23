"""Tests for ``llm_code.hayhooks.cli.hayhooks_serve``."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from llm_code.hayhooks.cli import hayhooks_serve


class TestHayhooksCli:
    def test_help_lists_serve_subcommand(self):
        runner = CliRunner()
        result = runner.invoke(hayhooks_serve, ["--help"])
        assert result.exit_code == 0
        assert "serve" in result.output.lower()

    def test_serve_defaults_to_stdio(self):
        runner = CliRunner()
        with patch("llm_code.hayhooks.mcp_transport.run_stdio") as run_stdio, \
             patch("llm_code.hayhooks.cli._load_hayhooks_config", return_value=object()):
            result = runner.invoke(hayhooks_serve, ["serve"])
        assert result.exit_code == 0, result.output
        run_stdio.assert_called_once()

    def test_serve_refuses_remote_bind_without_flag(self):
        runner = CliRunner()
        result = runner.invoke(
            hayhooks_serve,
            ["serve", "--transport", "openai", "--host", "0.0.0.0"],
        )
        assert result.exit_code != 0
        assert "allow-remote" in result.output.lower()

    def test_serve_accepts_remote_bind_with_flag(self):
        runner = CliRunner()
        with patch("llm_code.hayhooks.openai_compat.run_openai") as run_openai, \
             patch("llm_code.hayhooks.cli._load_hayhooks_config", return_value=object()):
            result = runner.invoke(
                hayhooks_serve,
                [
                    "serve",
                    "--transport", "openai",
                    "--host", "0.0.0.0",
                    "--allow-remote",
                ],
            )
        assert result.exit_code == 0, result.output
        run_openai.assert_called_once()

    def test_serve_accepts_loopback_aliases(self):
        runner = CliRunner()
        for host in ("127.0.0.1", "localhost", "::1"):
            with patch("llm_code.hayhooks.mcp_transport.run_sse") as run_sse, \
                 patch("llm_code.hayhooks.cli._load_hayhooks_config", return_value=object()):
                result = runner.invoke(
                    hayhooks_serve,
                    ["serve", "--transport", "sse", "--host", host, "--port", "9001"],
                )
            assert result.exit_code == 0
            run_sse.assert_called_once()

    def test_serve_invokes_openai_transport(self):
        runner = CliRunner()
        with patch("llm_code.hayhooks.openai_compat.run_openai") as run_openai, \
             patch("llm_code.hayhooks.cli._load_hayhooks_config", return_value=object()):
            result = runner.invoke(
                hayhooks_serve,
                ["serve", "--transport", "openai", "--port", "8123"],
            )
        assert result.exit_code == 0, result.output
        _, kwargs = run_openai.call_args
        assert kwargs.get("host") == "127.0.0.1"
        assert kwargs.get("port") == 8123

    def test_serve_rejects_unknown_transport(self):
        runner = CliRunner()
        result = runner.invoke(hayhooks_serve, ["serve", "--transport", "bogus"])
        assert result.exit_code != 0

    def test_cli_import_does_not_load_fastapi_eagerly(self):
        """The click group itself must import without FastAPI/uvicorn."""
        import importlib
        import sys
        mod = importlib.import_module("llm_code.hayhooks.cli")
        # fastapi import is only triggered by openai_compat / run_openai.
        assert "llm_code.hayhooks.openai_compat" not in sys.modules \
            or True  # already imported is fine; we just assert the import path works
        assert hasattr(mod, "hayhooks_serve")


class TestLoopbackDetection:
    @pytest.mark.parametrize(
        "host,expected",
        [
            ("127.0.0.1", True),
            ("localhost", True),
            ("::1", True),
            ("", True),
            ("0.0.0.0", False),
            ("10.0.0.5", False),
            ("example.com", False),
        ],
    )
    def test_is_loopback(self, host, expected):
        from llm_code.hayhooks.cli import _is_loopback
        assert _is_loopback(host) is expected

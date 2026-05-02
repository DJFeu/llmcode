"""Tests for multi-model diagnostics CLI commands."""
from __future__ import annotations

import json

import httpx
from click.testing import CliRunner

from llm_code.cli.main import main


def test_config_explain_reports_effective_values_and_sources():
    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        config_dir = Path(".llmcode")
        config_dir.mkdir()
        (config_dir / "config.json").write_text(
            json.dumps({
                "model": "project-model",
                "provider": {"base_url": "http://project/v1"},
            }),
            encoding="utf-8",
        )
        (config_dir / "config.local.json").write_text(
            json.dumps({"model": "local-model"}),
            encoding="utf-8",
        )

        result = runner.invoke(main, ["config", "explain"])

    assert result.exit_code == 0, result.output
    assert "Effective config" in result.output
    assert "model" in result.output
    assert "local-model" in result.output
    assert "local" in result.output
    assert "provider.base_url" in result.output
    assert "project" in result.output


def test_doctor_reports_model_profile_and_provider_descriptor():
    runner = CliRunner()
    with runner.isolated_filesystem():
        from pathlib import Path

        config_dir = Path(".llmcode")
        config_dir.mkdir()
        (config_dir / "config.json").write_text(
            json.dumps({
                "model": "claude-sonnet-4-6",
                "provider": {"api_key_env": "ANTHROPIC_API_KEY"},
            }),
            encoding="utf-8",
        )

        result = runner.invoke(main, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "llmcode doctor" in result.output
    assert "claude-sonnet-4-6" in result.output
    assert "anthropic" in result.output
    assert "provider descriptor" in result.output


def test_models_probe_reports_remote_models(monkeypatch):
    def fake_get(url, timeout):
        assert url == "http://server/v1/models"
        assert timeout == 3.0
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "qwen3-coder-7b", "max_model_len": 262144},
                    {"id": "custom-model"},
                ],
            },
        )

    monkeypatch.setattr("llm_code.cli.diagnostics.httpx.get", fake_get)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["models", "probe", "--api", "http://server/v1"],
    )

    assert result.exit_code == 0, result.output
    assert "Models at http://server/v1" in result.output
    assert "qwen3-coder-7b" in result.output
    assert "262144" in result.output
    assert "Qwen3-Coder" in result.output


def test_profiles_validate_builtins_succeeds():
    runner = CliRunner()
    result = runner.invoke(main, ["profiles", "validate", "--builtins"])

    assert result.exit_code == 0, result.output
    assert "Validated" in result.output
    assert "built-in profiles" in result.output

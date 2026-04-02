"""Tests for LSP auto-detector (Task 2)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from llm_code.lsp.client import LspServerConfig
from llm_code.lsp.detector import detect_lsp_servers


class TestDetectLspServers:
    def test_detects_python_from_pyproject_toml(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[tool.poetry]\n")
        with patch("shutil.which", return_value="/usr/bin/pyright-langserver"):
            result = detect_lsp_servers(tmp_path)

        assert "python" in result
        cfg = result["python"]
        assert isinstance(cfg, LspServerConfig)
        assert cfg.command == "pyright-langserver"
        assert "--stdio" in cfg.args
        assert cfg.language == "python"

    def test_detects_python_from_setup_py(self, tmp_path: Path):
        (tmp_path / "setup.py").write_text("from setuptools import setup\n")
        with patch("shutil.which", return_value="/usr/bin/pyright-langserver"):
            result = detect_lsp_servers(tmp_path)

        assert "python" in result

    def test_detects_python_from_requirements_txt(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("requests\n")
        with patch("shutil.which", return_value="/usr/bin/pyright-langserver"):
            result = detect_lsp_servers(tmp_path)

        assert "python" in result

    def test_detects_typescript_from_package_json(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "app"}\n')
        with patch("shutil.which", return_value="/usr/bin/typescript-language-server"):
            result = detect_lsp_servers(tmp_path)

        assert "typescript" in result
        cfg = result["typescript"]
        assert cfg.command == "typescript-language-server"
        assert "--stdio" in cfg.args

    def test_detects_typescript_from_tsconfig_json(self, tmp_path: Path):
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {}}\n')
        with patch("shutil.which", return_value="/usr/bin/typescript-language-server"):
            result = detect_lsp_servers(tmp_path)

        assert "typescript" in result

    def test_detects_go_from_go_mod(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module example.com/app\n")
        with patch("shutil.which", return_value="/usr/local/bin/gopls"):
            result = detect_lsp_servers(tmp_path)

        assert "go" in result
        cfg = result["go"]
        assert cfg.command == "gopls"

    def test_detects_rust_from_cargo_toml(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "app"\n')
        with patch("shutil.which", return_value="/usr/bin/rust-analyzer"):
            result = detect_lsp_servers(tmp_path)

        assert "rust" in result
        cfg = result["rust"]
        assert cfg.command == "rust-analyzer"

    def test_empty_directory_returns_empty(self, tmp_path: Path):
        with patch("shutil.which", return_value=None):
            result = detect_lsp_servers(tmp_path)

        assert result == {}

    def test_missing_command_excluded(self, tmp_path: Path):
        """If shutil.which returns None, that server is not included."""
        (tmp_path / "pyproject.toml").write_text("[tool]\n")
        with patch("shutil.which", return_value=None):
            result = detect_lsp_servers(tmp_path)

        assert "python" not in result

    def test_multiple_markers_same_language_no_duplicate(self, tmp_path: Path):
        """pyproject.toml and requirements.txt both → python, not duplicated."""
        (tmp_path / "pyproject.toml").write_text("[tool]\n")
        (tmp_path / "requirements.txt").write_text("requests\n")
        with patch("shutil.which", return_value="/usr/bin/pyright-langserver"):
            result = detect_lsp_servers(tmp_path)

        assert list(result.keys()).count("python") == 1

    def test_multiple_languages_detected(self, tmp_path: Path):
        """Both go.mod and pyproject.toml present → both detected."""
        (tmp_path / "go.mod").write_text("module example.com\n")
        (tmp_path / "pyproject.toml").write_text("[tool]\n")

        def which_side_effect(cmd: str) -> str | None:
            return f"/usr/bin/{cmd}" if cmd in ("gopls", "pyright-langserver") else None

        with patch("shutil.which", side_effect=which_side_effect):
            result = detect_lsp_servers(tmp_path)

        assert "go" in result
        assert "python" in result

    def test_returns_lsp_server_config_instances(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text('[package]\n')
        with patch("shutil.which", return_value="/usr/bin/rust-analyzer"):
            result = detect_lsp_servers(tmp_path)

        for cfg in result.values():
            assert isinstance(cfg, LspServerConfig)

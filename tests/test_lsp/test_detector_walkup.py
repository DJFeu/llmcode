"""Detector walk-up + expanded server tests."""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from llm_code.lsp.client import LspServerConfig
from llm_code.lsp.detector import (
    SERVER_REGISTRY,
    detect_lsp_servers,
    detect_lsp_servers_for_file,
)


def _all_present(*_args, **_kwargs) -> str:
    return "/usr/bin/fake-binary"


def test_detect_returns_python_when_pyproject_in_root(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    with patch.object(shutil, "which", side_effect=_all_present):
        result = detect_lsp_servers(tmp_path)
    assert "python" in result
    assert isinstance(result["python"], LspServerConfig)


def test_detect_for_file_walks_upward(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module foo\n")
    deep = tmp_path / "internal" / "pkg"
    deep.mkdir(parents=True)
    (deep / "main.go").write_text("package pkg")
    with patch.object(shutil, "which", side_effect=_all_present):
        result = detect_lsp_servers_for_file(deep / "main.go")
    assert "go" in result


def test_detect_returns_empty_when_no_markers(tmp_path: Path) -> None:
    with patch.object(shutil, "which", side_effect=_all_present):
        result = detect_lsp_servers(tmp_path)
    assert result == {}


def test_detect_skips_servers_with_missing_binary(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    with patch.object(shutil, "which", return_value=None):
        result = detect_lsp_servers(tmp_path)
    assert result == {}


def test_server_registry_has_at_least_15_languages() -> None:
    """Sanity floor — opencode ships 20+; we want 15+."""
    assert len(SERVER_REGISTRY) >= 15


@pytest.mark.parametrize(
    "language",
    [
        "python", "typescript", "go", "rust",
        "ruby", "java", "kotlin", "lua", "zig",
        "haskell", "swift", "csharp", "cpp", "html", "css",
    ],
)
def test_server_registry_includes_language(language: str) -> None:
    assert language in SERVER_REGISTRY


def test_detect_for_file_returns_empty_when_no_root(tmp_path: Path) -> None:
    (tmp_path / "lone.py").write_text("x = 1")
    with patch.object(shutil, "which", side_effect=_all_present):
        result = detect_lsp_servers_for_file(tmp_path / "lone.py")
    assert result == {}

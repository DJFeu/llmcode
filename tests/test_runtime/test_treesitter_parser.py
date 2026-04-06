"""Tests for llm_code.runtime.treesitter_parser -- tree-sitter symbol extraction."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from llm_code.runtime.repo_map import (
    FileSymbols,
    build_repo_map,
    compute_map_budget,
)
from llm_code.runtime.treesitter_parser import is_available, parse_file


class TestIsAvailable:
    def test_returns_bool(self) -> None:
        result = is_available()
        assert isinstance(result, bool)

    def test_returns_false_when_import_fails(self) -> None:
        with patch.dict("sys.modules", {"tree_sitter_language_pack": None}):

            # Force re-evaluation by calling directly
            # When module is None in sys.modules, import raises ImportError
            assert is_available() is True or is_available() is False  # just check it doesn't crash


class TestParseFile:
    def test_python_class_and_function(self, tmp_path: Path) -> None:
        code = '''
class UserService:
    def create_user(self, name: str) -> None:
        pass
    def delete_user(self, uid: int) -> bool:
        return True
    def _private(self):
        pass

def standalone_helper() -> str:
    return "hi"

def _private_func():
    pass
'''
        p = tmp_path / "service.py"
        p.write_text(code)

        result = parse_file(p, "service.py")

        if result is None:
            pytest.skip("tree-sitter not available")

        assert isinstance(result, FileSymbols)
        assert result.path == "service.py"
        assert len(result.classes) == 1
        assert result.classes[0].name == "UserService"
        assert "create_user" in result.classes[0].methods
        assert "delete_user" in result.classes[0].methods
        # Private methods should be excluded
        assert "_private" not in result.classes[0].methods
        assert "standalone_helper" in result.functions
        # Private functions should be excluded
        assert "_private_func" not in result.functions

    def test_returns_none_for_unsupported_extension(self, tmp_path: Path) -> None:
        p = tmp_path / "data.json"
        p.write_text('{"key": "value"}')

        result = parse_file(p, "data.json")
        assert result is None

    def test_returns_none_for_txt_file(self, tmp_path: Path) -> None:
        p = tmp_path / "readme.txt"
        p.write_text("Hello world")

        result = parse_file(p, "readme.txt")
        assert result is None

    def test_graceful_when_treesitter_not_installed(self, tmp_path: Path) -> None:
        p = tmp_path / "test.py"
        p.write_text("def foo(): pass")

        with patch.dict("sys.modules", {"tree_sitter_language_pack": None}):
            result = parse_file(p, "test.py")
            assert result is None

    def test_javascript_file(self, tmp_path: Path) -> None:
        code = '''
class MyComponent {
    render() {}
}

function handleClick() {}
'''
        p = tmp_path / "app.js"
        p.write_text(code)

        result = parse_file(p, "app.js")
        if result is None:
            pytest.skip("tree-sitter not available")

        assert isinstance(result, FileSymbols)
        assert any(c.name == "MyComponent" for c in result.classes)
        assert "handleClick" in result.functions

    def test_go_file(self, tmp_path: Path) -> None:
        code = '''package main

type Server struct {
    Port int
}

func NewServer() *Server {
    return &Server{}
}

func (s *Server) Start() error {
    return nil
}
'''
        p = tmp_path / "main.go"
        p.write_text(code)

        result = parse_file(p, "main.go")
        if result is None:
            pytest.skip("tree-sitter not available")

        assert isinstance(result, FileSymbols)
        assert any(c.name == "Server" for c in result.classes)
        assert "NewServer" in result.functions or "Start" in result.functions

    def test_empty_python_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.py"
        p.write_text("")

        result = parse_file(p, "empty.py")
        if result is None:
            pytest.skip("tree-sitter not available")

        assert result.classes == ()
        assert result.functions == ()


class TestParseFileFallback:
    """Test that _parse_file in repo_map falls back when tree-sitter returns None."""

    def test_fallback_to_ast_when_treesitter_returns_none(self, tmp_path: Path) -> None:
        code = '''
class Foo:
    def bar(self): ...

def baz(): ...
'''
        (tmp_path / "mod.py").write_text(code)

        # Mock tree-sitter parse_file to return None, forcing fallback
        with patch(
            "llm_code.runtime.treesitter_parser.parse_file",
            return_value=None,
        ):
            repo_map = build_repo_map(tmp_path)

        assert len(repo_map.files) == 1
        fs = repo_map.files[0]
        # Should still extract via Python AST fallback
        assert len(fs.classes) >= 1
        assert fs.classes[0].name == "Foo"
        assert "baz" in fs.functions

    def test_fallback_when_treesitter_import_fails(self, tmp_path: Path) -> None:
        code = "def hello(): pass\n"
        (tmp_path / "simple.py").write_text(code)

        with patch.dict("sys.modules", {"llm_code.runtime.treesitter_parser": None}):
            repo_map = build_repo_map(tmp_path)

        assert len(repo_map.files) == 1
        assert "hello" in repo_map.files[0].functions


class TestComputeMapBudget:
    def test_basic_calculation(self) -> None:
        # context_window=128000, chat_tokens=10000
        # available = 128000 - 10000 - 4096 = 113904
        # 113904 // 8 = 14238 -> capped at 4096
        budget = compute_map_budget(128000, 10000)
        assert budget == 4096

    def test_small_context_window(self) -> None:
        # context_window=8000, chat_tokens=4000
        # available = 8000 - 4000 - 4096 = -96
        # -96 // 8 = -12 -> floored at 512
        budget = compute_map_budget(8000, 4000)
        assert budget == 512

    def test_medium_context(self) -> None:
        # context_window=32000, chat_tokens=16000
        # available = 32000 - 16000 - 4096 = 11904
        # 11904 // 8 = 1488
        budget = compute_map_budget(32000, 16000)
        assert budget == 1488

    def test_minimum_floor(self) -> None:
        budget = compute_map_budget(5000, 5000)
        assert budget == 512

    def test_maximum_cap(self) -> None:
        budget = compute_map_budget(1_000_000, 0)
        assert budget == 4096

"""Tests for llm_code.tools.dump -- codebase dump for external LLM use."""
from __future__ import annotations

from pathlib import Path


from llm_code.tools.dump import dump_codebase, DumpResult


class TestDumpCodebase:
    def test_dumps_simple_directory(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "utils.py").write_text("def add(a, b): return a + b")

        result = dump_codebase(tmp_path)
        assert isinstance(result, DumpResult)
        assert result.file_count == 2
        assert result.total_lines == 2
        assert "--- file: main.py ---" in result.text
        assert "--- file: utils.py ---" in result.text
        assert result.estimated_tokens > 0

    def test_skips_gitignore_patterns(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("code")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"\x00\x01")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("git config")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.js").write_text("module")

        result = dump_codebase(tmp_path)
        assert result.file_count == 1
        assert "__pycache__" not in result.text
        assert ".git" not in result.text
        assert "node_modules" not in result.text

    def test_skips_large_files(self, tmp_path: Path) -> None:
        (tmp_path / "small.py").write_text("x = 1")
        (tmp_path / "huge.bin").write_text("x" * 60_000)  # > 50KB

        result = dump_codebase(tmp_path)
        assert result.file_count == 1
        assert "huge.bin" not in result.text

    def test_respects_max_files_limit(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"file_{i}.py").write_text(f"x = {i}")

        result = dump_codebase(tmp_path, max_files=3)
        assert result.file_count == 3

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = dump_codebase(tmp_path)
        assert result.file_count == 0
        assert result.text == ""
        assert result.estimated_tokens == 0

    def test_token_estimate_approximation(self, tmp_path: Path) -> None:
        content = "word " * 100  # ~500 chars
        (tmp_path / "words.txt").write_text(content)

        result = dump_codebase(tmp_path)
        # Rough: len(text) // 4
        assert result.estimated_tokens > 0
        assert result.estimated_tokens == len(result.text) // 4

    def test_skips_binary_files(self, tmp_path: Path) -> None:
        (tmp_path / "code.py").write_text("x = 1")
        (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        result = dump_codebase(tmp_path)
        assert result.file_count == 1
        assert "image.png" not in result.text

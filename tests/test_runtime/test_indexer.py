"""Tests for ProjectIndexer — written TDD-style (RED first)."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.runtime.indexer import ProjectIndex, ProjectIndexer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_text(
        "class App:\n    pass\n\ndef main():\n    pass\n\nVERSION = '1.0'\n"
    )
    (tmp_path / "utils.py").write_text("def helper():\n    pass\n")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "mod.ts").write_text("export class Service {}\nexport function init() {}\n")
    (tmp_path / "go_file.go").write_text(
        "package main\n\nfunc Run() {}\ntype Config struct {}\n"
    )
    # Dirs that should be skipped
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("x")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"\x00")
    return tmp_path


# ---------------------------------------------------------------------------
# File scanning
# ---------------------------------------------------------------------------


def test_scan_files_finds_source_files(project: Path) -> None:
    indexer = ProjectIndexer(project)
    index = indexer.build_index()
    assert len(index.files) == 4


def test_scan_files_skips_ignored_dirs(project: Path) -> None:
    indexer = ProjectIndexer(project)
    index = indexer.build_index()
    paths = {f.path for f in index.files}
    assert not any("node_modules" in p for p in paths)
    assert not any(".git" in p for p in paths)
    assert not any("__pycache__" in p for p in paths)


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------


def test_extract_python_symbols(project: Path) -> None:
    indexer = ProjectIndexer(project)
    index = indexer.build_index()
    python_symbols = [s for s in index.symbols if s.file.endswith(".py") and s.file == "main.py"]
    names = {s.name for s in python_symbols}
    kinds = {s.name: s.kind for s in python_symbols}
    assert "App" in names
    assert "main" in names
    assert "VERSION" in names
    assert kinds["App"] == "class"
    assert kinds["main"] == "function"
    assert kinds["VERSION"] == "variable"


def test_extract_typescript_symbols(project: Path) -> None:
    indexer = ProjectIndexer(project)
    index = indexer.build_index()
    ts_symbols = [s for s in index.symbols if s.file.endswith(".ts")]
    names = {s.name for s in ts_symbols}
    assert "Service" in names
    assert "init" in names


def test_extract_go_symbols(project: Path) -> None:
    indexer = ProjectIndexer(project)
    index = indexer.build_index()
    go_symbols = [s for s in index.symbols if s.file.endswith(".go")]
    names = {s.name for s in go_symbols}
    kinds = {s.name: s.kind for s in go_symbols}
    assert "Run" in names
    assert "Config" in names
    assert kinds["Run"] == "function"
    assert kinds["Config"] == "class"


# ---------------------------------------------------------------------------
# Full index
# ---------------------------------------------------------------------------


def test_build_index(project: Path) -> None:
    indexer = ProjectIndexer(project)
    index = indexer.build_index()
    assert isinstance(index, ProjectIndex)
    assert len(index.files) == 4
    assert len(index.symbols) > 0
    assert index.generated_at  # non-empty ISO timestamp


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_save_and_load(project: Path, tmp_path: Path) -> None:
    indexer = ProjectIndexer(project)
    index = indexer.build_index()
    save_path = tmp_path / "index.json"
    indexer.save(index, save_path)
    assert save_path.exists()
    loaded = ProjectIndexer.load(save_path)
    assert loaded is not None
    assert len(loaded.files) == len(index.files)
    assert len(loaded.symbols) == len(index.symbols)
    assert loaded.generated_at == index.generated_at


def test_load_missing_returns_none(tmp_path: Path) -> None:
    result = ProjectIndexer.load(tmp_path / "nonexistent.json")
    assert result is None


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


def test_language_detection(tmp_path: Path) -> None:
    (tmp_path / "script.py").write_text("x = 1\n")
    (tmp_path / "app.ts").write_text("const x = 1;\n")
    (tmp_path / "main.go").write_text("package main\n")
    (tmp_path / "data.unknown").write_text("stuff\n")

    indexer = ProjectIndexer(tmp_path)
    index = indexer.build_index()
    lang_map = {f.path: f.language for f in index.files}

    assert lang_map["script.py"] == "python"
    assert lang_map["app.ts"] == "typescript"
    assert lang_map["main.go"] == "go"
    assert lang_map["data.unknown"] == "unknown"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_project(tmp_path: Path) -> None:
    indexer = ProjectIndexer(tmp_path)
    index = indexer.build_index()
    assert index.files == ()
    assert index.symbols == ()
    assert index.generated_at

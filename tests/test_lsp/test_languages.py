"""Tests for the centralized LSP language table and project-root walker."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.lsp.languages import (
    EXTENSION_LANGUAGE,
    ROOT_MARKERS,
    find_project_root,
    language_for_file,
)


@pytest.mark.parametrize(
    "filename, language",
    [
        ("foo.py", "python"),
        ("foo.pyi", "python"),
        ("foo.ts", "typescript"),
        ("foo.tsx", "typescript"),
        ("foo.js", "javascript"),
        ("foo.jsx", "javascript"),
        ("foo.go", "go"),
        ("foo.rs", "rust"),
        ("foo.rb", "ruby"),
        ("foo.java", "java"),
        ("foo.kt", "kotlin"),
        ("foo.swift", "swift"),
        ("foo.cpp", "cpp"),
        ("foo.cc", "cpp"),
        ("foo.cxx", "cpp"),
        ("foo.h", "c"),
        ("foo.c", "c"),
        ("foo.cs", "csharp"),
        ("foo.php", "php"),
        ("foo.scala", "scala"),
        ("foo.lua", "lua"),
        ("foo.zig", "zig"),
        ("foo.dart", "dart"),
        ("foo.ex", "elixir"),
        ("foo.exs", "elixir"),
        ("foo.erl", "erlang"),
        ("foo.hs", "haskell"),
        ("foo.ml", "ocaml"),
        ("foo.json", "json"),
        ("foo.yaml", "yaml"),
        ("foo.yml", "yaml"),
        ("foo.toml", "toml"),
        ("foo.html", "html"),
        ("foo.css", "css"),
        ("foo.scss", "scss"),
        ("foo.vue", "vue"),
        ("foo.svelte", "svelte"),
        ("foo.sh", "shellscript"),
        ("foo.bash", "shellscript"),
        ("foo.sql", "sql"),
    ],
)
def test_language_for_file_known_extensions(filename: str, language: str) -> None:
    assert language_for_file(filename) == language


def test_language_for_file_case_insensitive() -> None:
    assert language_for_file("FOO.PY") == "python"


def test_language_for_file_unknown() -> None:
    assert language_for_file("foo.unknown_ext_zzz") == ""


def test_extension_table_has_at_least_60_entries() -> None:
    """Sanity floor — opencode has ~120; we want at least 60 to be useful."""
    assert len(EXTENSION_LANGUAGE) >= 60


def test_root_markers_includes_common_files() -> None:
    expected = {
        "pyproject.toml", "setup.py", "package.json", "tsconfig.json",
        "go.mod", "Cargo.toml", ".git", "pom.xml", "build.gradle",
    }
    assert expected.issubset(set(ROOT_MARKERS))


def test_find_project_root_via_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    (sub / "mod.py").write_text("x = 1")
    assert find_project_root(sub / "mod.py") == tmp_path


def test_find_project_root_via_git_dir(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "a.py").write_text("")
    assert find_project_root(tmp_path / "x" / "a.py") == tmp_path


def test_find_project_root_returns_none_when_no_marker(tmp_path: Path) -> None:
    (tmp_path / "lone.py").write_text("")
    assert find_project_root(tmp_path / "lone.py") is None


def test_find_project_root_stops_at_first_match(tmp_path: Path) -> None:
    """Inner pyproject takes precedence over an outer .git."""
    (tmp_path / ".git").mkdir()
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "pyproject.toml").write_text("[project]\nname='inner'\n")
    sub = inner / "src"
    sub.mkdir()
    (sub / "a.py").write_text("")
    assert find_project_root(sub / "a.py") == inner

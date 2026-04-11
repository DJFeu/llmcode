"""Tests for the @file path completer (M15 Task B2)."""
from __future__ import annotations

from pathlib import Path

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from llm_code.view.repl.components.path_completer import PathCompleter


def test_at_prefix_yields_file_completions(tmp_path: Path) -> None:
    (tmp_path / "alpha.py").write_text("")
    (tmp_path / "beta.py").write_text("")
    completer = PathCompleter(cwd=tmp_path)
    doc = Document(text="@a", cursor_position=2)
    results = list(completer.get_completions(doc, CompleteEvent()))
    assert len(results) >= 1
    assert any("alpha" in c.text for c in results)


def test_dot_slash_prefix_yields_completions(tmp_path: Path) -> None:
    (tmp_path / "gamma.py").write_text("")
    completer = PathCompleter(cwd=tmp_path)
    doc = Document(text="./g", cursor_position=3)
    results = list(completer.get_completions(doc, CompleteEvent()))
    assert any("gamma" in c.text for c in results)


def test_no_completions_for_plain_token(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text("")
    completer = PathCompleter(cwd=tmp_path)
    doc = Document(text="hello", cursor_position=5)
    results = list(completer.get_completions(doc, CompleteEvent()))
    assert results == []


def test_skips_hidden_files(tmp_path: Path) -> None:
    (tmp_path / ".secret").write_text("")
    (tmp_path / "visible.py").write_text("")
    completer = PathCompleter(cwd=tmp_path)
    doc = Document(text="@", cursor_position=1)
    results = list(completer.get_completions(doc, CompleteEvent()))
    assert all("secret" not in c.text for c in results)
    assert any("visible" in c.text for c in results)


def test_dir_meta_marker(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    completer = PathCompleter(cwd=tmp_path)
    doc = Document(text="@s", cursor_position=2)
    results = list(completer.get_completions(doc, CompleteEvent()))
    # display_meta is a FormattedText list; join into a string for the
    # assertion.
    assert any(
        "dir" in "".join(text for _, text in c.display_meta)
        for c in results
    )

"""Tests for the rules_injector builtin hook."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.runtime.builtin_hooks import rules_injector
from llm_code.runtime.hooks import HookRunner


@pytest.fixture(autouse=True)
def _clear_state():
    rules_injector._INJECTED.clear()
    yield
    rules_injector._INJECTED.clear()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "CLAUDE.md").write_text("# Project rules\nuse type hints")
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.py").write_text("x = 1")
    (src / "AGENTS.md").write_text("# Agent rules\nbe terse")
    return tmp_path


def _ctx(file_path: str, sid: str = "s1") -> dict:
    return {
        "tool_name": "read_file",
        "session_id": sid,
        "file_path": file_path,
    }


def test_finds_project_root_via_pyproject(project: Path) -> None:
    found = rules_injector._find_project_root(project / "src" / "foo.py")
    assert found == project


def test_finds_no_root_when_no_marker(tmp_path: Path) -> None:
    (tmp_path / "lone.py").write_text("")
    assert rules_injector._find_project_root(tmp_path / "lone.py") is None


def test_collects_rule_files_from_root_and_subdir(project: Path) -> None:
    rules = rules_injector._collect_rule_files(project / "src" / "foo.py", project)
    names = {p.name for p in rules}
    assert {"CLAUDE.md", "AGENTS.md"}.issubset(names)


def test_inject_appends_rule_bodies_in_extra_output(project: Path) -> None:
    out = rules_injector.handle("post_tool_use", _ctx(str(project / "src" / "foo.py")))
    assert out is not None
    assert "Project rules" in out.extra_output
    assert "Agent rules" in out.extra_output


def test_each_rule_injected_only_once_per_session(project: Path) -> None:
    first = rules_injector.handle("post_tool_use", _ctx(str(project / "src" / "foo.py")))
    second = rules_injector.handle("post_tool_use", _ctx(str(project / "src" / "foo.py")))
    assert first is not None and "Project rules" in first.extra_output
    assert second is None or second.extra_output == ""


def test_different_sessions_inject_independently(project: Path) -> None:
    a = rules_injector.handle("post_tool_use", _ctx(str(project / "src" / "foo.py"), sid="a"))
    b = rules_injector.handle("post_tool_use", _ctx(str(project / "src" / "foo.py"), sid="b"))
    assert a is not None and b is not None
    assert "Project rules" in b.extra_output


def test_non_read_tool_is_ignored(project: Path) -> None:
    ctx = {
        "tool_name": "bash",
        "session_id": "s1",
        "file_path": str(project / "src" / "foo.py"),
    }
    assert rules_injector.handle("post_tool_use", ctx) is None


def test_missing_file_path_is_ignored() -> None:
    assert rules_injector.handle("post_tool_use", {"tool_name": "read_file"}) is None


def test_size_cap_truncates(project: Path, monkeypatch) -> None:
    big = "x" * 50_000
    (project / "CLAUDE.md").write_text(big)
    monkeypatch.setattr(rules_injector, "MAX_INJECT_BYTES", 1024)
    out = rules_injector.handle("post_tool_use", _ctx(str(project / "src" / "foo.py")))
    assert out is not None
    assert len(out.extra_output.encode()) <= 2048


def test_session_end_clears_state(project: Path) -> None:
    rules_injector.handle("post_tool_use", _ctx(str(project / "src" / "foo.py")))
    rules_injector.handle("session_end", {"session_id": "s1"})
    out = rules_injector.handle("post_tool_use", _ctx(str(project / "src" / "foo.py")))
    assert out is not None and "Project rules" in out.extra_output


def test_register_subscribes_to_post_tool_use_and_session_end() -> None:
    runner = HookRunner()
    rules_injector.register(runner)
    assert "post_tool_use" in runner._subscribers
    assert "session_end" in runner._subscribers

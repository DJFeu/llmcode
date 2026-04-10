"""Tests for agent memory scope management."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.runtime.agent_memory import (
    MEMORY_TOOLS,
    MemoryScope,
    _sanitise_name,
    inject_memory_tools,
    resolve_memory_dir,
)


class TestSanitiseName:
    def test_plain_name(self) -> None:
        assert _sanitise_name("security-auditor") == "security-auditor"

    def test_colons(self) -> None:
        assert _sanitise_name("agent:v2") == "agent-v2"

    def test_windows_unsafe(self) -> None:
        assert _sanitise_name('a<b>c|d"e') == "a-b-c-d-e"

    def test_empty_fallback(self) -> None:
        assert _sanitise_name(":::") == "unnamed"


class TestResolveMemoryDir:
    def test_user_scope(self) -> None:
        path = resolve_memory_dir("reviewer", "user")
        assert path == Path.home() / ".llm-code" / "agent-memory" / "reviewer"

    def test_project_scope(self, tmp_path: Path) -> None:
        path = resolve_memory_dir("reviewer", "project", project_path=tmp_path)
        assert path == tmp_path / ".llm-code" / "agent-memory" / "reviewer"

    def test_local_scope(self, tmp_path: Path) -> None:
        path = resolve_memory_dir("reviewer", "local", project_path=tmp_path)
        assert path == tmp_path / ".llm-code" / "agent-memory-local" / "reviewer"

    def test_project_scope_requires_project_path(self) -> None:
        with pytest.raises(ValueError, match="project_path is required"):
            resolve_memory_dir("x", "project", project_path=None)

    def test_local_scope_requires_project_path(self) -> None:
        with pytest.raises(ValueError, match="project_path is required"):
            resolve_memory_dir("x", "local", project_path=None)

    def test_unknown_scope(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Unknown memory scope"):
            resolve_memory_dir("x", "bad", project_path=tmp_path)  # type: ignore[arg-type]

    def test_sanitised_name_in_path(self) -> None:
        path = resolve_memory_dir("agent:v2", "user")
        assert "agent-v2" in str(path)


class TestInjectMemoryTools:
    def test_none_stays_none(self) -> None:
        assert inject_memory_tools(None) is None

    def test_adds_to_existing(self) -> None:
        base = frozenset({"bash", "grep_search"})
        result = inject_memory_tools(base)
        assert result is not None
        assert MEMORY_TOOLS <= result
        assert "bash" in result
        assert "grep_search" in result

    def test_empty_gets_memory_tools(self) -> None:
        result = inject_memory_tools(frozenset())
        assert result == MEMORY_TOOLS

    def test_idempotent(self) -> None:
        base = frozenset({"read_file", "write_file", "edit_file", "bash"})
        result = inject_memory_tools(base)
        assert result == base

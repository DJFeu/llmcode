"""Tests for per-directory instruction attachment."""
from __future__ import annotations

import pytest

from llm_code.runtime.instruction_attach import (
    attach_for,
    clear_attached,
    find_nearby_instructions,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear attachment cache before every test."""
    clear_attached()
    yield
    clear_attached()


class TestFindNearbyInstructions:
    def test_finds_in_same_directory(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "AGENTS.md").write_text("project rules")
        target = tmp_path / "src" / "main.py"
        target.parent.mkdir()
        target.write_text("# code")
        results = find_nearby_instructions(target)
        assert len(results) == 1
        assert results[0].name == "AGENTS.md"

    def test_finds_in_parent_directory(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "AGENTS.md").write_text("module rules")
        (tmp_path / "src" / "auth").mkdir()
        target = tmp_path / "src" / "auth" / "login.py"
        target.write_text("# code")
        results = find_nearby_instructions(target)
        assert any(p.parent.name == "src" for p in results)

    def test_finds_multiple_levels(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "AGENTS.md").write_text("root rules")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "AGENTS.md").write_text("module rules")
        target = tmp_path / "src" / "main.py"
        target.write_text("# code")
        results = find_nearby_instructions(target)
        # Should find both
        assert len(results) == 2

    def test_stops_at_git_root(self, tmp_path):
        # Create instructions OUTSIDE the git root — should not be found
        outer = tmp_path / "outer"
        outer.mkdir()
        (outer / "AGENTS.md").write_text("outer rules")
        repo = outer / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "AGENTS.md").write_text("repo rules")
        target = repo / "src" / "main.py"
        target.parent.mkdir()
        target.write_text("# code")
        results = find_nearby_instructions(target)
        # Only repo/AGENTS.md, not outer/AGENTS.md
        names = [str(p) for p in results]
        assert any("repo/AGENTS.md" in n for n in names)
        assert not any("outer/AGENTS.md" in n for n in names)

    def test_prefers_AGENTS_over_CLAUDE(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "AGENTS.md").write_text("agents rules")
        (tmp_path / "CLAUDE.md").write_text("claude rules")
        target = tmp_path / "main.py"
        target.write_text("# code")
        results = find_nearby_instructions(target)
        # First match per directory wins (AGENTS.md before CLAUDE.md)
        assert len(results) == 1
        assert results[0].name == "AGENTS.md"

    def test_no_results_for_empty_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        target = tmp_path / "main.py"
        target.write_text("# code")
        results = find_nearby_instructions(target)
        assert results == []


class TestAttachFor:
    def test_attaches_new_instruction(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "AGENTS.md").write_text("Project conventions: use snake_case.")
        target = tmp_path / "main.py"
        target.write_text("# code")
        footer = attach_for(target)
        assert "snake_case" in footer
        assert "Auto-attached" in footer

    def test_does_not_attach_twice_in_same_session(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "AGENTS.md").write_text("rules")
        target1 = tmp_path / "a.py"
        target1.write_text("# code")
        target2 = tmp_path / "b.py"
        target2.write_text("# code")
        first = attach_for(target1)
        second = attach_for(target2)
        assert "rules" in first
        assert second == ""  # already attached

    def test_skips_files_in_base_set(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "AGENTS.md").write_text("rules")
        target = tmp_path / "main.py"
        target.write_text("# code")
        base = {str(tmp_path / "AGENTS.md")}
        footer = attach_for(target, base_instructions=base)
        assert footer == ""  # already in system prompt

    def test_returns_empty_when_no_instructions(self, tmp_path):
        (tmp_path / ".git").mkdir()
        target = tmp_path / "main.py"
        target.write_text("# code")
        footer = attach_for(target)
        assert footer == ""

    def test_handles_nonexistent_file(self, tmp_path):
        target = tmp_path / "nonexistent.py"
        footer = attach_for(target)
        assert footer == ""

    def test_clear_resets_cache(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "AGENTS.md").write_text("rules")
        target = tmp_path / "main.py"
        target.write_text("# code")
        first = attach_for(target)
        clear_attached()
        second = attach_for(target)
        assert "rules" in first
        assert "rules" in second  # cache was cleared, attached again

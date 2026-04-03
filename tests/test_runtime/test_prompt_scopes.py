"""Tests for three-tier prompt cache scopes in SystemPromptBuilder."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_code.runtime.context import ProjectContext
from llm_code.runtime.prompt import PromptSection, SystemPromptBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        cwd=tmp_path,
        is_git_repo=False,
        git_status="",
        instructions="",
    )


def _build(context: ProjectContext, **kwargs) -> str:
    builder = SystemPromptBuilder()
    return builder.build(context, **kwargs)


def _section_positions(prompt: str) -> dict:
    """Return character positions for key markers in the prompt."""
    return {
        "cache_boundary_1": prompt.find("# -- CACHE BOUNDARY --"),
        "cache_boundary_2": prompt.rfind("# -- CACHE BOUNDARY --"),
    }


# ---------------------------------------------------------------------------
# PromptSection dataclass
# ---------------------------------------------------------------------------

class TestPromptSection:
    def test_is_frozen(self) -> None:
        """PromptSection is immutable (frozen dataclass)."""
        section = PromptSection(content="hello", scope="global", priority=0)
        with pytest.raises(dataclasses.FrozenInstanceError if hasattr(section, "__dataclass_params__") else AttributeError):
            section.content = "changed"  # type: ignore[misc]

    def test_default_priority(self) -> None:
        """Default priority is 0."""
        section = PromptSection(content="x", scope="project")
        assert section.priority == 0

    def test_all_scopes_valid(self) -> None:
        """All three scope values are accepted."""
        for scope in ("global", "project", "session"):
            s = PromptSection(content="x", scope=scope)  # type: ignore[arg-type]
            assert s.scope == scope


# ---------------------------------------------------------------------------
# Scope ordering tests
# ---------------------------------------------------------------------------

class TestScopeOrdering:
    def test_global_sections_come_first(self, tmp_path: Path) -> None:
        """Global scope content appears before project and session content."""
        ctx = _make_context(tmp_path)
        prompt = _build(ctx)

        # The intro (_INTRO) and behavior rules are global
        intro_pos = prompt.find("You are a coding assistant")
        env_pos = prompt.find("## Environment")

        assert intro_pos != -1
        assert env_pos != -1
        assert intro_pos < env_pos, "Global content should precede session content"

    def test_project_sections_come_second(self, tmp_path: Path) -> None:
        """Project scope content appears after global but before session content."""
        instructions_dir = tmp_path / ".llm-code"
        instructions_dir.mkdir()
        (instructions_dir / "INSTRUCTIONS.md").write_text("Project rule: do X.", encoding="utf-8")
        ctx = ProjectContext.discover(tmp_path)
        prompt = _build(ctx)

        global_pos = prompt.find("You are a coding assistant")
        project_pos = prompt.find("## Project Instructions")
        session_pos = prompt.find("## Environment")

        assert global_pos != -1
        assert project_pos != -1
        assert session_pos != -1
        assert global_pos < project_pos < session_pos

    def test_session_sections_come_last(self, tmp_path: Path) -> None:
        """Session scope content (environment) appears after all other scopes."""
        ctx = _make_context(tmp_path)
        prompt = _build(ctx)

        global_pos = prompt.find("You are a coding assistant")
        env_pos = prompt.find("## Environment")

        assert global_pos != -1
        assert env_pos != -1
        assert global_pos < env_pos


# ---------------------------------------------------------------------------
# Cache boundary tests
# ---------------------------------------------------------------------------

class TestCacheBoundaries:
    def test_two_cache_boundaries_present_with_project_content(self, tmp_path: Path) -> None:
        """Two cache boundary markers appear when all three scopes have content."""
        instructions_dir = tmp_path / ".llm-code"
        instructions_dir.mkdir()
        (instructions_dir / "INSTRUCTIONS.md").write_text("Some project instruction.", encoding="utf-8")
        ctx = ProjectContext.discover(tmp_path)
        prompt = _build(ctx)

        boundaries = [i for i in range(len(prompt)) if prompt.startswith("# -- CACHE BOUNDARY --", i)]
        assert len(boundaries) == 2, f"Expected 2 cache boundaries, found {len(boundaries)}"

    def test_one_cache_boundary_without_project_content(self, tmp_path: Path) -> None:
        """One cache boundary appears when project scope is empty (global → session only)."""
        ctx = _make_context(tmp_path)
        prompt = _build(ctx)

        boundaries = [i for i in range(len(prompt)) if prompt.startswith("# -- CACHE BOUNDARY --", i)]
        # Only global and session scopes present → one boundary
        assert len(boundaries) == 1, f"Expected 1 cache boundary, found {len(boundaries)}"

    def test_cache_control_marker_after_each_boundary(self, tmp_path: Path) -> None:
        """Each cache boundary is followed by a cache_control JSON marker."""
        instructions_dir = tmp_path / ".llm-code"
        instructions_dir.mkdir()
        (instructions_dir / "INSTRUCTIONS.md").write_text("project instructions", encoding="utf-8")
        ctx = ProjectContext.discover(tmp_path)
        prompt = _build(ctx)

        expected_marker = json.dumps({"type": "cache_control", "cache_type": "ephemeral"})
        assert prompt.count(expected_marker) == 2

    def test_boundaries_in_correct_order(self, tmp_path: Path) -> None:
        """First boundary is between global and project; second between project and session."""
        instructions_dir = tmp_path / ".llm-code"
        instructions_dir.mkdir()
        (instructions_dir / "INSTRUCTIONS.md").write_text("project note", encoding="utf-8")
        ctx = ProjectContext.discover(tmp_path)
        prompt = _build(ctx)

        global_content_pos = prompt.find("You are a coding assistant")
        project_content_pos = prompt.find("## Project Instructions")
        session_content_pos = prompt.find("## Environment")

        boundaries = [i for i in range(len(prompt)) if prompt.startswith("# -- CACHE BOUNDARY --", i)]
        assert len(boundaries) == 2

        first_boundary, second_boundary = boundaries
        # First boundary should be between global and project sections
        assert global_content_pos < first_boundary < project_content_pos
        # Second boundary should be between project and session sections
        assert project_content_pos < second_boundary < session_content_pos

    def test_no_boundary_if_only_global_scope(self, tmp_path: Path) -> None:
        """No boundary markers when only global sections are present (degenerate case)."""
        # Build manually to test serializer directly
        builder = SystemPromptBuilder()
        sections = [
            PromptSection(content="global a", scope="global", priority=0),
            PromptSection(content="global b", scope="global", priority=1),
        ]
        result = builder._serialize(sections)
        assert "# -- CACHE BOUNDARY --" not in result


# ---------------------------------------------------------------------------
# Serialize ordering and priority tests
# ---------------------------------------------------------------------------

class TestSerializeOrdering:
    def test_priority_within_scope(self) -> None:
        """Lower priority value comes first within the same scope."""
        builder = SystemPromptBuilder()
        sections = [
            PromptSection(content="second", scope="global", priority=10),
            PromptSection(content="first", scope="global", priority=0),
        ]
        result = builder._serialize(sections)
        assert result.index("first") < result.index("second")

    def test_scope_ordering_overrides_priority(self) -> None:
        """Global scope comes before project regardless of priority values."""
        builder = SystemPromptBuilder()
        sections = [
            PromptSection(content="project content", scope="project", priority=0),
            PromptSection(content="global content", scope="global", priority=999),
        ]
        result = builder._serialize(sections)
        assert result.index("global content") < result.index("project content")

    def test_cache_boundary_between_global_and_session(self) -> None:
        """Cache boundary is inserted between global and session scopes."""
        builder = SystemPromptBuilder()
        sections = [
            PromptSection(content="global stuff", scope="global", priority=0),
            PromptSection(content="session stuff", scope="session", priority=0),
        ]
        result = builder._serialize(sections)
        global_pos = result.index("global stuff")
        boundary_pos = result.index("# -- CACHE BOUNDARY --")
        session_pos = result.index("session stuff")
        assert global_pos < boundary_pos < session_pos


# ---------------------------------------------------------------------------
# Import for frozen dataclass check
# ---------------------------------------------------------------------------
import dataclasses  # noqa: E402 — imported here to keep test self-contained

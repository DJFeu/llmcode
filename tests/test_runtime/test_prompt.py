"""Tests for ProjectContext discovery and SystemPromptBuilder."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch


from llm_code.api.types import ToolDefinition
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.prompt import SystemPromptBuilder
from llm_code.runtime.skills import Skill, SkillSet


# ---------------------------------------------------------------------------
# ProjectContext.discover
# ---------------------------------------------------------------------------

class TestProjectContextDiscover:
    def test_non_git_dir(self, tmp_path: Path) -> None:
        ctx = ProjectContext.discover(tmp_path)
        assert ctx.is_git_repo is False
        assert ctx.git_status == ""
        assert ctx.cwd == tmp_path

    def test_git_dir_detected(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext.discover(tmp_path)
        assert ctx.is_git_repo is True

    def test_git_status_called_for_git_repo(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=" M foo.py\n"
            )
            ctx = ProjectContext.discover(tmp_path)
        assert " M foo.py" in ctx.git_status

    def test_git_status_empty_for_non_git(self, tmp_path: Path) -> None:
        ctx = ProjectContext.discover(tmp_path)
        assert ctx.git_status == ""

    def test_loads_instructions_file(self, tmp_path: Path) -> None:
        instructions_dir = tmp_path / ".llmcode"
        instructions_dir.mkdir()
        (instructions_dir / "INSTRUCTIONS.md").write_text("Do the thing.", encoding="utf-8")
        ctx = ProjectContext.discover(tmp_path)
        # New format wraps with "# Instructions from: <path>" header
        assert "Do the thing." in ctx.instructions

    def test_no_instructions_file(self, tmp_path: Path) -> None:
        ctx = ProjectContext.discover(tmp_path)
        assert ctx.instructions == ""


# ---------------------------------------------------------------------------
# SystemPromptBuilder
# ---------------------------------------------------------------------------

class TestSystemPromptBuilder:
    def _make_context(self, tmp_path: Path, **kwargs) -> ProjectContext:
        defaults = dict(
            cwd=tmp_path,
            is_git_repo=False,
            git_status="",
            instructions="",
        )
        defaults.update(kwargs)
        return ProjectContext(**defaults)

    def test_build_returns_string(self, tmp_path: Path) -> None:
        ctx = self._make_context(tmp_path)
        result = SystemPromptBuilder().build(ctx)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_cwd_in_prompt(self, tmp_path: Path) -> None:
        ctx = self._make_context(tmp_path)
        result = SystemPromptBuilder().build(ctx)
        assert str(tmp_path) in result

    def test_instructions_in_prompt_when_provided(self, tmp_path: Path) -> None:
        ctx = self._make_context(tmp_path, instructions="Follow the project style guide.")
        result = SystemPromptBuilder().build(ctx)
        assert "Follow the project style guide." in result

    def test_instructions_absent_when_empty(self, tmp_path: Path) -> None:
        ctx = self._make_context(tmp_path, instructions="")
        result = SystemPromptBuilder().build(ctx)
        # No placeholder or empty instructions section leaking in
        assert "Follow the project style guide." not in result

    def test_native_tools_no_xml(self, tmp_path: Path) -> None:
        ctx = self._make_context(tmp_path)
        tool = ToolDefinition(
            name="read_file",
            description="Read a file",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
        result = SystemPromptBuilder().build(ctx, tools=(tool,), native_tools=True)
        assert "<tool_call>" not in result

    def test_non_native_tools_xml_injected(self, tmp_path: Path) -> None:
        ctx = self._make_context(tmp_path)
        tool = ToolDefinition(
            name="read_file",
            description="Read a file",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
        result = SystemPromptBuilder().build(ctx, tools=(tool,), native_tools=False)
        assert "<tool_call>" in result
        assert "read_file" in result

    def test_git_status_in_prompt_when_present(self, tmp_path: Path) -> None:
        ctx = self._make_context(tmp_path, is_git_repo=True, git_status=" M main.py")
        result = SystemPromptBuilder().build(ctx)
        assert " M main.py" in result


# ---------------------------------------------------------------------------
# TestSystemPromptBuilder — Cache Boundary & Skills
# ---------------------------------------------------------------------------

class TestSystemPromptBuilderCacheAndSkills:
    """Tests for prefix-cache boundary and skills injection."""

    _CACHE_MARKER = "# -- CACHE BOUNDARY --"

    def _make_context(self, tmp_path: Path, **kwargs) -> ProjectContext:
        defaults = dict(
            cwd=tmp_path,
            is_git_repo=False,
            git_status="",
            instructions="",
        )
        defaults.update(kwargs)
        return ProjectContext(**defaults)

    def _make_skill(self, *, name: str, auto: bool, trigger: str | None = None) -> Skill:
        return Skill(
            name=name,
            description=f"desc for {name}",
            content=f"Content of {name}",
            auto=auto,
            trigger=trigger or name,
        )

    def test_cache_boundary_present(self, tmp_path: Path) -> None:
        ctx = self._make_context(tmp_path)
        result = SystemPromptBuilder().build(ctx)
        assert self._CACHE_MARKER in result

    def test_static_content_before_cache_boundary(self, tmp_path: Path) -> None:
        ctx = self._make_context(tmp_path)
        result = SystemPromptBuilder().build(ctx)
        boundary_idx = result.index(self._CACHE_MARKER)
        # Static intro must appear before the boundary
        assert result.index("coding assistant") < boundary_idx

    def test_dynamic_content_after_cache_boundary(self, tmp_path: Path) -> None:
        ctx = self._make_context(tmp_path)
        result = SystemPromptBuilder().build(ctx)
        boundary_idx = result.index(self._CACHE_MARKER)
        # cwd is dynamic — must appear after boundary
        assert result.index(str(tmp_path)) > boundary_idx

    def test_auto_skills_injected_before_boundary(self, tmp_path: Path) -> None:
        auto_skill = self._make_skill(name="linter", auto=True)
        skill_set = SkillSet(auto_skills=(auto_skill,), command_skills=())
        ctx = self._make_context(tmp_path)
        result = SystemPromptBuilder().build(ctx, skills=skill_set, routed_skills=(auto_skill,))
        boundary_idx = result.index(self._CACHE_MARKER)
        assert "Content of linter" in result
        assert result.index("Content of linter") < boundary_idx

    def test_command_skills_not_in_output(self, tmp_path: Path) -> None:
        cmd_skill = self._make_skill(name="code-review", auto=False)
        skill_set = SkillSet(auto_skills=(), command_skills=(cmd_skill,))
        ctx = self._make_context(tmp_path)
        result = SystemPromptBuilder().build(ctx, skills=skill_set)
        assert "Content of code-review" not in result

    def test_active_skill_content_injected_after_boundary(self, tmp_path: Path) -> None:
        ctx = self._make_context(tmp_path)
        active_content = "Please perform a deep code review."
        result = SystemPromptBuilder().build(ctx, active_skill_content=active_content)
        boundary_idx = result.index(self._CACHE_MARKER)
        assert active_content in result
        assert result.index(active_content) > boundary_idx

    def test_no_skills_no_auto_section(self, tmp_path: Path) -> None:
        ctx = self._make_context(tmp_path)
        result = SystemPromptBuilder().build(ctx, skills=None)
        # No skills injected — just the boundary present
        assert self._CACHE_MARKER in result

    def test_instructions_after_cache_boundary(self, tmp_path: Path) -> None:
        ctx = self._make_context(tmp_path, instructions="Use snake_case.")
        result = SystemPromptBuilder().build(ctx)
        boundary_idx = result.index(self._CACHE_MARKER)
        assert result.index("Use snake_case.") > boundary_idx

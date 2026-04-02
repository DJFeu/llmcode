"""Tests for ProjectContext discovery and SystemPromptBuilder."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch


from llm_code.api.types import ToolDefinition
from llm_code.runtime.context import ProjectContext
from llm_code.runtime.prompt import SystemPromptBuilder


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
        instructions_dir = tmp_path / ".llm-code"
        instructions_dir.mkdir()
        (instructions_dir / "INSTRUCTIONS.md").write_text("Do the thing.", encoding="utf-8")
        ctx = ProjectContext.discover(tmp_path)
        assert ctx.instructions == "Do the thing."

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

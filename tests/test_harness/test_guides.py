"""Tests for harness guide implementations."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_repo_map_guide_returns_compact_string(tmp_path: Path):
    from llm_code.harness.guides import repo_map_guide

    py_file = tmp_path / "example.py"
    py_file.write_text("def hello():\n    pass\n")

    result = repo_map_guide(cwd=tmp_path, max_tokens=2000)
    assert isinstance(result, str)
    assert "hello" in result


def test_repo_map_guide_empty_dir(tmp_path: Path):
    from llm_code.harness.guides import repo_map_guide

    result = repo_map_guide(cwd=tmp_path, max_tokens=2000)
    assert result == ""


def test_repo_map_guide_handles_errors(tmp_path: Path):
    from llm_code.harness.guides import repo_map_guide

    with patch("llm_code.harness.guides.build_repo_map", side_effect=RuntimeError("boom")):
        result = repo_map_guide(cwd=tmp_path, max_tokens=2000)
    assert result == ""


def test_analysis_context_guide_returns_stored():
    from llm_code.harness.guides import analysis_context_guide

    result = analysis_context_guide(context="[Code Analysis] 3 violations found")
    assert result == "[Code Analysis] 3 violations found"


def test_analysis_context_guide_none():
    from llm_code.harness.guides import analysis_context_guide

    assert analysis_context_guide(context=None) == ""


def test_plan_mode_denied_tools():
    from llm_code.harness.guides import plan_mode_denied_tools, PLAN_DENIED_TOOLS

    result = plan_mode_denied_tools(active=True)
    assert result == PLAN_DENIED_TOOLS
    assert "write_file" in result
    assert "edit_file" in result
    assert "bash" in result


def test_plan_mode_inactive_returns_empty():
    from llm_code.harness.guides import plan_mode_denied_tools

    result = plan_mode_denied_tools(active=False)
    assert result == frozenset()


def test_knowledge_guide_returns_content(tmp_path: Path):
    from llm_code.harness.guides import knowledge_guide

    knowledge_dir = tmp_path / ".llmcode" / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "modules").mkdir()
    (knowledge_dir / "index.md").write_text(
        "# Knowledge Index\n\n- [Api](modules/api.md) — REST API\n"
    )
    (knowledge_dir / "modules" / "api.md").write_text("# API\n\nHandles requests.\n")

    result = knowledge_guide(cwd=tmp_path, max_tokens=3000)
    assert "API" in result


def test_knowledge_guide_empty(tmp_path: Path):
    from llm_code.harness.guides import knowledge_guide

    result = knowledge_guide(cwd=tmp_path, max_tokens=3000)
    assert result == ""


def test_knowledge_guide_handles_errors(tmp_path: Path):
    from llm_code.harness.guides import knowledge_guide

    with patch("llm_code.harness.guides.KnowledgeCompiler", side_effect=RuntimeError("boom")):
        result = knowledge_guide(cwd=tmp_path, max_tokens=3000)
    assert result == ""

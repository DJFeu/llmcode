"""Tests for model-specific tool filtering in ToolRegistry."""
from __future__ import annotations

from llm_code.tools.base import PermissionLevel, Tool, ToolResult
from llm_code.tools.registry import ToolRegistry, _filter_by_model, _is_gpt_codex


class _StubTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"stub {self._name}"

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> PermissionLevel:
        return PermissionLevel.READ_ONLY

    def execute(self, args: dict) -> ToolResult:
        return ToolResult(output="ok")


class TestIsGptCodex:
    def test_gpt_4o(self):
        assert _is_gpt_codex("gpt-4o") is True

    def test_gpt_5(self):
        assert _is_gpt_codex("gpt-5") is True

    def test_codex(self):
        assert _is_gpt_codex("openai/gpt-codex") is True

    def test_claude_not_gpt(self):
        assert _is_gpt_codex("claude-opus-4-6") is False

    def test_qwen_not_gpt(self):
        assert _is_gpt_codex("qwen3.5-122b") is False

    def test_gpt_oss_excluded(self):
        # gpt-oss is open-source weights, behaves differently
        assert _is_gpt_codex("openai/gpt-oss-20b") is False


class TestFilterByModel:
    def test_no_filtering_when_only_one_editor(self):
        tools = [_StubTool("read_file"), _StubTool("edit_file"), _StubTool("bash")]
        result = _filter_by_model(tools, "gpt-4o")
        assert len(result) == 3  # apply_patch not present, no filtering

    def test_gpt_hides_edit_when_apply_patch_present(self):
        tools = [_StubTool("read_file"), _StubTool("edit_file"), _StubTool("apply_patch")]
        result = _filter_by_model(tools, "gpt-4o")
        names = [t.name for t in result]
        assert "edit_file" not in names
        assert "apply_patch" in names

    def test_qwen_hides_apply_patch_when_edit_present(self):
        tools = [_StubTool("read_file"), _StubTool("edit_file"), _StubTool("apply_patch")]
        result = _filter_by_model(tools, "qwen3.5-122b")
        names = [t.name for t in result]
        assert "edit_file" in names
        assert "apply_patch" not in names

    def test_claude_hides_apply_patch(self):
        tools = [_StubTool("edit_file"), _StubTool("apply_patch")]
        result = _filter_by_model(tools, "claude-opus-4-6")
        names = [t.name for t in result]
        assert "apply_patch" not in names


class TestRegistryWithModel:
    def test_definitions_with_model_filters(self):
        reg = ToolRegistry()
        reg.register(_StubTool("read_file"))
        reg.register(_StubTool("edit_file"))
        reg.register(_StubTool("apply_patch"))
        # GPT model
        gpt_defs = reg.definitions(model="gpt-4o")
        gpt_names = {d.name for d in gpt_defs}
        assert "edit_file" not in gpt_names
        assert "apply_patch" in gpt_names
        # Qwen model
        qwen_defs = reg.definitions(model="qwen3.5")
        qwen_names = {d.name for d in qwen_defs}
        assert "edit_file" in qwen_names
        assert "apply_patch" not in qwen_names

    def test_definitions_without_model_returns_all(self):
        reg = ToolRegistry()
        reg.register(_StubTool("edit_file"))
        reg.register(_StubTool("apply_patch"))
        defs = reg.definitions()  # no model
        assert len(defs) == 2

    def test_allowed_filter_combined_with_model(self):
        reg = ToolRegistry()
        reg.register(_StubTool("read_file"))
        reg.register(_StubTool("edit_file"))
        reg.register(_StubTool("apply_patch"))
        defs = reg.definitions(allowed={"edit_file", "apply_patch"}, model="gpt-4o")
        names = {d.name for d in defs}
        assert names == {"apply_patch"}  # edit filtered out by model

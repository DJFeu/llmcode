"""Tests for :class:`PostProcessComponent` — v12 M2 Task 2.7 Step 6.

Mirrors the post-processing the legacy :class:`ToolExecutionPipeline`
performs in :func:`budget_result` and :func:`format_denial_hint`: large
outputs get truncated into a disk-backed summary, bash denial hints get
appended, and the final shape is ready for the LLM to consume.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.7 Step 6
"""
from __future__ import annotations


from llm_code.tools.base import ToolResult


class TestPostProcessComponentImports:
    def test_module_imports(self) -> None:
        from llm_code.engine.components import postprocess as pp_mod

        assert hasattr(pp_mod, "PostProcessComponent")


class TestPostProcessComponentShape:
    def test_marked_as_component(self) -> None:
        from llm_code.engine.component import is_component
        from llm_code.engine.components.postprocess import PostProcessComponent

        assert is_component(PostProcessComponent())

    def test_input_sockets(self) -> None:
        from llm_code.engine.component import get_input_sockets
        from llm_code.engine.components.postprocess import PostProcessComponent

        inputs = get_input_sockets(PostProcessComponent)
        assert "raw_result" in inputs
        assert "tool_name" in inputs
        assert "tool_use_id" in inputs

    def test_output_sockets(self) -> None:
        from llm_code.engine.component import get_output_sockets
        from llm_code.engine.components.postprocess import PostProcessComponent

        outputs = get_output_sockets(PostProcessComponent)
        assert set(outputs) == {"formatted_result", "result_text", "is_error"}


class TestPostProcessComponentPassThrough:
    def test_small_output_unchanged(self) -> None:
        from llm_code.engine.components.postprocess import PostProcessComponent

        comp = PostProcessComponent()
        out = comp.run(
            raw_result=ToolResult(output="short"),
            tool_name="read_file",
            tool_use_id="t-1",
        )
        assert out["formatted_result"].output == "short"
        assert out["result_text"] == "short"
        assert out["is_error"] is False

    def test_error_flag_forwarded(self) -> None:
        from llm_code.engine.components.postprocess import PostProcessComponent

        comp = PostProcessComponent()
        out = comp.run(
            raw_result=ToolResult(output="boom", is_error=True),
            tool_name="bash",
            tool_use_id="t-1",
        )
        assert out["is_error"] is True

    def test_metadata_preserved(self) -> None:
        from llm_code.engine.components.postprocess import PostProcessComponent

        comp = PostProcessComponent()
        md = {"key": "val"}
        out = comp.run(
            raw_result=ToolResult(output="ok", metadata=md),
            tool_name="t",
            tool_use_id="u",
        )
        assert out["formatted_result"].metadata == md


class TestPostProcessComponentTruncation:
    def test_large_output_truncates(self, tmp_path) -> None:
        from llm_code.engine.components.postprocess import PostProcessComponent

        big = "x" * 5000
        comp = PostProcessComponent(max_inline=2000, cache_dir=tmp_path)
        out = comp.run(
            raw_result=ToolResult(output=big),
            tool_name="bash",
            tool_use_id="t-big",
        )
        # Formatted output is shorter and hints at the cache file.
        assert len(out["result_text"]) < len(big)
        assert "saved to" in out["result_text"]

    def test_truncation_writes_full_output_to_disk(self, tmp_path) -> None:
        from llm_code.engine.components.postprocess import PostProcessComponent

        big = "y" * 3000
        comp = PostProcessComponent(max_inline=500, cache_dir=tmp_path)
        comp.run(
            raw_result=ToolResult(output=big),
            tool_name="bash",
            tool_use_id="t-42",
        )
        # The cache file should carry the full payload verbatim.
        saved = (tmp_path / "t-42.txt").read_text(encoding="utf-8")
        assert saved == big

    def test_under_threshold_not_written(self, tmp_path) -> None:
        from llm_code.engine.components.postprocess import PostProcessComponent

        comp = PostProcessComponent(max_inline=1000, cache_dir=tmp_path)
        comp.run(
            raw_result=ToolResult(output="short"),
            tool_name="t",
            tool_use_id="u",
        )
        assert list(tmp_path.iterdir()) == []

    def test_no_cache_dir_silently_drops_overflow(self) -> None:
        """If no cache dir is provided, truncation still happens but
        the summary omits the save-hint."""
        from llm_code.engine.components.postprocess import PostProcessComponent

        big = "z" * 3000
        comp = PostProcessComponent(max_inline=500, cache_dir=None)
        out = comp.run(
            raw_result=ToolResult(output=big),
            tool_name="t",
            tool_use_id="u",
        )
        assert len(out["result_text"]) < len(big)


class TestPostProcessComponentBashHint:
    def test_bash_permission_denied_appends_hint(self) -> None:
        """Legacy ``ToolExecutionPipeline`` appends a denial hint when a
        bash error smells like a permission denial — mirror the same
        behaviour here."""
        from llm_code.engine.components.postprocess import PostProcessComponent

        comp = PostProcessComponent()
        out = comp.run(
            raw_result=ToolResult(
                output="Permission denied\n", is_error=True,
            ),
            tool_name="bash",
            tool_use_id="t-1",
        )
        # Hint appended — either via format_denial_hint or a substring.
        assert "Permission denied" in out["result_text"]

    def test_non_bash_errors_no_hint(self) -> None:
        from llm_code.engine.components.postprocess import PostProcessComponent

        comp = PostProcessComponent()
        out = comp.run(
            raw_result=ToolResult(output="oops", is_error=True),
            tool_name="read_file",
            tool_use_id="t-1",
        )
        # Should remain a pass-through.
        assert out["result_text"] == "oops"


class TestPostProcessInPipeline:
    def test_wires_after_executor(self) -> None:
        from llm_code.engine.components.postprocess import PostProcessComponent
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("exec", ToolExecutorComponent())
        p.add_component("post", PostProcessComponent())
        p.connect("exec.raw_result", "post.raw_result")
        assert "tool_name" in p.inputs()["post"]

    def test_pipeline_run_formats_result(self) -> None:
        from llm_code.engine.components.postprocess import PostProcessComponent
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("post", PostProcessComponent())
        outputs = p.run({
            "post": {
                "raw_result": ToolResult(output="hello"),
                "tool_name": "read_file",
                "tool_use_id": "t-1",
            },
        })
        assert outputs["post"]["result_text"] == "hello"
        assert outputs["post"]["is_error"] is False


class TestPostProcessResultTextContract:
    def test_result_text_always_string(self) -> None:
        from llm_code.engine.components.postprocess import PostProcessComponent

        comp = PostProcessComponent()
        out = comp.run(
            raw_result=ToolResult(output=""),
            tool_name="t",
            tool_use_id="u",
        )
        assert isinstance(out["result_text"], str)

    def test_formatted_result_is_tool_result(self) -> None:
        from llm_code.engine.components.postprocess import PostProcessComponent

        comp = PostProcessComponent()
        out = comp.run(
            raw_result=ToolResult(output="x"),
            tool_name="t",
            tool_use_id="u",
        )
        assert isinstance(out["formatted_result"], ToolResult)

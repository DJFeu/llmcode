"""Tests for :mod:`llm_code.migrate.v12.rewriters.tool_pipeline_subclass`."""
from __future__ import annotations

import libcst as cst

from llm_code.migrate.v12.diagnostics import Diagnostics
from llm_code.migrate.v12.rewriters.tool_pipeline_subclass import (
    ToolPipelineSubclassRewriter,
)


def _rewrite(source: str) -> tuple[str, Diagnostics]:
    diag = Diagnostics()
    rewriter = ToolPipelineSubclassRewriter(diag)
    rewriter.set_path("foo.py")
    module = cst.parse_module(source).visit(rewriter)
    return module.code, diag


class TestSimpleSubclass:
    def test_pre_execute_renamed_to_run(self) -> None:
        src = (
            "from llm_code.runtime.tool_pipeline import ToolExecutionPipeline\n\n"
            "class MyPipeline(ToolExecutionPipeline):\n"
            "    def pre_execute(self, ctx):\n"
            "        return ctx\n"
        )
        new, diag = _rewrite(src)
        assert "@component" in new
        assert "class MyComponent:" in new
        assert "def run(self, ctx)" in new
        assert "ToolExecutionPipeline" not in new
        assert "from llm_code.engine import Pipeline, component" in new
        assert not diag.any()

    def test_post_process_renamed_when_only_hook(self) -> None:
        src = (
            "from llm_code.runtime.tool_pipeline import ToolExecutionPipeline\n\n"
            "class MyPipeline(ToolExecutionPipeline):\n"
            "    def post_process(self, result):\n"
            "        return result\n"
        )
        new, _ = _rewrite(src)
        assert "def run(self, result)" in new

    def test_both_hooks_keeps_post_process(self) -> None:
        src = (
            "from llm_code.runtime.tool_pipeline import ToolExecutionPipeline\n\n"
            "class MyPipeline(ToolExecutionPipeline):\n"
            "    def pre_execute(self, ctx):\n"
            "        return ctx\n"
            "    def post_process(self, ctx, r):\n"
            "        return r\n"
        )
        new, _ = _rewrite(src)
        # pre_execute becomes run, post_process stays (author merges manually).
        assert "def run(self, ctx)" in new
        assert "def post_process(self, ctx, r)" in new

    def test_register_function_appended(self) -> None:
        src = (
            "from llm_code.runtime.tool_pipeline import ToolExecutionPipeline\n\n"
            "class MyPipeline(ToolExecutionPipeline):\n"
            "    def pre_execute(self, ctx):\n"
            "        return ctx\n"
        )
        new, _ = _rewrite(src)
        assert "def register(pipeline: Pipeline) -> None:" in new
        assert 'pipeline.add_component("myComponent", MyComponent())' in new

    def test_preserves_docstring_on_class(self) -> None:
        src = (
            "from llm_code.runtime.tool_pipeline import ToolExecutionPipeline\n\n"
            "class MyPipeline(ToolExecutionPipeline):\n"
            '    """Custom legacy pipeline."""\n'
            "    def pre_execute(self, ctx):\n"
            "        return ctx\n"
        )
        new, _ = _rewrite(src)
        assert '"""Custom legacy pipeline."""' in new


class TestAliasedImport:
    def test_aliased_base_class_recognised(self) -> None:
        src = (
            "from llm_code.runtime.tool_pipeline import "
            "ToolExecutionPipeline as TEP\n\n"
            "class MyPipeline(TEP):\n"
            "    def pre_execute(self, ctx):\n"
            "        return ctx\n"
        )
        new, _ = _rewrite(src)
        assert "@component" in new
        assert "class MyComponent:" in new
        assert "TEP" not in new


class TestUnsupported:
    def test_multiple_inheritance_flagged(self) -> None:
        src = (
            "from llm_code.runtime.tool_pipeline import ToolExecutionPipeline\n\n"
            "class Mixin:\n"
            "    pass\n\n"
            "class MyPipeline(Mixin, ToolExecutionPipeline):\n"
            "    def pre_execute(self, ctx):\n"
            "        return ctx\n"
        )
        new, diag = _rewrite(src)
        assert "class MyPipeline(Mixin, ToolExecutionPipeline):" in new
        patterns = {e.pattern for e in diag.entries}
        assert "multiple_inheritance_with_legacy_pipeline" in patterns

    def test_self_class_metaprogramming_flagged(self) -> None:
        src = (
            "from llm_code.runtime.tool_pipeline import ToolExecutionPipeline\n\n"
            "class MyPipeline(ToolExecutionPipeline):\n"
            "    def pre_execute(self, ctx):\n"
            "        return self.__class__.helper(ctx)\n"
        )
        new, diag = _rewrite(src)
        # Class left untouched, diagnostic emitted.
        assert "class MyPipeline(ToolExecutionPipeline):" in new
        patterns = {e.pattern for e in diag.entries}
        assert "metaprogramming_on_self_class" in patterns

    def test_existing_register_flagged(self) -> None:
        src = (
            "from llm_code.runtime.tool_pipeline import ToolExecutionPipeline\n\n"
            "def register(pipeline):\n"
            "    pass\n\n"
            "class MyPipeline(ToolExecutionPipeline):\n"
            "    def pre_execute(self, ctx):\n"
            "        return ctx\n"
        )
        new, diag = _rewrite(src)
        patterns = {e.pattern for e in diag.entries}
        assert "existing_register_symbol" in patterns
        # The rewriter should not emit a *second* register() definition.
        assert new.count("def register(") == 1


class TestMultipleClasses:
    def test_two_pipelines_get_two_register_lines(self) -> None:
        src = (
            "from llm_code.runtime.tool_pipeline import ToolExecutionPipeline\n\n"
            "class APipeline(ToolExecutionPipeline):\n"
            "    def pre_execute(self, ctx):\n"
            "        return ctx\n\n"
            "class BPipeline(ToolExecutionPipeline):\n"
            "    def pre_execute(self, ctx):\n"
            "        return ctx\n"
        )
        new, _ = _rewrite(src)
        assert 'pipeline.add_component("aComponent", AComponent())' in new
        assert 'pipeline.add_component("bComponent", BComponent())' in new


class TestIdempotence:
    def test_second_run_is_noop(self) -> None:
        src = (
            "from llm_code.runtime.tool_pipeline import ToolExecutionPipeline\n\n"
            "class MyPipeline(ToolExecutionPipeline):\n"
            "    def pre_execute(self, ctx):\n"
            "        return ctx\n"
        )
        first, _ = _rewrite(src)
        second, diag = _rewrite(first)
        assert first == second
        assert not diag.any()


class TestRunAlreadyDefined:
    def test_class_with_existing_run_method_not_renamed(self) -> None:
        src = (
            "from llm_code.runtime.tool_pipeline import ToolExecutionPipeline\n\n"
            "class MyPipeline(ToolExecutionPipeline):\n"
            "    def run(self, ctx):\n"
            "        return ctx\n"
            "    def pre_execute(self, ctx):\n"
            "        return ctx\n"
        )
        new, _ = _rewrite(src)
        # Both methods survive; the class picks up @component and loses
        # the legacy base.
        assert "@component" in new
        assert "def run(self, ctx)" in new
        assert "def pre_execute(self, ctx)" in new

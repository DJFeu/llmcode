"""Tests for :class:`ToolExecutorComponent` — v12 M2 Task 2.7 Step 5.

Runs the actual ``tool.execute(args)`` call. Errors propagate to the
caller — retry / fallback logic belongs in the M3 Agent, not here.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.7 Step 5
"""
from __future__ import annotations

import pytest

from llm_code.tools.base import ToolResult


class _FakeTool:
    """Minimal tool stand-in for executor tests.

    We don't exercise the full :class:`Tool` ABC because the executor
    only calls :meth:`execute` — tracking call args keeps tests terse.
    """
    def __init__(self, name: str, result: ToolResult | None = None) -> None:
        self.name = name
        self._result = result or ToolResult(output=f"{name}-ok")
        self.calls: list[dict] = []

    def execute(self, args: dict) -> ToolResult:
        self.calls.append(dict(args))
        return self._result


class _RaisingTool(_FakeTool):
    """Always raises — simulates a tool implementation bug."""

    def __init__(self, exc: BaseException) -> None:
        super().__init__("raising")
        self._exc = exc

    def execute(self, args: dict) -> ToolResult:
        self.calls.append(dict(args))
        raise self._exc


class TestToolExecutorComponentImports:
    def test_module_imports(self) -> None:
        from llm_code.engine.components import tool_executor as te_mod

        assert hasattr(te_mod, "ToolExecutorComponent")


class TestToolExecutorComponentShape:
    def test_marked_as_component(self) -> None:
        from llm_code.engine.component import is_component
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )

        assert is_component(ToolExecutorComponent())

    def test_input_sockets(self) -> None:
        from llm_code.engine.component import get_input_sockets
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )

        inputs = get_input_sockets(ToolExecutorComponent)
        assert set(inputs) >= {
            "resolved_tool", "tool_args", "proceed", "cached_result",
        }

    def test_output_sockets(self) -> None:
        from llm_code.engine.component import get_output_sockets
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )

        outputs = get_output_sockets(ToolExecutorComponent)
        assert set(outputs) == {"raw_result", "executed"}


class TestToolExecutorComponentRun:
    def test_executes_resolved_tool(self) -> None:
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )

        tool = _FakeTool("bash", ToolResult(output="hello"))
        comp = ToolExecutorComponent()
        out = comp.run(
            proceed=True,
            resolved_tool=tool,
            tool_args={"cmd": "echo hi"},
            cached_result=None,
        )
        assert out["executed"] is True
        assert out["raw_result"].output == "hello"
        assert tool.calls == [{"cmd": "echo hi"}]

    def test_proceed_false_returns_error_result(self) -> None:
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )

        comp = ToolExecutorComponent()
        out = comp.run(
            proceed=False,
            resolved_tool=None,
            tool_args={},
            cached_result=None,
        )
        assert out["executed"] is False
        assert out["raw_result"].is_error is True

    def test_cached_result_bypasses_execution(self) -> None:
        """When ``cached_result`` is present the executor must not call
        ``tool.execute`` again."""
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )

        tool = _FakeTool("bash")
        cached = ToolResult(output="from-cache")
        comp = ToolExecutorComponent()
        out = comp.run(
            proceed=False,  # speculative hit sets proceed to False
            resolved_tool=tool,
            tool_args={"cmd": "x"},
            cached_result=cached,
        )
        assert out["raw_result"] is cached
        assert out["executed"] is False
        assert tool.calls == []

    def test_no_tool_and_no_cache_errors(self) -> None:
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )

        comp = ToolExecutorComponent()
        out = comp.run(
            proceed=True,
            resolved_tool=None,
            tool_args={},
            cached_result=None,
        )
        assert out["executed"] is False
        assert out["raw_result"].is_error is True
        assert "no tool" in out["raw_result"].output.lower()

    def test_tool_exception_propagates_by_default(self) -> None:
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )

        tool = _RaisingTool(RuntimeError("boom"))
        comp = ToolExecutorComponent()
        with pytest.raises(RuntimeError):
            comp.run(
                proceed=True,
                resolved_tool=tool,
                tool_args={},
                cached_result=None,
            )

    def test_tool_exception_catchable_mode(self) -> None:
        """Optional ``catch_errors=True`` converts exceptions into an
        error ToolResult — used by parity tests that compare shapes."""
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )

        tool = _RaisingTool(RuntimeError("boom"))
        comp = ToolExecutorComponent(catch_errors=True)
        out = comp.run(
            proceed=True,
            resolved_tool=tool,
            tool_args={},
            cached_result=None,
        )
        assert out["executed"] is False
        assert out["raw_result"].is_error is True
        assert "boom" in out["raw_result"].output

    def test_tool_args_forwarded_verbatim(self) -> None:
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )

        tool = _FakeTool("t")
        comp = ToolExecutorComponent()
        comp.run(
            proceed=True,
            resolved_tool=tool,
            tool_args={"a": 1, "b": [1, 2]},
            cached_result=None,
        )
        assert tool.calls[0] == {"a": 1, "b": [1, 2]}

    def test_empty_args_dict(self) -> None:
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )

        tool = _FakeTool("t")
        comp = ToolExecutorComponent()
        out = comp.run(
            proceed=True,
            resolved_tool=tool,
            tool_args={},
            cached_result=None,
        )
        assert out["executed"] is True

    def test_raw_result_is_tool_result_instance(self) -> None:
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )

        tool = _FakeTool("t", ToolResult(output="v", metadata={"a": 1}))
        comp = ToolExecutorComponent()
        out = comp.run(
            proceed=True,
            resolved_tool=tool,
            tool_args={},
            cached_result=None,
        )
        assert isinstance(out["raw_result"], ToolResult)
        assert out["raw_result"].metadata == {"a": 1}


class TestToolExecutorInPipeline:
    def test_wires_after_resolver(self) -> None:
        from llm_code.engine.components.deferred_tool_resolver import (
            DeferredToolResolverComponent,
        )
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )
        from llm_code.engine.pipeline import Pipeline

        class _R:
            def get(self, _):
                return None
        p = Pipeline()
        p.add_component("resolver", DeferredToolResolverComponent(_R()))
        p.add_component("exec", ToolExecutorComponent())
        p.connect("resolver.resolved_tool", "exec.resolved_tool")
        p.connect("resolver.proceed", "exec.proceed")
        assert "tool_args" in p.inputs()["exec"]

    def test_pipeline_run_executes(self) -> None:
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )
        from llm_code.engine.pipeline import Pipeline

        tool = _FakeTool("t", ToolResult(output="done"))
        p = Pipeline()
        p.add_component("exec", ToolExecutorComponent())
        outputs = p.run({
            "exec": {
                "proceed": True,
                "resolved_tool": tool,
                "tool_args": {"x": 1},
                "cached_result": None,
            },
        })
        assert outputs["exec"]["raw_result"].output == "done"
        assert outputs["exec"]["executed"] is True


class TestToolExecutorErrorShape:
    def test_error_when_gate_denies(self) -> None:
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )

        comp = ToolExecutorComponent()
        out = comp.run(
            proceed=False,
            resolved_tool=None,
            tool_args={},
            cached_result=None,
        )
        assert out["raw_result"].is_error is True
        assert out["raw_result"].output  # non-empty

    def test_error_result_contains_metadata_key(self) -> None:
        """Synthetic error ToolResults carry a metadata marker so
        observability layers can split real tool errors from gate
        denials."""
        from llm_code.engine.components.tool_executor import (
            ToolExecutorComponent,
        )

        comp = ToolExecutorComponent()
        out = comp.run(
            proceed=False,
            resolved_tool=None,
            tool_args={},
            cached_result=None,
        )
        md = out["raw_result"].metadata or {}
        assert md.get("source") == "tool_executor_gate"

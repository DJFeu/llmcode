"""M2 — Pipeline class tests (Task 2.3).

Tests for `Pipeline.add_component`, `connect`, `run`, `validate`,
`inputs`, `outputs`, `to_dot`. Also covers `state_reads/writes`
conflict detection via `Pipeline.validate`.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.2
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.3 + 2.5
"""
from __future__ import annotations

from typing import Any

import pytest

from llm_code.engine.component import (
    component,
    output_types,
    state_reads,
    state_writes,
)


# ---- test doubles ---------------------------------------------------------


@component
@output_types(sum=int)
class _Adder:
    def run(self, a: int, b: int) -> dict:
        return {"sum": a + b}


@component
@output_types(doubled=int)
class _Doubler:
    def run(self, x: int) -> dict:
        return {"doubled": x * 2}


@component
@output_types(printed=str)
class _Stringifier:
    def run(self, x: int) -> dict:
        return {"printed": str(x)}


@component
@output_types(result=int)
class _Echo:
    def run(self, x: int) -> dict:
        return {"result": x}


# Subclass fixtures at module scope so `get_type_hints` can resolve them.
class _Animal:
    pass


class _Dog(_Animal):
    pass


@component
@output_types(out=_Dog)
class _DogProducer:
    def run(self) -> dict:
        return {"out": _Dog()}


@component
@output_types(out=_Animal)
class _AnimalProducer:
    def run(self) -> dict:
        return {"out": _Animal()}


@component
@output_types(done=bool)
class _AnimalConsumer:
    def run(self, a: _Animal) -> dict:
        return {"done": isinstance(a, _Animal)}


@component
@output_types(done=bool)
class _DogConsumer:
    def run(self, d: _Dog) -> dict:
        return {"done": True}


def _plain_object() -> Any:
    class NotAComponent:
        def run(self, x: int) -> dict:
            return {"y": x}

    return NotAComponent()


# ---- module wiring --------------------------------------------------------


class TestPipelineModuleImports:
    def test_pipeline_module_importable(self) -> None:
        from llm_code.engine import pipeline as pipe_mod

        assert hasattr(pipe_mod, "Pipeline")
        assert hasattr(pipe_mod, "SocketMismatchError")

    def test_pipeline_class_importable(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        assert Pipeline is not None

    def test_socket_mismatch_error_is_value_error(self) -> None:
        from llm_code.engine.pipeline import SocketMismatchError

        assert issubclass(SocketMismatchError, ValueError)


# ---- add_component --------------------------------------------------------


class TestAddComponent:
    def test_add_component_registers_by_name(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("add", _Adder())
        # Name appears in topological order after validate.
        assert "add" in p._components  # noqa: SLF001 (test introspection)

    def test_add_component_duplicate_name_raises(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("add", _Adder())
        with pytest.raises(ValueError):
            p.add_component("add", _Adder())

    def test_add_component_rejects_non_component(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        with pytest.raises(TypeError):
            p.add_component("plain", _plain_object())

    def test_add_multiple_components(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        p.add_component("s", _Stringifier())
        assert set(p._components.keys()) == {"a", "d", "s"}  # noqa: SLF001


# ---- connect --------------------------------------------------------------


class TestConnect:
    def test_connect_compatible_sockets(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        p.connect("a.sum", "d.x")
        # internal: connection recorded.
        assert ("a", "sum", "d", "x") in p._connections  # noqa: SLF001

    def test_connect_incompatible_types_raises(self) -> None:
        from llm_code.engine.pipeline import Pipeline, SocketMismatchError

        @component
        @output_types(out=str)
        class _StrSource:
            def run(self, x: int) -> dict:
                return {"out": str(x)}

        p = Pipeline()
        p.add_component("src", _StrSource())
        p.add_component("dst", _Doubler())  # expects int x
        with pytest.raises(SocketMismatchError):
            p.connect("src.out", "dst.x")

    def test_connect_parses_component_and_socket(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        p.connect("a.sum", "d.x")
        src_name, src_sock, dst_name, dst_sock = p._connections[0]  # noqa: SLF001
        assert src_name == "a"
        assert src_sock == "sum"
        assert dst_name == "d"
        assert dst_sock == "x"

    def test_connect_missing_dot_raises(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        with pytest.raises(ValueError):
            p.connect("a_no_dot", "d.x")

    def test_connect_unknown_source_component_raises(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("d", _Doubler())
        with pytest.raises(KeyError):
            p.connect("missing.sum", "d.x")

    def test_connect_unknown_dest_component_raises(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        with pytest.raises(KeyError):
            p.connect("a.sum", "missing.x")

    def test_connect_unknown_source_socket_raises(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        with pytest.raises(KeyError):
            p.connect("a.nonexistent", "d.x")

    def test_connect_unknown_dest_socket_raises(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        with pytest.raises(KeyError):
            p.connect("a.sum", "d.nonexistent")

    def test_connect_any_source_ok(self) -> None:
        """Any → concrete type is accepted (lenient check)."""
        from llm_code.engine.pipeline import Pipeline

        @component
        @output_types(out=Any)
        class _AnySource:
            def run(self) -> dict:
                return {"out": 42}

        p = Pipeline()
        p.add_component("src", _AnySource())
        p.add_component("d", _Doubler())
        p.connect("src.out", "d.x")  # no raise

    def test_connect_any_dest_ok(self) -> None:
        """Concrete → Any is accepted."""
        from llm_code.engine.pipeline import Pipeline

        @component
        @output_types(done=bool)
        class _AnySink:
            def run(self, anything: Any) -> dict:
                return {"done": True}

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("sink", _AnySink())
        p.connect("a.sum", "sink.anything")


# ---- run ------------------------------------------------------------------


class TestPipelineRun:
    def test_run_empty_pipeline_returns_empty(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        assert p.run({}) == {}

    def test_run_single_component_with_entry_inputs(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("d", _Doubler())
        out = p.run({"d": {"x": 5}})
        assert out == {"d": {"doubled": 10}}

    def test_run_linear_chain(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        p.connect("a.sum", "d.x")
        out = p.run({"a": {"a": 2, "b": 3}})
        assert out["a"]["sum"] == 5
        assert out["d"]["doubled"] == 10

    def test_run_three_stage_chain(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        p.add_component("s", _Stringifier())
        p.connect("a.sum", "d.x")
        p.connect("d.doubled", "s.x")
        out = p.run({"a": {"a": 4, "b": 6}})
        assert out["a"]["sum"] == 10
        assert out["d"]["doubled"] == 20
        assert out["s"]["printed"] == "20"

    def test_run_diamond_topology(self) -> None:
        """a → b, a → c, b+c → d.

        `_Adder` takes two inputs; we feed its a= from b, b= from c.
        """
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("echo", _Echo())
        p.add_component("b", _Doubler())
        p.add_component("c", _Doubler())
        p.add_component("merge", _Adder())
        p.connect("echo.result", "b.x")
        p.connect("echo.result", "c.x")
        p.connect("b.doubled", "merge.a")
        p.connect("c.doubled", "merge.b")
        out = p.run({"echo": {"x": 3}})
        assert out["echo"]["result"] == 3
        assert out["b"]["doubled"] == 6
        assert out["c"]["doubled"] == 6
        assert out["merge"]["sum"] == 12

    def test_run_raises_on_component_exception(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        @component
        @output_types(v=int)
        class _Boom:
            def run(self, x: int) -> dict:
                raise RuntimeError("boom")

        p = Pipeline()
        p.add_component("boom", _Boom())
        with pytest.raises(RuntimeError, match="boom"):
            p.run({"boom": {"x": 1}})

    def test_run_uses_component_default_when_no_input(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        @component
        @output_types(v=int)
        class _WithDefault:
            def run(self, x: int = 42) -> dict:
                return {"v": x}

        p = Pipeline()
        p.add_component("d", _WithDefault())
        out = p.run({})  # no entry inputs → uses default
        assert out["d"]["v"] == 42

    def test_run_entry_inputs_override_default(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        @component
        @output_types(v=int)
        class _WithDefault:
            def run(self, x: int = 42) -> dict:
                return {"v": x}

        p = Pipeline()
        p.add_component("d", _WithDefault())
        out = p.run({"d": {"x": 100}})
        assert out["d"]["v"] == 100

    def test_run_respects_topological_order(self) -> None:
        """Execution order must respect dependencies.

        Uses a side-effect list to record execution order.
        """
        from llm_code.engine.pipeline import Pipeline

        order: list[str] = []

        @component
        @output_types(v=int)
        class _Recorder:
            def __init__(self, name: str) -> None:
                self._name = name

            def run(self, x: int = 0) -> dict:
                order.append(self._name)
                return {"v": x + 1}

        p = Pipeline()
        p.add_component("first", _Recorder("first"))
        p.add_component("second", _Recorder("second"))
        p.add_component("third", _Recorder("third"))
        p.connect("first.v", "second.x")
        p.connect("second.v", "third.x")
        p.run({"first": {"x": 0}})
        assert order == ["first", "second", "third"]

    def test_run_cycle_raises(self) -> None:
        """A pipeline with a cycle cannot be run."""
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Echo())
        p.add_component("b", _Echo())
        p.connect("a.result", "b.x")
        p.connect("b.result", "a.x")
        with pytest.raises(RuntimeError, match="cycle"):
            p.run({})

    def test_run_passes_multiple_inputs(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        out = p.run({"a": {"a": 7, "b": 11}})
        assert out["a"]["sum"] == 18


# ---- validate -------------------------------------------------------------


class TestValidate:
    def test_validate_empty_pipeline_ok(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.validate()  # no raise

    def test_validate_linear_pipeline_ok(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        p.connect("a.sum", "d.x")
        p.validate()

    def test_validate_detects_cycle(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Echo())
        p.add_component("b", _Echo())
        p.connect("a.result", "b.x")
        p.connect("b.result", "a.x")
        with pytest.raises(RuntimeError):
            p.validate()

    def test_validate_detects_state_write_conflict(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        @component
        @state_writes("iteration")
        @output_types(v=int)
        class _WriterA:
            def run(self) -> dict:
                return {"v": 1}

        @component
        @state_writes("iteration")
        @output_types(v=int)
        class _WriterB:
            def run(self) -> dict:
                return {"v": 2}

        p = Pipeline()
        p.add_component("wa", _WriterA())
        p.add_component("wb", _WriterB())
        with pytest.raises(ValueError, match="iteration"):
            p.validate()

    def test_validate_allows_disjoint_state_writes(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        @component
        @state_writes("iteration")
        @output_types(v=int)
        class _WriterA:
            def run(self) -> dict:
                return {"v": 1}

        @component
        @state_writes("tool_calls")
        @output_types(v=int)
        class _WriterB:
            def run(self) -> dict:
                return {"v": 2}

        p = Pipeline()
        p.add_component("wa", _WriterA())
        p.add_component("wb", _WriterB())
        p.validate()  # no raise

    def test_validate_allows_state_reads_overlap(self) -> None:
        """Multiple components reading the same key is fine."""
        from llm_code.engine.pipeline import Pipeline

        @component
        @state_reads("messages")
        @output_types(v=int)
        class _ReaderA:
            def run(self) -> dict:
                return {"v": 1}

        @component
        @state_reads("messages")
        @output_types(v=int)
        class _ReaderB:
            def run(self) -> dict:
                return {"v": 2}

        p = Pipeline()
        p.add_component("ra", _ReaderA())
        p.add_component("rb", _ReaderB())
        p.validate()

    def test_validate_error_names_both_conflicting_components(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        @component
        @state_writes("iteration")
        @output_types(v=int)
        class _WriterA:
            def run(self) -> dict:
                return {"v": 1}

        @component
        @state_writes("iteration")
        @output_types(v=int)
        class _WriterB:
            def run(self) -> dict:
                return {"v": 2}

        p = Pipeline()
        p.add_component("wa", _WriterA())
        p.add_component("wb", _WriterB())
        with pytest.raises(ValueError) as exc:
            p.validate()
        msg = str(exc.value)
        # Both component names should appear for good error UX.
        assert "wa" in msg
        assert "wb" in msg


# ---- inputs / outputs -----------------------------------------------------


class TestInputsOutputs:
    def test_inputs_empty_pipeline(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        assert p.inputs() == {}

    def test_outputs_empty_pipeline(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        assert p.outputs() == {}

    def test_inputs_lists_entry_points(self) -> None:
        """Only sockets with no incoming connection are entry points."""
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        p.connect("a.sum", "d.x")
        ins = p.inputs()
        # `a.a`, `a.b` are entry; `d.x` is fed internally.
        assert "a" in ins
        assert set(ins["a"].keys()) == {"a", "b"}
        assert "d" not in ins or "x" not in ins.get("d", {})

    def test_outputs_lists_exit_points(self) -> None:
        """Output sockets with no outgoing connection are exit points."""
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        p.connect("a.sum", "d.x")
        outs = p.outputs()
        # `d.doubled` has no outgoing edge → exit.
        assert "d" in outs
        assert "doubled" in outs["d"]
        # `a.sum` is consumed → not exit.
        assert "a" not in outs or "sum" not in outs.get("a", {})

    def test_inputs_single_component(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        ins = p.inputs()
        assert set(ins["a"].keys()) == {"a", "b"}

    def test_outputs_single_component(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        outs = p.outputs()
        assert set(outs["a"].keys()) == {"sum"}


# ---- to_dot ---------------------------------------------------------------


class TestToDot:
    def test_to_dot_empty_pipeline(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        dot = p.to_dot()
        assert isinstance(dot, str)
        assert "digraph" in dot

    def test_to_dot_contains_component_names(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("alpha", _Adder())
        p.add_component("beta", _Doubler())
        p.connect("alpha.sum", "beta.x")
        dot = p.to_dot()
        assert "alpha" in dot
        assert "beta" in dot

    def test_to_dot_contains_edge(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("src", _Adder())
        p.add_component("dst", _Doubler())
        p.connect("src.sum", "dst.x")
        dot = p.to_dot()
        assert "->" in dot


# ---- state reads/writes decorators ---------------------------------------


class TestStateWriteConflictInPipeline:
    def test_same_component_instance_allowed(self) -> None:
        """Same class added twice under different names: still conflicts.

        Each instance declares the same `state_writes`; validate must
        detect it as a conflict (two distinct components writing the
        same key).
        """
        from llm_code.engine.pipeline import Pipeline

        @component
        @state_writes("mode")
        @output_types(v=int)
        class _Writer:
            def run(self) -> dict:
                return {"v": 1}

        p = Pipeline()
        p.add_component("w1", _Writer())
        p.add_component("w2", _Writer())
        with pytest.raises(ValueError):
            p.validate()

    def test_single_writer_ok(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        @component
        @state_writes("mode")
        @output_types(v=int)
        class _Writer:
            def run(self) -> dict:
                return {"v": 1}

        p = Pipeline()
        p.add_component("solo", _Writer())
        p.validate()

    def test_multiple_keys_one_conflict_detected(self) -> None:
        """Two writers share one key of many → conflict flagged."""
        from llm_code.engine.pipeline import Pipeline

        @component
        @state_writes("mode", "iteration")
        @output_types(v=int)
        class _WriterA:
            def run(self) -> dict:
                return {"v": 1}

        @component
        @state_writes("iteration", "degraded")
        @output_types(v=int)
        class _WriterB:
            def run(self) -> dict:
                return {"v": 2}

        p = Pipeline()
        p.add_component("wa", _WriterA())
        p.add_component("wb", _WriterB())
        with pytest.raises(ValueError, match="iteration"):
            p.validate()


# ---- more complex scenarios ----------------------------------------------


class TestComplexPipelines:
    def test_pipeline_with_five_components(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("start", _Echo())
        p.add_component("d1", _Doubler())
        p.add_component("d2", _Doubler())
        p.add_component("d3", _Doubler())
        p.add_component("stringify", _Stringifier())
        p.connect("start.result", "d1.x")
        p.connect("d1.doubled", "d2.x")
        p.connect("d2.doubled", "d3.x")
        p.connect("d3.doubled", "stringify.x")
        out = p.run({"start": {"x": 1}})
        assert out["stringify"]["printed"] == "8"

    def test_component_fanout_to_multiple_consumers(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("src", _Echo())
        p.add_component("d", _Doubler())
        p.add_component("s", _Stringifier())
        p.connect("src.result", "d.x")
        p.connect("src.result", "s.x")
        out = p.run({"src": {"x": 7}})
        assert out["d"]["doubled"] == 14
        assert out["s"]["printed"] == "7"

    def test_two_disconnected_subgraphs_run_independently(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("g1_src", _Echo())
        p.add_component("g1_d", _Doubler())
        p.add_component("g2_src", _Echo())
        p.add_component("g2_d", _Doubler())
        p.connect("g1_src.result", "g1_d.x")
        p.connect("g2_src.result", "g2_d.x")
        out = p.run({"g1_src": {"x": 2}, "g2_src": {"x": 5}})
        assert out["g1_d"]["doubled"] == 4
        assert out["g2_d"]["doubled"] == 10

    def test_pipeline_add_and_connect_returns_none(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        assert p.add_component("a", _Adder()) is None
        assert p.add_component("d", _Doubler()) is None
        assert p.connect("a.sum", "d.x") is None

    def test_rerun_pipeline_same_result(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        p.connect("a.sum", "d.x")
        out1 = p.run({"a": {"a": 1, "b": 2}})
        out2 = p.run({"a": {"a": 1, "b": 2}})
        assert out1 == out2

    def test_pipeline_rerun_different_entry(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        p.connect("a.sum", "d.x")
        out1 = p.run({"a": {"a": 1, "b": 2}})
        out2 = p.run({"a": {"a": 10, "b": 20}})
        assert out1["d"]["doubled"] == 6
        assert out2["d"]["doubled"] == 60


class TestMissingRequiredInputs:
    def test_run_missing_required_input_raises_type_error(self) -> None:
        """A required input with no entry input and no upstream connection
        fails when the component is actually called. We let Python's
        standard TypeError surface from the call site."""
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        # `_Adder.run(a, b)` is called with no args.
        with pytest.raises(TypeError):
            p.run({})


# ---- edge cases ----------------------------------------------------------


class TestPipelineEdgeCases:
    def test_connect_does_not_allow_double_connect_same_pair(self) -> None:
        """Connecting the same src→dst edge twice records both entries.

        The graph's in-degree remains correct (edge deduplication in
        Graph), but the connection list accumulates for parity with
        Haystack's behaviour. Only resolution-side matters.
        """
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        p.connect("a.sum", "d.x")
        # Second connect of same pair still works; idempotent on graph.
        p.connect("a.sum", "d.x")
        # Pipeline still runnable.
        out = p.run({"a": {"a": 1, "b": 1}})
        assert out["d"]["doubled"] == 4

    def test_pipeline_with_isolated_component(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("alone", _Echo())
        out = p.run({"alone": {"x": 99}})
        assert out["alone"]["result"] == 99

    def test_pipeline_with_many_entry_sockets(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        @component
        @output_types(total=int)
        class _Summer:
            def run(self, a: int, b: int, c: int) -> dict:
                return {"total": a + b + c}

        p = Pipeline()
        p.add_component("sum", _Summer())
        out = p.run({"sum": {"a": 1, "b": 2, "c": 3}})
        assert out["sum"]["total"] == 6

    def test_pipeline_input_type_with_subclass_compatible(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("prod", _DogProducer())
        p.add_component("cons", _AnimalConsumer())
        p.connect("prod.out", "cons.a")  # Dog → Animal ok
        out = p.run({})
        assert out["cons"]["done"] is True

    def test_pipeline_input_type_with_superclass_rejected(self) -> None:
        from llm_code.engine.pipeline import Pipeline, SocketMismatchError

        p = Pipeline()
        p.add_component("prod", _AnimalProducer())
        p.add_component("cons", _DogConsumer())
        # Animal → Dog is narrowing → reject.
        with pytest.raises(SocketMismatchError):
            p.connect("prod.out", "cons.d")


class TestToDotAdvanced:
    def test_to_dot_starts_with_digraph(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        dot = p.to_dot()
        assert dot.strip().startswith("digraph")

    def test_to_dot_ends_with_closing_brace(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        dot = p.to_dot()
        assert dot.rstrip().endswith("}")

    def test_to_dot_includes_edge_for_each_connection(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("src", _Adder())
        p.add_component("mid", _Doubler())
        p.add_component("dst", _Stringifier())
        p.connect("src.sum", "mid.x")
        p.connect("mid.doubled", "dst.x")
        dot = p.to_dot()
        # Each edge renders as "src" -> "mid" style.
        assert dot.count("->") >= 2


class TestInputsOutputsExtra:
    def test_outputs_detects_fanout_leaves(self) -> None:
        """When a socket feeds multiple downstream sockets, it's not a
        leaf output."""
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("src", _Echo())
        p.add_component("a", _Doubler())
        p.add_component("b", _Doubler())
        p.connect("src.result", "a.x")
        p.connect("src.result", "b.x")
        outs = p.outputs()
        # `src.result` is consumed → not leaf.
        assert "src" not in outs or "result" not in outs.get("src", {})
        # `a.doubled` and `b.doubled` are leaves.
        assert "doubled" in outs["a"]
        assert "doubled" in outs["b"]

    def test_inputs_optional_sockets_included(self) -> None:
        """Optional (default-valued) sockets are still listed in
        entry inputs — callers may choose to pass them."""
        from llm_code.engine.pipeline import Pipeline

        @component
        @output_types(v=int)
        class _Opt:
            def run(self, x: int = 5, y: int = 10) -> dict:
                return {"v": x + y}

        p = Pipeline()
        p.add_component("opt", _Opt())
        ins = p.inputs()
        assert set(ins["opt"].keys()) == {"x", "y"}


class TestValidateExtra:
    def test_validate_cycle_via_three_nodes(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Echo())
        p.add_component("b", _Echo())
        p.add_component("c", _Echo())
        p.connect("a.result", "b.x")
        p.connect("b.result", "c.x")
        p.connect("c.result", "a.x")
        with pytest.raises(RuntimeError):
            p.validate()

    def test_validate_three_components_no_conflict(self) -> None:
        """No state decorators → no state conflict."""
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        p.add_component("s", _Stringifier())
        p.connect("a.sum", "d.x")
        p.connect("d.doubled", "s.x")
        p.validate()


class TestConnectionInternals:
    def test_multiple_connections_all_recorded(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("src", _Echo())
        p.add_component("a", _Doubler())
        p.add_component("b", _Doubler())
        p.connect("src.result", "a.x")
        p.connect("src.result", "b.x")
        assert len(p._connections) == 2  # noqa: SLF001
        dst_names = {c[2] for c in p._connections}  # noqa: SLF001
        assert dst_names == {"a", "b"}

    def test_pipeline_run_returns_dict_per_component(self) -> None:
        from llm_code.engine.pipeline import Pipeline

        p = Pipeline()
        p.add_component("a", _Adder())
        p.add_component("d", _Doubler())
        p.connect("a.sum", "d.x")
        out = p.run({"a": {"a": 1, "b": 2}})
        assert isinstance(out, dict)
        assert isinstance(out["a"], dict)
        assert isinstance(out["d"], dict)

"""M2 — Component decorator + Socket tests (Task 2.2).

Tests for `@component`, `Socket`, `@output_types`, `@state_reads`,
`@state_writes` and the helpers `is_component`, `get_input_sockets`,
`get_output_sockets`.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.2
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.2 + 2.5
"""
from __future__ import annotations

import dataclasses
from typing import Any

import pytest


class TestComponentModuleImports:
    def test_component_module_importable(self) -> None:
        from llm_code.engine import component as comp_mod

        assert hasattr(comp_mod, "component")
        assert hasattr(comp_mod, "Socket")
        assert hasattr(comp_mod, "output_types")
        assert hasattr(comp_mod, "is_component")
        assert hasattr(comp_mod, "get_input_sockets")
        assert hasattr(comp_mod, "get_output_sockets")
        assert hasattr(comp_mod, "state_reads")
        assert hasattr(comp_mod, "state_writes")


class TestSocket:
    def test_socket_is_frozen_dataclass(self) -> None:
        from llm_code.engine.component import Socket

        assert dataclasses.is_dataclass(Socket)
        # `frozen=True` means assignment raises FrozenInstanceError.
        s = Socket(name="x", type=int, direction="input")
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.name = "y"  # type: ignore[misc]

    def test_socket_fields(self) -> None:
        from llm_code.engine.component import Socket

        s = Socket(name="foo", type=str, direction="input")
        assert s.name == "foo"
        assert s.type is str
        assert s.direction == "input"
        assert s.required is True
        assert s.default is None

    def test_socket_with_default(self) -> None:
        from llm_code.engine.component import Socket

        s = Socket(name="foo", type=int, direction="input", required=False, default=42)
        assert s.required is False
        assert s.default == 42

    def test_socket_output_direction(self) -> None:
        from llm_code.engine.component import Socket

        s = Socket(name="out", type=bool, direction="output")
        assert s.direction == "output"

    def test_socket_equality(self) -> None:
        from llm_code.engine.component import Socket

        a = Socket(name="x", type=int, direction="input")
        b = Socket(name="x", type=int, direction="input")
        assert a == b

    def test_socket_hashable(self) -> None:
        from llm_code.engine.component import Socket

        s = Socket(name="x", type=int, direction="input")
        assert hash(s) is not None


class TestComponentDecoratorBasics:
    def test_component_marks_class(self) -> None:
        from llm_code.engine.component import component

        @component
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert getattr(Foo, "__is_component__", False) is True

    def test_component_requires_run_method(self) -> None:
        from llm_code.engine.component import component

        with pytest.raises(TypeError):
            @component
            class NoRun:  # no run method
                pass

    def test_component_introspects_simple_inputs(self) -> None:
        from llm_code.engine.component import component

        @component
        class Adder:
            def run(self, a: int, b: int) -> dict:
                return {"sum": a + b}

        assert "a" in Adder.__component_inputs__
        assert "b" in Adder.__component_inputs__
        assert Adder.__component_inputs__["a"].type is int
        assert Adder.__component_inputs__["b"].type is int

    def test_component_skips_self_param(self) -> None:
        from llm_code.engine.component import component

        @component
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert "self" not in Foo.__component_inputs__

    def test_component_marks_required_without_default(self) -> None:
        from llm_code.engine.component import component

        @component
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert Foo.__component_inputs__["x"].required is True

    def test_component_marks_optional_with_default(self) -> None:
        from llm_code.engine.component import component

        @component
        class Foo:
            def run(self, x: int = 5) -> dict:
                return {"y": x}

        assert Foo.__component_inputs__["x"].required is False
        assert Foo.__component_inputs__["x"].default == 5

    def test_component_input_direction_is_input(self) -> None:
        from llm_code.engine.component import component

        @component
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert Foo.__component_inputs__["x"].direction == "input"

    def test_component_untyped_param_becomes_any(self) -> None:
        from llm_code.engine.component import component

        @component
        class Foo:
            def run(self, x):  # no annotation
                return {"y": x}

        assert Foo.__component_inputs__["x"].type is Any

    def test_component_empty_outputs_by_default(self) -> None:
        from llm_code.engine.component import component

        @component
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert Foo.__component_outputs__ == {}

    def test_component_preserves_class_identity(self) -> None:
        from llm_code.engine.component import component

        @component
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

            def helper(self) -> str:
                return "hello"

        f = Foo()
        assert f.run(1) == {"y": 1}
        assert f.helper() == "hello"

    def test_component_class_name_preserved(self) -> None:
        from llm_code.engine.component import component

        @component
        class FooBar:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert FooBar.__name__ == "FooBar"


class TestOutputTypes:
    def test_output_types_sets_outputs(self) -> None:
        from llm_code.engine.component import component, output_types

        @component
        @output_types(allowed=bool, reason=str)
        class Foo:
            def run(self, x: int) -> dict:
                return {"allowed": True, "reason": ""}

        assert "allowed" in Foo.__component_outputs__
        assert "reason" in Foo.__component_outputs__
        assert Foo.__component_outputs__["allowed"].type is bool
        assert Foo.__component_outputs__["reason"].type is str

    def test_output_types_direction_is_output(self) -> None:
        from llm_code.engine.component import component, output_types

        @component
        @output_types(allowed=bool)
        class Foo:
            def run(self, x: int) -> dict:
                return {"allowed": True}

        assert Foo.__component_outputs__["allowed"].direction == "output"

    def test_output_types_marks_required(self) -> None:
        from llm_code.engine.component import component, output_types

        @component
        @output_types(result=int)
        class Foo:
            def run(self, x: int) -> dict:
                return {"result": x * 2}

        assert Foo.__component_outputs__["result"].required is True

    def test_output_types_multiple_outputs(self) -> None:
        from llm_code.engine.component import component, output_types

        @component
        @output_types(a=int, b=str, c=bool)
        class Triple:
            def run(self, x: int) -> dict:
                return {"a": x, "b": str(x), "c": bool(x)}

        assert set(Triple.__component_outputs__.keys()) == {"a", "b", "c"}

    def test_output_types_empty(self) -> None:
        from llm_code.engine.component import component, output_types

        @component
        @output_types()
        class Noop:
            def run(self, x: int) -> dict:
                return {}

        assert Noop.__component_outputs__ == {}

    def test_output_types_reverse_order_also_works(self) -> None:
        """Applying @component before @output_types still yields outputs."""
        from llm_code.engine.component import component, output_types

        @output_types(y=int)
        @component
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert "y" in Foo.__component_outputs__


class TestHelpers:
    def test_is_component_true_for_decorated(self) -> None:
        from llm_code.engine.component import component, is_component

        @component
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert is_component(Foo) is True
        assert is_component(Foo()) is True

    def test_is_component_false_for_plain_class(self) -> None:
        from llm_code.engine.component import is_component

        class Plain:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert is_component(Plain) is False
        assert is_component(Plain()) is False

    def test_is_component_false_for_non_class_objects(self) -> None:
        from llm_code.engine.component import is_component

        assert is_component(42) is False
        assert is_component("hello") is False
        assert is_component(None) is False

    def test_get_input_sockets(self) -> None:
        from llm_code.engine.component import component, get_input_sockets

        @component
        class Foo:
            def run(self, a: int, b: str) -> dict:
                return {}

        sockets = get_input_sockets(Foo)
        assert set(sockets.keys()) == {"a", "b"}

    def test_get_output_sockets(self) -> None:
        from llm_code.engine.component import component, get_output_sockets, output_types

        @component
        @output_types(y=int, z=str)
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x, "z": str(x)}

        sockets = get_output_sockets(Foo)
        assert set(sockets.keys()) == {"y", "z"}

    def test_get_sockets_on_plain_class_returns_empty(self) -> None:
        from llm_code.engine.component import get_input_sockets, get_output_sockets

        class Plain:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert get_input_sockets(Plain) == {}
        assert get_output_sockets(Plain) == {}

    def test_get_sockets_accepts_instance(self) -> None:
        from llm_code.engine.component import (
            component,
            get_input_sockets,
            get_output_sockets,
            output_types,
        )

        @component
        @output_types(y=int)
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        inst = Foo()
        assert set(get_input_sockets(inst).keys()) == {"x"}
        assert set(get_output_sockets(inst).keys()) == {"y"}


class TestStateDecorators:
    def test_state_reads_attaches_frozen_set(self) -> None:
        from llm_code.engine.component import state_reads

        @state_reads("messages", "iteration")
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert Foo.__state_reads__ == frozenset({"messages", "iteration"})

    def test_state_writes_attaches_frozen_set(self) -> None:
        from llm_code.engine.component import state_writes

        @state_writes("tool_calls", "denial_history")
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert Foo.__state_writes__ == frozenset({"tool_calls", "denial_history"})

    def test_state_reads_empty_when_undeclared(self) -> None:
        from llm_code.engine.component import component

        @component
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        # No __state_reads__ attribute on undecorated class.
        assert getattr(Foo, "__state_reads__", frozenset()) == frozenset()

    def test_state_writes_empty_when_undeclared(self) -> None:
        from llm_code.engine.component import component

        @component
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert getattr(Foo, "__state_writes__", frozenset()) == frozenset()

    def test_state_reads_with_no_keys_is_empty(self) -> None:
        from llm_code.engine.component import state_reads

        @state_reads()
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert Foo.__state_reads__ == frozenset()

    def test_state_writes_with_no_keys_is_empty(self) -> None:
        from llm_code.engine.component import state_writes

        @state_writes()
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert Foo.__state_writes__ == frozenset()

    def test_state_reads_can_combine_with_component(self) -> None:
        from llm_code.engine.component import component, state_reads

        @component
        @state_reads("mode")
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert Foo.__state_reads__ == frozenset({"mode"})
        assert getattr(Foo, "__is_component__", False) is True

    def test_state_writes_can_combine_with_component(self) -> None:
        from llm_code.engine.component import component, state_writes

        @component
        @state_writes("iteration")
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert Foo.__state_writes__ == frozenset({"iteration"})
        assert getattr(Foo, "__is_component__", False) is True

    def test_state_reads_writes_together(self) -> None:
        from llm_code.engine.component import component, state_reads, state_writes

        @component
        @state_reads("messages")
        @state_writes("tool_calls")
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        assert Foo.__state_reads__ == frozenset({"messages"})
        assert Foo.__state_writes__ == frozenset({"tool_calls"})


class TestComponentInstanceCheck:
    def test_instance_gets_component_attributes(self) -> None:
        from llm_code.engine.component import component, output_types

        @component
        @output_types(y=int)
        class Foo:
            def run(self, x: int) -> dict:
                return {"y": x}

        inst = Foo()
        assert getattr(inst, "__is_component__", False) is True
        # Instance attribute access falls back to class — it works.
        assert set(inst.__component_inputs__.keys()) == {"x"}
        assert set(inst.__component_outputs__.keys()) == {"y"}


class TestComponentWithInit:
    def test_component_with_constructor_args(self) -> None:
        from llm_code.engine.component import component, output_types

        @component
        @output_types(out=int)
        class Multiplier:
            def __init__(self, factor: int) -> None:
                self._factor = factor

            def run(self, x: int) -> dict:
                return {"out": x * self._factor}

        m = Multiplier(factor=3)
        assert m.run(4) == {"out": 12}
        assert "x" in Multiplier.__component_inputs__
        assert Multiplier.__component_outputs__["out"].type is int

    def test_component_with_no_inputs(self) -> None:
        from llm_code.engine.component import component, output_types

        @component
        @output_types(now=int)
        class Timestamp:
            def run(self) -> dict:
                return {"now": 42}

        assert Timestamp.__component_inputs__ == {}
        assert Timestamp.__component_outputs__["now"].type is int


class TestSocketRepr:
    def test_socket_repr_includes_name(self) -> None:
        from llm_code.engine.component import Socket

        s = Socket(name="foo", type=int, direction="input")
        assert "foo" in repr(s)

    def test_socket_repr_includes_direction(self) -> None:
        from llm_code.engine.component import Socket

        s = Socket(name="foo", type=int, direction="output")
        assert "output" in repr(s)


class TestComponentComplexTypes:
    def test_component_with_generic_hint_falls_back(self) -> None:
        """list[int] type hints are accepted (not validated here)."""
        from llm_code.engine.component import component

        @component
        class Foo:
            def run(self, xs: list) -> dict:
                return {"count": len(xs)}

        # The socket exists; exact type may be the origin `list` or the
        # raw generic depending on get_type_hints behaviour — we only
        # check presence.
        assert "xs" in Foo.__component_inputs__

    def test_component_with_optional_default_none(self) -> None:
        from llm_code.engine.component import component

        @component
        class Foo:
            def run(self, x: int = 0) -> dict:
                return {"y": x}

        socket = Foo.__component_inputs__["x"]
        assert socket.required is False
        assert socket.default == 0


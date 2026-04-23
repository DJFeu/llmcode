"""Tests for :class:`PromptAssemblerComponent` — v12 M2 Task 2.7 Step 7.

Thin wrapper over :class:`llm_code.engine.prompt_builder.PromptBuilder`
so the Pipeline DAG can include prompt assembly as a stage. The
Component keeps the PromptBuilder's dict-shaped API
(``{"prompt": "..."}``) so parity with existing callers is preserved.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.7 Step 7
"""
from __future__ import annotations

import pytest


class TestPromptAssemblerImports:
    def test_module_imports(self) -> None:
        from llm_code.engine.components import prompt_assembler as pa_mod

        assert hasattr(pa_mod, "PromptAssemblerComponent")


class TestPromptAssemblerShape:
    def test_marked_as_component(self) -> None:
        from llm_code.engine.component import is_component
        from llm_code.engine.components.prompt_assembler import (
            PromptAssemblerComponent,
        )
        from llm_code.engine.prompt_builder import PromptBuilder

        comp = PromptAssemblerComponent(PromptBuilder(template="hi"))
        assert is_component(comp)

    def test_output_sockets(self) -> None:
        from llm_code.engine.component import get_output_sockets
        from llm_code.engine.components.prompt_assembler import (
            PromptAssemblerComponent,
        )

        outputs = get_output_sockets(PromptAssemblerComponent)
        assert set(outputs) == {"prompt"}

    def test_input_socket_is_mapping(self) -> None:
        from llm_code.engine.component import get_input_sockets
        from llm_code.engine.components.prompt_assembler import (
            PromptAssemblerComponent,
        )

        inputs = get_input_sockets(PromptAssemblerComponent)
        assert "variables" in inputs


class TestPromptAssemblerRun:
    def test_renders_inline_template(self) -> None:
        from llm_code.engine.components.prompt_assembler import (
            PromptAssemblerComponent,
        )
        from llm_code.engine.prompt_builder import PromptBuilder

        pb = PromptBuilder(template="Hello {{ name }}!")
        comp = PromptAssemblerComponent(pb)
        out = comp.run(variables={"name": "world"})
        assert out["prompt"] == "Hello world!"

    def test_missing_variable_raises(self) -> None:
        from llm_code.engine.components.prompt_assembler import (
            PromptAssemblerComponent,
        )
        from llm_code.engine.prompt_builder import PromptBuilder
        from jinja2 import UndefinedError

        pb = PromptBuilder(template="Hi {{ x }}")
        comp = PromptAssemblerComponent(pb)
        with pytest.raises(UndefinedError):
            comp.run(variables={})

    def test_required_variable_check(self) -> None:
        from llm_code.engine.components.prompt_assembler import (
            PromptAssemblerComponent,
        )
        from llm_code.engine.prompt_builder import PromptBuilder

        pb = PromptBuilder(template="Hi {{ x }}", required_variables=["x"])
        comp = PromptAssemblerComponent(pb)
        with pytest.raises(ValueError):
            comp.run(variables={})

    def test_variables_dict_is_copied(self) -> None:
        """Caller mutation after run() must not affect the rendered
        output, because PromptBuilder accepts a snapshot view."""
        from llm_code.engine.components.prompt_assembler import (
            PromptAssemblerComponent,
        )
        from llm_code.engine.prompt_builder import PromptBuilder

        pb = PromptBuilder(template="Hi {{ name }}")
        comp = PromptAssemblerComponent(pb)
        vars_dict = {"name": "A"}
        out = comp.run(variables=vars_dict)
        vars_dict["name"] = "MUTATED"
        assert out["prompt"] == "Hi A"

    def test_multiple_variables(self) -> None:
        from llm_code.engine.components.prompt_assembler import (
            PromptAssemblerComponent,
        )
        from llm_code.engine.prompt_builder import PromptBuilder

        pb = PromptBuilder(template="{{ a }}-{{ b }}")
        comp = PromptAssemblerComponent(pb)
        out = comp.run(variables={"a": "x", "b": "y"})
        assert out["prompt"] == "x-y"

    def test_builder_accessor(self) -> None:
        from llm_code.engine.components.prompt_assembler import (
            PromptAssemblerComponent,
        )
        from llm_code.engine.prompt_builder import PromptBuilder

        pb = PromptBuilder(template="x")
        comp = PromptAssemblerComponent(pb)
        assert comp.builder is pb

    def test_output_contains_string(self) -> None:
        from llm_code.engine.components.prompt_assembler import (
            PromptAssemblerComponent,
        )
        from llm_code.engine.prompt_builder import PromptBuilder

        pb = PromptBuilder(template="hi")
        comp = PromptAssemblerComponent(pb)
        out = comp.run(variables={})
        assert isinstance(out["prompt"], str)


class TestPromptAssemblerInPipeline:
    def test_pipeline_run_renders(self) -> None:
        from llm_code.engine.components.prompt_assembler import (
            PromptAssemblerComponent,
        )
        from llm_code.engine.pipeline import Pipeline
        from llm_code.engine.prompt_builder import PromptBuilder

        p = Pipeline()
        p.add_component(
            "prompt",
            PromptAssemblerComponent(PromptBuilder(template="Hi {{ n }}")),
        )
        out = p.run({"prompt": {"variables": {"n": "there"}}})
        assert out["prompt"]["prompt"] == "Hi there"

    def test_entry_socket_exposed(self) -> None:
        from llm_code.engine.components.prompt_assembler import (
            PromptAssemblerComponent,
        )
        from llm_code.engine.pipeline import Pipeline
        from llm_code.engine.prompt_builder import PromptBuilder

        p = Pipeline()
        p.add_component(
            "prompt",
            PromptAssemblerComponent(PromptBuilder(template="x")),
        )
        assert "variables" in p.inputs()["prompt"]

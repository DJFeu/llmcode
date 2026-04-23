"""PromptAssemblerComponent — wrap :class:`PromptBuilder` for the Pipeline DAG.

Thin adapter that turns the M1 :class:`PromptBuilder` into a @component.
The Component owns one :class:`PromptBuilder` instance and exposes a
single input socket ``variables`` (a mapping of template variable name
to value). The output is the renderer's native ``{"prompt": str}`` dict
so downstream consumers see the same shape they always have.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.7 Step 7
"""
from __future__ import annotations

from typing import Any, Mapping

from llm_code.engine.component import component, output_types
from llm_code.engine.prompt_builder import PromptBuilder


@component
@output_types(prompt=str)
class PromptAssemblerComponent:
    """Render a :class:`PromptBuilder` template from pipeline inputs."""

    def __init__(self, builder: PromptBuilder) -> None:
        self._builder = builder

    @property
    def builder(self) -> PromptBuilder:
        """Expose the underlying builder (useful for tests / diagnostics)."""
        return self._builder

    def run(self, variables: Mapping[str, Any]) -> dict[str, Any]:
        """Render the template with ``variables``.

        We snapshot ``variables`` into a plain dict so later caller
        mutations do not leak into the rendered output.
        """
        snapshot = dict(variables)
        return self._builder.run(**snapshot)

"""Example legacy plugin — used as a migration fixture.

This file is the EXPECTED OUTPUT of running ``llmcode migrate v12``
on ``../legacy_plugin_example_1/plugin.py``. The mid-module imports
below are the deliberate side effect of the codemod's rewriter: they
are flagged E402 by ruff but asserting their exact shape is the whole
point of the fixture, so the file-level ``ruff: noqa: E402`` pragma
intentionally silences the check here only.
"""
# ruff: noqa: E402
from __future__ import annotations
from llm_code.engine.prompt_builder import PromptBuilder
beast = PromptBuilder(template_path="modes/beast.j2")
from llm_code.engine import Pipeline, component


@component
class AuditingComponent:
    """A simple legacy subclass."""

    def run(self, ctx):
        self.ctx = ctx
        return ctx


def build_system_prompt(task: str) -> str:
    return beast.run(task=task)["prompt"]
def register(pipeline: Pipeline) -> None:
    """Register components produced by the v12 codemod."""
    pipeline.add_component("auditingComponent", AuditingComponent())

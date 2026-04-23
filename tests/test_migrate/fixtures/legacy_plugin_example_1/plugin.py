"""Example legacy plugin — used as a migration fixture."""
from __future__ import annotations

from llm_code.runtime.prompts.mode import beast
from llm_code.runtime.tool_pipeline import ToolExecutionPipeline


class AuditingPipeline(ToolExecutionPipeline):
    """A simple legacy subclass."""

    def pre_execute(self, ctx):
        self.ctx = ctx
        return ctx


def build_system_prompt(task: str) -> str:
    return beast.format(task=task)

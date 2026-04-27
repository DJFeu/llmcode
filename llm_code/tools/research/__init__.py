"""Research-pipeline package (v2.8.0).

Contains M2's multi-query expansion (`expansion.py`), M5's pipeline
orchestrator (`pipeline.py`), and M5's high-level tool
(`research_tool.py`).

Plan:
* M2 — docs/superpowers/plans/2026-04-27-llm-code-v17-m2-multi-query-expansion.md
* M5 — docs/superpowers/plans/2026-04-27-llm-code-v17-m5-research-tool.md
"""
from __future__ import annotations

from llm_code.tools.research.expansion import expand, expand_template
from llm_code.tools.research.pipeline import (
    ResearchOutput,
    ResearchSource,
    run_research,
)
from llm_code.tools.research.research_tool import ResearchInput, ResearchTool

__all__ = [
    "expand",
    "expand_template",
    "run_research",
    "ResearchOutput",
    "ResearchSource",
    "ResearchTool",
    "ResearchInput",
]

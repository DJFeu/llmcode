"""Research-pipeline package (v2.8.0).

Contains M2's multi-query expansion (`expansion.py`), M5's pipeline
orchestrator (`pipeline.py` — wave 3), and M5's high-level tool
(`research_tool.py` — wave 3).

Plan:
* M2 — docs/superpowers/plans/2026-04-27-llm-code-v17-m2-multi-query-expansion.md
* M5 — docs/superpowers/plans/2026-04-27-llm-code-v17-m5-research-tool.md
"""
from __future__ import annotations

from llm_code.tools.research.expansion import expand, expand_template

__all__ = ["expand", "expand_template"]

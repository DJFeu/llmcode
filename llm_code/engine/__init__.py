"""LLM-Code v12 engine — Haystack-borrowed primitives.

Scaffolding package for v12 M0. Populated progressively per milestone:

- M1: prompt_builder.py  (Jinja2 PromptBuilder)
- M2: component.py, pipeline.py, graph.py, components/*
- M3: agent.py, policies/*
- M5: async_pipeline.py, async_component.py, concurrency.py
- M6: tracing.py, observability/*
- M7: components/memory/*

See: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md.
"""
from __future__ import annotations

from llm_code.engine.state import MemoryScope, Mode, State

# M3: Agent + policy factory. Importing here keeps the public surface
# a single ``from llm_code.engine import ...`` for runtime callers.
from llm_code.engine.agent import Agent, build_agent_from_config
from llm_code.engine.agent_result import AgentError, AgentResult

# M5: AsyncPipeline + async decorators. Exported so downstream code
# that wants to opt into the async engine can ``from llm_code.engine
# import AsyncPipeline, async_component``.
from llm_code.engine.async_component import async_component
from llm_code.engine.async_pipeline import AsyncPipeline
from llm_code.engine.concurrency import (
    DEFAULT_GROUP,
    MAX_GROUP_PARALLELISM,
    assert_no_blocking_io,
    run_group_parallel,
)

__all__ = [
    "Agent",
    "AgentError",
    "AgentResult",
    "AsyncPipeline",
    "DEFAULT_GROUP",
    "MAX_GROUP_PARALLELISM",
    "MemoryScope",
    "Mode",
    "State",
    "assert_no_blocking_io",
    "async_component",
    "build_agent_from_config",
    "run_group_parallel",
]

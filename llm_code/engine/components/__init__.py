"""v12 engine components — registered Pipeline stages.

Populated by milestones:
- M2: permission_check, denial_tracking, rate_limiter, speculative_executor,
      deferred_tool_resolver, tool_executor, postprocess, prompt_assembler
- M7: memory/embedder, memory/retriever, memory/reranker,
      memory/writer, memory/context

See: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.2, §5.7.
"""
from __future__ import annotations

"""Default v12 pipeline factory — seven-stage tool-execution DAG.

This module assembles a :class:`Pipeline` with the canonical M2 stages:

    perm → denial → rate → speculative → resolver → exec → post

Optional subset selection is driven by
:attr:`EngineConfig.pipeline_stages`: a stage name missing from the
tuple is skipped (its edges are elided as well). The memory wiring
scheduled for M7 is left as an anchor comment; subagents that land the
memory Components insert new nodes + connections at that anchor and do
not edit any call site here.

Design notes
------------
- The factory takes explicit ``registry`` / ``permission_policy`` /
  ``denial_tracker`` arguments instead of reaching into the
  ``ConversationRuntime`` object. That keeps the factory testable in
  isolation and lets the shim in ``runtime/tool_pipeline.py`` inject
  whichever instances the legacy path already holds.
- We intentionally do not wire the PromptAssembler here — prompt
  assembly sits upstream of tool dispatch in v12 and is constructed
  per-turn by the Agent in M3. The memory hook below is the only
  anchor downstream code relies on.

Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-pipeline-dag.md Task 2.8
"""
from __future__ import annotations

from typing import Any, Protocol

from llm_code.engine.components.denial_tracking import DenialTrackingComponent
from llm_code.engine.components.deferred_tool_resolver import (
    DeferredToolResolverComponent,
)
from llm_code.engine.components.permission_check import PermissionCheckComponent
from llm_code.engine.components.postprocess import PostProcessComponent
from llm_code.engine.components.rate_limiter import RateLimiterComponent
from llm_code.engine.components.speculative_executor import (
    SpeculativeExecutorComponent,
)
from llm_code.engine.components.tool_executor import ToolExecutorComponent
from llm_code.engine.pipeline import Pipeline
from llm_code.runtime.config import EngineConfig
from llm_code.runtime.permission_denial_tracker import PermissionDenialTracker
from llm_code.runtime.permissions import PermissionMode, PermissionPolicy


class _Registry(Protocol):
    def get(self, name: str) -> Any: ...


def build_default_pipeline(
    config: EngineConfig,
    *,
    registry: _Registry,
    permission_policy: PermissionPolicy | None = None,
    denial_tracker: PermissionDenialTracker | None = None,
) -> Pipeline:
    """Assemble the default seven-stage pipeline.

    Args:
        config: :class:`EngineConfig` — controls which stages are
            included (``pipeline_stages``).
        registry: Tool registry exposing ``get(name) -> Tool | None``.
            Lent to :class:`DeferredToolResolverComponent`.
        permission_policy: Optional policy. Defaults to a
            :attr:`PermissionMode.FULL_ACCESS` policy — parity tests
            and parity-running CLIs should pass in the real runtime
            policy.
        denial_tracker: Optional denial tracker for persistence across
            sessions. Defaults to a fresh instance per pipeline.
    """
    stages = frozenset(config.pipeline_stages)
    policy = permission_policy or PermissionPolicy(mode=PermissionMode.FULL_ACCESS)

    p = Pipeline()

    if "perm" in stages:
        p.add_component("perm", PermissionCheckComponent(policy))
    if "denial" in stages:
        p.add_component(
            "denial",
            DenialTrackingComponent(tracker=denial_tracker),
        )
    if "rate" in stages:
        p.add_component("rate", RateLimiterComponent())
    if "speculative" in stages:
        p.add_component("speculative", SpeculativeExecutorComponent())
    if "resolver" in stages:
        p.add_component("resolver", DeferredToolResolverComponent(registry))
    # M7 HOOK: memory Components insert here (before PromptAssembler).
    # When the memory milestone lands, subagents add
    # ``Embedder → Retriever → Reranker → MemoryContext`` nodes and
    # connect them to the PromptAssembler created upstream by the
    # Agent. Do NOT insert them into the tool-execution DAG here —
    # memory Components sit above this factory's scope.
    if "exec" in stages:
        p.add_component("exec", ToolExecutorComponent())
    if "post" in stages:
        p.add_component("post", PostProcessComponent())

    _wire(p, stages)
    p.validate()
    return p


def _wire(p: Pipeline, stages: frozenset[str]) -> None:
    """Connect the stages that are registered.

    Each ``connect`` call is guarded so selectively removing stages
    (e.g. dropping ``speculative`` for a simplified parity baseline)
    does not raise.
    """
    if {"perm", "denial"} <= stages:
        p.connect("perm.allowed", "denial.allowed")
        p.connect("perm.reason", "denial.reason")
    if {"denial", "rate"} <= stages:
        p.connect("denial.proceed", "rate.proceed")
    if {"rate", "speculative"} <= stages:
        p.connect("rate.proceed", "speculative.proceed")
    if {"speculative", "resolver"} <= stages:
        p.connect("speculative.cache_hit", "resolver.cache_hit")
        p.connect("speculative.proceed", "resolver.proceed")
    if {"resolver", "exec"} <= stages:
        p.connect("resolver.resolved_tool", "exec.resolved_tool")
        p.connect("resolver.proceed", "exec.proceed")
    if {"speculative", "exec"} <= stages:
        p.connect("speculative.cached_result", "exec.cached_result")
    # If no speculative stage, still wire denial->exec via rate so that
    # `proceed` flows end-to-end. The minimal path perm→exec→post is
    # explicitly allowed to have exec's proceed sourced from entry.
    if {"exec", "post"} <= stages:
        p.connect("exec.raw_result", "post.raw_result")

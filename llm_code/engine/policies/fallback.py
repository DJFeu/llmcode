"""Built-in :class:`FallbackPolicy` implementations.

When a tool call fails after retries are exhausted, a fallback policy
gets a chance to swap the tool for a semantically-equivalent alternative
so the agent can make progress instead of surfacing the error to the
model. Two strategies ship today:

- :class:`SemanticFallback` — pre-declared static mapping. Zero-cost,
  deterministic, covers the common "web_search → web_fetch" type of
  swap.
- :class:`ModelFallback` — asks a cheap model which alternative tool to
  try. Cached by ``(failed_tool, error-class)`` so repeated failures on
  the same tool don't burn LLM calls.

Spec: docs/superpowers/specs/2026-04-21-llm-code-v12-haystack-borrow-design.md §5.3
Plan: docs/superpowers/plans/2026-04-21-llm-code-v12-agent-loop-control.md Task 3.3
"""
from __future__ import annotations

from typing import Callable, Mapping

from llm_code.engine.policies import FallbackDecision
from llm_code.engine.state import State


class NoFallback:
    """Default: never propose an alternative. The error bubbles up to
    the model, which can reason about the failure itself.
    """

    def fallback(
        self, failed_tool: str, error: Exception, state: State
    ) -> FallbackDecision:
        return FallbackDecision(fallback_tool=None, reason="no-fallback policy")


class SemanticFallback:
    """Map a failed tool to a declared semantic alternative.

    The default mapping encodes "these two tools answer roughly the same
    question":

    - ``web_search → web_fetch`` — a single URL lookup can stand in for
      a broader search when search is down.
    - ``glob_search → bash`` — we can always shell out to ``find`` if
      the in-process globber blew up.
    - ``lsp_go_to_definition → grep_search`` — text search is a coarser
      but reliable fallback when the LSP server crashes.

    Callers can pass ``overrides`` to extend or replace entries. The
    overrides layer on top of the defaults; passing
    ``{"web_search": None}`` removes a default by setting it to
    ``None`` — which we interpret as "no fallback declared".
    """

    DEFAULT_MAP: Mapping[str, str] = {
        "web_search": "web_fetch",
        "glob_search": "bash",
        "lsp_go_to_definition": "grep_search",
    }

    def __init__(self, overrides: Mapping[str, str | None] | None = None) -> None:
        merged: dict[str, str] = dict(self.DEFAULT_MAP)
        if overrides:
            for k, v in overrides.items():
                if v is None:
                    merged.pop(k, None)
                else:
                    merged[k] = v
        self._map = merged

    def fallback(
        self, failed_tool: str, error: Exception, state: State
    ) -> FallbackDecision:
        target = self._map.get(failed_tool)
        if target is None:
            return FallbackDecision(
                fallback_tool=None,
                reason=f"no fallback declared for {failed_tool}",
            )
        return FallbackDecision(
            fallback_tool=target,
            reason=f"semantic fallback: {failed_tool} -> {target}",
        )


# Signature for the ``suggest_fn`` hook used by :class:`ModelFallback`.
# The function receives the failed tool, the error, and the list of
# available tool names; returns either a tool name or ``None``.
ModelSuggestFn = Callable[[str, Exception, tuple[str, ...]], str | None]


class ModelFallback:
    """Delegate fallback decisions to a cheap LLM.

    The actual LLM call is encapsulated in ``suggest_fn`` — a plain
    callable. This indirection means:

    1. The policy can be tested without importing any SDK.
    2. The caller can pick their own model (Haiku, a local small
       model, …) without touching policy code.

    Results are cached by ``(failed_tool, error_class_name)`` so a
    flaky tool that fails 10 times in one run triggers at most one
    LLM call.
    """

    def __init__(
        self,
        suggest_fn: ModelSuggestFn,
        available_tools: tuple[str, ...],
        cache: dict[tuple[str, str], str | None] | None = None,
    ) -> None:
        if not available_tools:
            raise ValueError("ModelFallback requires at least one available tool")
        self._suggest = suggest_fn
        self._tools = tuple(available_tools)
        # A caller-supplied cache lets tests inspect hits/misses; when
        # omitted we keep one per instance so caches don't leak across
        # agents that share a process.
        self._cache: dict[tuple[str, str], str | None] = (
            cache if cache is not None else {}
        )

    def fallback(
        self, failed_tool: str, error: Exception, state: State
    ) -> FallbackDecision:
        key = (failed_tool, type(error).__name__)
        if key in self._cache:
            cached = self._cache[key]
            if cached is None:
                return FallbackDecision(
                    fallback_tool=None,
                    reason=f"cached: no suggestion for {failed_tool}",
                )
            return FallbackDecision(
                fallback_tool=cached,
                reason=f"cached model fallback: {failed_tool} -> {cached}",
            )
        try:
            suggestion = self._suggest(failed_tool, error, self._tools)
        except Exception as exc:  # noqa: BLE001 - defensive; policy must not throw.
            # If the cheap model itself errors, pretend there was no
            # suggestion — we must never make a retry worse.
            self._cache[key] = None
            return FallbackDecision(
                fallback_tool=None,
                reason=f"model fallback failed: {exc}",
            )
        # Treat "unknown" suggestion the same as no suggestion; this
        # guards against hallucinated tool names leaking into exec.
        if suggestion is None or suggestion not in self._tools:
            self._cache[key] = None
            return FallbackDecision(
                fallback_tool=None,
                reason=f"model returned unusable suggestion: {suggestion!r}",
            )
        self._cache[key] = suggestion
        return FallbackDecision(
            fallback_tool=suggestion,
            reason=f"model fallback: {failed_tool} -> {suggestion}",
        )


__all__ = ["ModelFallback", "ModelSuggestFn", "NoFallback", "SemanticFallback"]

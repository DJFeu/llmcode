"""Tests for :mod:`llm_code.engine.policies.fallback`."""
from __future__ import annotations

import pytest

from llm_code.engine.policies import FallbackPolicy
from llm_code.engine.policies.fallback import (
    ModelFallback,
    NoFallback,
    SemanticFallback,
)


# ---------------------------------------------------------------------------
# NoFallback
# ---------------------------------------------------------------------------


class TestNoFallback:
    def test_never_proposes(self):
        decision = NoFallback().fallback("web_search", Exception(), {})
        assert decision.fallback_tool is None
        assert "no-fallback" in decision.reason

    def test_protocol_conformance(self):
        assert isinstance(NoFallback(), FallbackPolicy)


# ---------------------------------------------------------------------------
# SemanticFallback
# ---------------------------------------------------------------------------


class TestSemanticFallback:
    def test_web_search_default(self):
        decision = SemanticFallback().fallback("web_search", Exception(), {})
        assert decision.fallback_tool == "web_fetch"

    def test_glob_search_default(self):
        decision = SemanticFallback().fallback("glob_search", Exception(), {})
        assert decision.fallback_tool == "bash"

    def test_lsp_go_to_definition_default(self):
        decision = SemanticFallback().fallback(
            "lsp_go_to_definition", Exception(), {}
        )
        assert decision.fallback_tool == "grep_search"

    def test_unknown_tool_returns_none(self):
        decision = SemanticFallback().fallback("unknown_tool", Exception(), {})
        assert decision.fallback_tool is None
        assert "no fallback declared" in decision.reason

    def test_override_extends_defaults(self):
        policy = SemanticFallback(overrides={"my_tool": "backup_tool"})
        assert policy.fallback("my_tool", Exception(), {}).fallback_tool == "backup_tool"
        # Default mapping still works.
        assert (
            policy.fallback("web_search", Exception(), {}).fallback_tool
            == "web_fetch"
        )

    def test_override_none_removes_default(self):
        policy = SemanticFallback(overrides={"web_search": None})
        assert policy.fallback("web_search", Exception(), {}).fallback_tool is None

    def test_override_replaces_default(self):
        policy = SemanticFallback(overrides={"web_search": "alt_search"})
        assert (
            policy.fallback("web_search", Exception(), {}).fallback_tool
            == "alt_search"
        )


# ---------------------------------------------------------------------------
# ModelFallback
# ---------------------------------------------------------------------------


class TestModelFallback:
    def test_requires_available_tools(self):
        with pytest.raises(ValueError):
            ModelFallback(lambda f, e, t: None, available_tools=())

    def test_returns_suggestion_when_valid(self):
        tools = ("alpha", "beta", "gamma")
        calls: list[tuple] = []

        def suggest(failed, err, available):
            calls.append((failed, type(err).__name__, available))
            return "beta"

        policy = ModelFallback(suggest, tools)
        decision = policy.fallback("alpha", ValueError("x"), {})
        assert decision.fallback_tool == "beta"
        assert len(calls) == 1

    def test_invalid_suggestion_rejected(self):
        # Suggestion not in available_tools.
        policy = ModelFallback(
            lambda f, e, t: "not_in_tools", available_tools=("a", "b")
        )
        decision = policy.fallback("a", Exception(), {})
        assert decision.fallback_tool is None
        assert "unusable" in decision.reason

    def test_none_suggestion_rejected(self):
        policy = ModelFallback(lambda f, e, t: None, available_tools=("a",))
        decision = policy.fallback("a", Exception(), {})
        assert decision.fallback_tool is None

    def test_caches_by_tool_and_error_class(self):
        hits = []

        def suggest(failed, err, available):
            hits.append((failed, type(err).__name__))
            return "b"

        policy = ModelFallback(suggest, available_tools=("a", "b"))
        policy.fallback("a", ValueError(), {})
        policy.fallback("a", ValueError(), {})
        policy.fallback("a", ValueError(), {})
        assert len(hits) == 1  # cached after first call

    def test_different_error_class_bypasses_cache(self):
        hits = []

        def suggest(failed, err, available):
            hits.append(type(err).__name__)
            return "b"

        policy = ModelFallback(suggest, available_tools=("a", "b"))
        policy.fallback("a", ValueError(), {})
        policy.fallback("a", RuntimeError(), {})
        assert hits == ["ValueError", "RuntimeError"]

    def test_suggest_fn_exception_swallowed(self):
        def suggest(failed, err, available):
            raise RuntimeError("LLM broken")

        policy = ModelFallback(suggest, available_tools=("a",))
        decision = policy.fallback("a", Exception(), {})
        assert decision.fallback_tool is None
        assert "model fallback failed" in decision.reason

    def test_cache_persists_across_calls(self):
        cache: dict = {}

        def suggest(failed, err, available):
            return "b"

        p1 = ModelFallback(suggest, available_tools=("a", "b"), cache=cache)
        p1.fallback("a", ValueError(), {})
        assert ("a", "ValueError") in cache

    def test_cached_none_result_surfaces_cached_reason(self):
        def suggest(failed, err, available):
            return None

        policy = ModelFallback(suggest, available_tools=("a", "b"))
        # Populate cache
        policy.fallback("a", Exception(), {})
        # Second call hits cache
        decision = policy.fallback("a", Exception(), {})
        assert decision.fallback_tool is None
        assert "cached" in decision.reason

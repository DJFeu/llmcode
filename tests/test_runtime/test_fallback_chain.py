"""Tests for Wave2-3 FallbackChain multi-model escalation."""
from __future__ import annotations


def test_fallback_chain_returns_next_in_order() -> None:
    from llm_code.runtime.fallback import FallbackChain

    chain = FallbackChain(["sonnet", "haiku", "gpt-4o"])
    assert chain.next("sonnet") == "haiku"
    assert chain.next("haiku") == "gpt-4o"


def test_fallback_chain_exhausts_returns_none() -> None:
    """The final model in the chain has nowhere to fall back to."""
    from llm_code.runtime.fallback import FallbackChain

    chain = FallbackChain(["sonnet", "haiku"])
    assert chain.next("haiku") is None


def test_fallback_chain_unknown_current_starts_at_head() -> None:
    """When the active model is not in the chain, pick the first entry."""
    from llm_code.runtime.fallback import FallbackChain

    chain = FallbackChain(["sonnet", "haiku"])
    assert chain.next("grok-9000") == "sonnet"


def test_fallback_chain_empty_is_noop() -> None:
    from llm_code.runtime.fallback import FallbackChain

    chain = FallbackChain([])
    assert chain.next("sonnet") is None


def test_fallback_chain_single_entry_equals_legacy_behavior() -> None:
    """A 1-element chain matches the old `fallback: str` field."""
    from llm_code.runtime.fallback import FallbackChain

    chain = FallbackChain(["haiku"])
    assert chain.next("sonnet") == "haiku"
    # Once we're on the single fallback, nothing more to try.
    assert chain.next("haiku") is None


def test_fallback_chain_respects_non_retryable_error_kind() -> None:
    """An auth/model-not-found error skips the chain entirely."""
    from llm_code.runtime.fallback import FallbackChain

    chain = FallbackChain(["sonnet", "haiku"])
    assert chain.next("sonnet", error_kind="non_retryable") is None


def test_fallback_chain_from_routing_config_string_legacy() -> None:
    """Backward-compat: ``fallback: str`` is promoted to a single-entry chain."""
    from llm_code.runtime.config import ModelRoutingConfig
    from llm_code.runtime.fallback import FallbackChain

    cfg = ModelRoutingConfig(fallback="haiku")
    chain = FallbackChain.from_routing(cfg)
    assert chain.next("sonnet") == "haiku"
    assert chain.next("haiku") is None


def test_fallback_chain_from_routing_config_list() -> None:
    """New-style: ``fallbacks: list[str]`` produces a multi-step chain."""
    from llm_code.runtime.config import ModelRoutingConfig
    from llm_code.runtime.fallback import FallbackChain

    cfg = ModelRoutingConfig(fallbacks=("haiku", "gpt-4o"))
    chain = FallbackChain.from_routing(cfg)
    assert chain.next("sonnet") == "haiku"
    assert chain.next("haiku") == "gpt-4o"
    assert chain.next("gpt-4o") is None


def test_fallback_chain_list_takes_precedence_over_string() -> None:
    """If both ``fallbacks`` and legacy ``fallback`` are set, the list wins."""
    from llm_code.runtime.config import ModelRoutingConfig
    from llm_code.runtime.fallback import FallbackChain

    cfg = ModelRoutingConfig(fallback="ignored", fallbacks=("haiku", "gpt-4o"))
    chain = FallbackChain.from_routing(cfg)
    assert list(chain) == ["haiku", "gpt-4o"]

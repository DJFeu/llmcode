"""Tests for llm_code.tui.spinner_verbs."""
from __future__ import annotations

from llm_code.tui.spinner_verbs import DEFAULT_VERBS, get_verb


def test_default_pool_non_empty() -> None:
    assert len(DEFAULT_VERBS) >= 100
    assert all(isinstance(v, str) and v for v in DEFAULT_VERBS)


def test_get_verb_deterministic_with_seed() -> None:
    a = get_verb(seed=42)
    b = get_verb(seed=42)
    assert a == b
    assert a in DEFAULT_VERBS


def test_get_verb_different_seeds_may_differ() -> None:
    seen = {get_verb(seed=i) for i in range(50)}
    # at least a handful of distinct verbs across 50 seeds
    assert len(seen) > 5


def test_mode_replace_uses_only_override() -> None:
    override = ("Zzzing", "Qqqing")
    picks = {get_verb(seed=i, override=override, mode="replace") for i in range(30)}
    assert picks.issubset(set(override))
    assert picks  # non-empty


def test_mode_replace_empty_falls_back_to_defaults() -> None:
    v = get_verb(seed=1, override=(), mode="replace")
    assert v in DEFAULT_VERBS


def test_mode_append_extends_defaults() -> None:
    extra = ("XylophonicOverride",)
    # Try many seeds; the extra should show up sometimes OR defaults should always be valid
    seen = {
        get_verb(seed=i, override=extra, mode="append")
        for i in range(500)
    }
    assert all(v in DEFAULT_VERBS or v in extra for v in seen)
    # Pool size is defaults + extra
    # With 500 seeds, we expect the extra to appear at least once
    # (probabilistically very likely; random.Random is deterministic so stable)
    assert "XylophonicOverride" in seen

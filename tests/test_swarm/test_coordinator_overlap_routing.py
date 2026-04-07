"""Tests for Coordinator._select_delegation_target overlap-based routing."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from llm_code.swarm.coordinator import Coordinator


def _coord(threshold: float = 0.6) -> Coordinator:
    config = MagicMock()
    config.swarm = MagicMock(overlap_threshold=threshold, synthesis_enabled=True)
    return Coordinator(manager=MagicMock(), provider=MagicMock(), config=config)


def test_high_overlap_returns_resume() -> None:
    coord = _coord(threshold=0.5)
    member = SimpleNamespace(
        id="m1",
        context="implement parser tokenizer lexer ast",
    )
    decision, target = coord._select_delegation_target(
        "implement parser tokenizer lexer", [member]
    )
    assert decision == "resume"
    assert target == "m1"


def test_low_overlap_returns_spawn() -> None:
    coord = _coord(threshold=0.6)
    member = SimpleNamespace(id="m1", context="completely unrelated topic")
    decision, target = coord._select_delegation_target(
        "deploy kubernetes cluster ingress", [member]
    )
    assert decision == "spawn"
    assert target == "deploy kubernetes cluster ingress"


def test_no_candidates_returns_spawn() -> None:
    coord = _coord()
    decision, target = coord._select_delegation_target("anything", [])
    assert decision == "spawn"


@pytest.mark.parametrize("threshold,expected", [(0.1, "resume"), (0.99, "spawn")])
def test_threshold_parametrized(threshold: float, expected: str) -> None:
    coord = _coord(threshold=threshold)
    member = SimpleNamespace(id="m1", context="alpha beta gamma")
    decision, _ = coord._select_delegation_target("alpha beta delta", [member])
    assert decision == expected

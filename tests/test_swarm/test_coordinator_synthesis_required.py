"""Regression: Coordinator.orchestrate() must call _synthesize() first."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_code.swarm.coordinator import Coordinator


def _make_coord() -> Coordinator:
    manager = MagicMock()
    provider = MagicMock()
    config = MagicMock()
    config.model = "test-model"
    config.swarm = MagicMock(
        synthesis_enabled=True, max_members=5, overlap_threshold=0.6
    )
    return Coordinator(manager=manager, provider=provider, config=config)


@pytest.mark.asyncio
async def test_synthesize_called_before_decompose() -> None:
    coord = _make_coord()

    call_order: list[str] = []

    async def fake_synth(task: str) -> dict:
        call_order.append("synth")
        return {"should_delegate": False, "reason": "stub"}

    async def fake_decompose(task: str) -> list[dict]:
        call_order.append("decompose")
        return []

    coord._synthesize = fake_synth  # type: ignore[assignment]
    coord._decompose = fake_decompose  # type: ignore[assignment]

    result = await coord.orchestrate("Build a thing that does many things in detail.")
    assert "synth" in call_order
    assert call_order[0] == "synth"
    assert "decompose" not in call_order  # short-circuited
    assert "Skipping delegation" in result


@pytest.mark.asyncio
async def test_synthesize_called_even_when_delegate_true() -> None:
    coord = _make_coord()
    calls: list[str] = []

    async def fake_synth(task: str) -> dict:
        calls.append("synth")
        return {"should_delegate": True, "reason": "ok"}

    async def fake_decompose(task: str) -> list[dict]:
        calls.append("decompose")
        return []  # forces "no subtasks" branch

    coord._synthesize = fake_synth  # type: ignore[assignment]
    coord._decompose = fake_decompose  # type: ignore[assignment]

    await coord.orchestrate("A long enough task description to bypass shortcut " * 5)
    assert calls.index("synth") < calls.index("decompose")

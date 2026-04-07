"""Regression: short tasks short-circuit synthesis with a stub."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llm_code.swarm.coordinator import Coordinator


@pytest.mark.asyncio
async def test_short_task_short_circuits_synthesize() -> None:
    config = MagicMock()
    config.swarm = MagicMock(synthesis_enabled=True, overlap_threshold=0.6)
    coord = Coordinator(manager=MagicMock(), provider=MagicMock(), config=config)
    coord._provider.send_message = MagicMock(
        side_effect=AssertionError("synth must not call provider for short tasks")
    )
    result = await coord._synthesize("tiny task")
    assert result is not None
    assert result["should_delegate"] is True
    assert "stub" in result["reason"]


def test_select_delegation_target_exists() -> None:
    """Coordinator must expose _select_delegation_target — no path may bypass it."""
    config = MagicMock()
    config.swarm = MagicMock(overlap_threshold=0.6)
    coord = Coordinator(manager=MagicMock(), provider=MagicMock(), config=config)
    assert callable(coord._select_delegation_target)

"""M5 Task 5.10 Step 3 — 1000× ``Agent.run_async()`` soak test.

Drives 1000 consecutive async agent runs with a mock chat_fn that
returns no tool calls (one-shot "final response" path). Passes when
no single run took longer than 2× the median latency — i.e. there is
no hang, stall, or slow-path regression hidden by average-only
timings.

Gated behind ``LLMCODE_PERF=1`` because the raw wall-clock cost is
~2–5 s on a modern laptop and we want the default ``pytest -q`` run
to stay under a minute.
"""
from __future__ import annotations

import statistics
import time
from unittest.mock import MagicMock

import pytest

from llm_code.engine.agent import Agent
from llm_code.engine.pipeline import Pipeline


# Tight loop count so the test finishes in a few seconds on a laptop
# while still exercising hundreds of iterations — enough to catch a
# pathological tail-latency regression.
_ITERATIONS = 1000


def _no_tool_chat(_messages, _tools):
    """Synthetic chat_fn: no tool calls, plain assistant text chunk.

    Returning no tool calls triggers the Agent's ``model_responded``
    exit path on the first turn, so each ``run_async`` completes in
    one loop iteration — exactly what we want for a soak benchmark.
    """
    return ([], [{"text": "done"}])


@pytest.fixture
def _soak_agent() -> Agent:
    pipeline = MagicMock(spec=Pipeline)
    return Agent(
        pipeline,
        chat_fn=_no_tool_chat,
        max_agent_steps=2,
    )


@pytest.mark.perf
@pytest.mark.slow
class TestAgentRunAsyncSoak:
    async def test_1000_consecutive_runs_no_hang(
        self, _soak_agent: Agent,
    ) -> None:
        """Assert the tail of the per-run latency distribution stays
        inside 2× the median — no single iteration hangs."""
        timings: list[float] = []
        for _ in range(_ITERATIONS):
            t0 = time.perf_counter()
            result = await _soak_agent.run_async([
                {"role": "user", "content": "soak"},
            ])
            timings.append(time.perf_counter() - t0)
            # Sanity: the agent must actually finish — anything else
            # would inflate the next iteration's timer.
            assert result.exit_reason == "model_responded"

        median = statistics.median(timings)
        slowest = max(timings)
        # Envelope: slowest run ≤ 2× median. Median is tiny (usually
        # << 1 ms), so we also enforce an absolute floor of 50 ms so
        # routine GC pauses don't trip the ratio check on a sub-ms
        # median.
        envelope = max(median * 2.0, 0.050)
        assert slowest <= envelope, (
            f"soak regression: slowest={slowest*1000:.3f} ms, "
            f"median={median*1000:.3f} ms, envelope={envelope*1000:.3f} ms"
        )
        # Zero-failure contract — every run must have completed.
        assert len(timings) == _ITERATIONS

    async def test_soak_does_not_leak_tasks(
        self, _soak_agent: Agent,
    ) -> None:
        """Run a short burst and confirm no stray asyncio tasks linger.

        A leaked background task would show up in ``asyncio.all_tasks()``
        after the event loop has returned to idle. We run a tiny burst
        (not the full 1000× — that's covered above) so this check
        finishes fast but still catches a dangling ``create_task``.
        """
        import asyncio

        loop = asyncio.get_running_loop()
        before = {t for t in asyncio.all_tasks(loop) if not t.done()}

        for _ in range(32):
            await _soak_agent.run_async([
                {"role": "user", "content": "burst"},
            ])

        # Let any trailing scheduled callbacks drain before snapshotting.
        await asyncio.sleep(0)

        after = {
            t
            for t in asyncio.all_tasks(loop)
            if not t.done() and t is not asyncio.current_task()
        }
        leaked = after - before
        assert not leaked, (
            f"{len(leaked)} task(s) survived the burst: "
            f"{[repr(t) for t in leaked]}"
        )

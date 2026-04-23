"""M5 Task 5.10 Step 1 — async pipeline ≥1.8× speedup benchmark.

Asserts the async pipeline's ``asyncio.gather``-backed fan-out beats
the sequential sync baseline by the ship-criteria multiplier when
driving I/O-bound tool calls. Also exercises the JSONL history log
so a nightly regression comparison has per-run data points.

Gating:

- ``pytest.mark.perf`` — default-skipped; only collected when
  ``LLMCODE_PERF=1`` is set. Nightly CI flips the flag.
- ``pytest.mark.slow`` — declared so local devs who opt into slow
  tests via ``LLMCODE_SLOW=1`` still pick this up.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.perf.harness import (
    PerfHarness,
    PipelineBenchResult,
    append_history,
    bench_async_pipeline_parallel,
    bench_sync_pipeline,
)


# The 10 ms sleep used inside the default work_coro / work_fn puts us
# comfortably above scheduler jitter on laptops and CI VMs alike.
_TOOL_CALLS = 8
_EXPECTED_SPEEDUP_AT_K4 = 1.8


@pytest.mark.perf
@pytest.mark.slow
class TestAsyncSpeedupBenchmark:
    async def test_async_pipeline_beats_sync_by_ship_criteria(
        self, tmp_path: Path,
    ) -> None:
        """K=4 async fan-out must finish ≥1.8× faster than sync sequential.

        Collects the sync baseline plus async measurements at
        K ∈ {1, 2, 4, 8} so the JSONL history carries a full sweep —
        a future regression bisect can pin the exact K where the speedup
        collapsed.
        """
        history = tmp_path / "pipeline_bench_history.jsonl"
        harness = PerfHarness()

        sync_result = bench_sync_pipeline(
            _TOOL_CALLS, samples=5, warmup=1, harness=harness,
        )
        append_history(history, sync_result)
        assert sync_result.median_s > 0.0

        async_results: dict[int, PipelineBenchResult] = {}
        for k in (1, 2, 4, 8):
            res = await bench_async_pipeline_parallel(
                _TOOL_CALLS,
                concurrency=k,
                samples=5,
                warmup=1,
                harness=harness,
            )
            append_history(history, res)
            async_results[k] = res

        # Ship criterion: at K=4 the async path is ≥1.8× faster than sync.
        k4_speedup = sync_result.median_s / async_results[4].median_s
        assert k4_speedup >= _EXPECTED_SPEEDUP_AT_K4, (
            f"async speedup at K=4 was {k4_speedup:.2f}× "
            f"(expected ≥ {_EXPECTED_SPEEDUP_AT_K4}×); "
            f"sync={sync_result.median_s*1000:.1f}ms, "
            f"async={async_results[4].median_s*1000:.1f}ms"
        )

        # Monotonicity: higher concurrency must not be slower than lower
        # concurrency (within ±15 % slack so scheduler noise doesn't
        # falsely flag a regression).
        assert async_results[2].median_s <= async_results[1].median_s * 1.15
        assert async_results[4].median_s <= async_results[2].median_s * 1.15

        # History log sanity — 1 sync + 4 async lines = 5 entries.
        assert history.is_file()
        assert len(history.read_text().splitlines()) == 5

    async def test_speedup_is_measurable_even_at_k2(self) -> None:
        """Weaker sibling of the K=4 check — gates the lower bound.

        At K=2 we only claim a mild speedup (≥1.4×); this guards against
        a future where K=4 still wins but K=2 has collapsed to serial
        (often a symptom of a shared lock regression).
        """
        harness = PerfHarness()
        sync_result = bench_sync_pipeline(
            _TOOL_CALLS, samples=5, warmup=1, harness=harness,
        )
        async_k2 = await bench_async_pipeline_parallel(
            _TOOL_CALLS,
            concurrency=2,
            samples=5,
            warmup=1,
            harness=harness,
        )
        speedup = sync_result.median_s / async_k2.median_s
        assert speedup >= 1.4, (
            f"async K=2 speedup {speedup:.2f}× is below the 1.4× floor"
        )


@pytest.mark.perf
class TestHarnessAsyncSurfaceDeterministic:
    """Non-timing-sensitive smoke tests for the new harness surface.

    These assert that the harness itself behaves correctly (samples
    counted, raw/filtered arrays well-formed) without relying on
    speedup magnitude — so they stay green even on extraordinarily
    loaded hardware.
    """

    async def test_measure_async_counts_samples(self) -> None:
        calls = {"n": 0}

        async def _fn() -> None:
            calls["n"] += 1

        h = PerfHarness()
        result = await h.measure_async(_fn, samples=4, warmup=2)
        assert calls["n"] == 6
        assert len(result.raw) == 4
        assert result.median >= 0.0

    async def test_bench_async_returns_pipeline_result(self) -> None:
        r = await bench_async_pipeline_parallel(
            tool_calls=4, concurrency=2, samples=2, warmup=0,
        )
        assert r.mode == "async"
        assert r.concurrency == 2
        assert r.tool_calls == 4
        assert r.median_s >= 0.0
        assert len(r.raw) == 2

    def test_bench_sync_returns_pipeline_result(self) -> None:
        r = bench_sync_pipeline(tool_calls=4, samples=2, warmup=0)
        assert r.mode == "sync"
        assert r.concurrency == 1
        assert r.tool_calls == 4
        assert r.median_s >= 0.0

    async def test_invalid_concurrency_raises(self) -> None:
        with pytest.raises(ValueError, match="concurrency"):
            await bench_async_pipeline_parallel(
                tool_calls=4, concurrency=0, samples=1, warmup=0,
            )

    def test_append_history_writes_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "hist.jsonl"
        r = bench_sync_pipeline(tool_calls=2, samples=1, warmup=0)
        append_history(path, r, extra={"note": "smoke"})
        append_history(path, r, extra={"note": "smoke-2"})
        lines = path.read_text().splitlines()
        assert len(lines) == 2
        import json

        first = json.loads(lines[0])
        assert first["mode"] == "sync"
        assert first["tool_calls"] == 2
        assert first["note"] == "smoke"
        assert isinstance(first["timestamp"], float)

"""Performance harness public surface (H8a — Sprint 2)."""
from llm_code.perf.harness import (
    BaselineStore,
    ComparisonResult,
    MeasureResult,
    PerfHarness,
    PipelineBenchResult,
    append_history,
    bench_async_pipeline_parallel,
    bench_sync_pipeline,
    iqr_filter,
)

__all__ = (
    "BaselineStore",
    "ComparisonResult",
    "MeasureResult",
    "PerfHarness",
    "PipelineBenchResult",
    "append_history",
    "bench_async_pipeline_parallel",
    "bench_sync_pipeline",
    "iqr_filter",
)

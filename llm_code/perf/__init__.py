"""Performance harness public surface (H8a — Sprint 2)."""
from llm_code.perf.harness import (
    BaselineStore,
    ComparisonResult,
    MeasureResult,
    PerfHarness,
    iqr_filter,
)

__all__ = (
    "BaselineStore",
    "ComparisonResult",
    "MeasureResult",
    "PerfHarness",
    "iqr_filter",
)

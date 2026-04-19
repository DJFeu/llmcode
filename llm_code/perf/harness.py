"""Perf harness: measure → IQR-filter → baseline compare (H8a).

Inspired by Gemini CLI's ``PerfTestHarness`` — three ideas we keep:

    * Warmup runs to escape cold-start transients before measuring.
    * IQR-based outlier filtering so a single GC pause / context switch
      doesn't wreck the median.
    * Versioned ``baselines.json`` plus an ``UPDATE_BASELINES=1`` env
      switch so baselines are deliberately re-pinned, not accidentally
      mutated.

The harness itself is pure — no assumptions about pytest or CI. Concrete
perf tests live under ``tests/perf_tests/`` (H8b) and call the harness.
"""
from __future__ import annotations

import json
import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


# ── IQR outlier filter ────────────────────────────────────────────────


def iqr_filter(samples: Sequence[float], k: float = 1.5) -> tuple[float, ...]:
    """Return ``samples`` with values outside ``[Q1 - k·IQR, Q3 + k·IQR]`` stripped.

    Preserves original order — callers that care about "i-th measurement"
    (not common, but cheap) can still zip against labels. For fewer than
    4 samples IQR is meaningless and the function returns the input
    untouched.
    """
    if len(samples) < 4:
        return tuple(samples)
    sorted_samples = sorted(samples)
    # ``method='inclusive'`` keeps the outlier itself out of the Q1/Q3
    # estimate — with ``exclusive`` the interpolation lets a single
    # giant sample drag Q3 high enough to survive the filter.
    q1, _q2, q3 = statistics.quantiles(sorted_samples, n=4, method="inclusive")
    iqr = q3 - q1
    low = q1 - k * iqr
    high = q3 + k * iqr
    return tuple(s for s in samples if low <= s <= high)


# ── Measurement result ────────────────────────────────────────────────


@dataclass(frozen=True)
class MeasureResult:
    """Outcome of one :meth:`PerfHarness.measure` call."""
    raw: tuple[float, ...]
    filtered: tuple[float, ...]
    median: float


# ── Baseline comparison result ───────────────────────────────────────


@dataclass(frozen=True)
class ComparisonResult:
    """Outcome of a single baseline comparison."""
    label: str
    measurement: float
    baseline: float | None
    allowed_delta_pct: float
    passed: bool
    delta_pct: float
    reason: str
    updated_baseline: bool = False


# ── Baseline persistence ──────────────────────────────────────────────


class BaselineStore:
    """Read/write a ``baselines.json`` file.

    The file is a flat ``dict[str, float]`` — one entry per metric
    label. Pretty-printed so code review can diff it sanely. Callers
    must atomically write via :meth:`write` (handled internally).
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, float]:
        if not self._path.is_file():
            return {}
        try:
            data = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}

    def write(self, data: dict[str, float]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {k: float(v) for k, v in data.items()},
            indent=2,
            sort_keys=True,
        )
        self._path.write_text(payload + "\n")

    def update(self, label: str, value: float) -> None:
        data = self.load()
        data[label] = float(value)
        self.write(data)


# ── Harness ───────────────────────────────────────────────────────────


@dataclass
class PerfHarness:
    """Tiny facade around measurement + comparison + baseline bookkeeping."""

    baseline_store: BaselineStore | None = None

    def measure(
        self,
        fn: Callable[[], None],
        *,
        samples: int = 7,
        warmup: int = 2,
    ) -> MeasureResult:
        if samples < 1:
            raise ValueError(f"samples must be >= 1 (got {samples})")
        if warmup < 0:
            raise ValueError(f"warmup must be >= 0 (got {warmup})")
        for _ in range(warmup):
            fn()
        raw: list[float] = []
        for _ in range(samples):
            t0 = time.perf_counter()
            fn()
            raw.append(time.perf_counter() - t0)
        filtered = iqr_filter(raw)
        median = statistics.median(filtered) if filtered else statistics.median(raw)
        return MeasureResult(raw=tuple(raw), filtered=filtered, median=median)

    # ------------------------------------------------------------------

    def compare_to_baseline(
        self,
        *,
        measurement: float,
        baseline: float,
        allowed_delta_pct: float = 15.0,
        label: str = "",
    ) -> ComparisonResult:
        if baseline <= 0:
            delta_pct = float("inf") if measurement > 0 else 0.0
        else:
            delta_pct = (measurement - baseline) / baseline * 100.0
        # Speed-ups always pass; slowdowns must stay within tolerance.
        passed = delta_pct <= allowed_delta_pct
        reason = (
            f"{delta_pct:+.2f}% vs baseline (tolerance {allowed_delta_pct:+.2f}%)"
        )
        return ComparisonResult(
            label=label,
            measurement=measurement,
            baseline=baseline,
            allowed_delta_pct=allowed_delta_pct,
            passed=passed,
            delta_pct=delta_pct,
            reason=reason,
        )

    def compare_or_update(
        self,
        *,
        label: str,
        measurement: float,
        allowed_delta_pct: float = 15.0,
    ) -> ComparisonResult:
        """Compare ``measurement`` against the stored baseline for ``label``.

        Respects ``UPDATE_BASELINES=1`` in the environment — in that mode
        the harness overwrites the baseline with the new measurement and
        passes, so ``pytest -k perf`` can be rerun to re-pin metrics.
        """
        if self.baseline_store is None:
            raise RuntimeError(
                "compare_or_update requires a baseline_store — construct "
                "PerfHarness(baseline_store=BaselineStore(path))."
            )

        if os.environ.get("UPDATE_BASELINES") == "1":
            self.baseline_store.update(label, measurement)
            return ComparisonResult(
                label=label,
                measurement=measurement,
                baseline=measurement,
                allowed_delta_pct=allowed_delta_pct,
                passed=True,
                delta_pct=0.0,
                reason=f"baseline pinned via UPDATE_BASELINES=1 ({measurement})",
                updated_baseline=True,
            )

        data = self.baseline_store.load()
        if label not in data:
            return ComparisonResult(
                label=label,
                measurement=measurement,
                baseline=None,
                allowed_delta_pct=allowed_delta_pct,
                passed=False,
                delta_pct=0.0,
                reason=(
                    f"no baseline for {label!r} — rerun with UPDATE_BASELINES=1 "
                    "to pin the first measurement."
                ),
            )

        return self.compare_to_baseline(
            measurement=measurement,
            baseline=data[label],
            allowed_delta_pct=allowed_delta_pct,
            label=label,
        )

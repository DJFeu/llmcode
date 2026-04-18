"""Tests for the perf harness + baseline compare (H8a — Sprint 2).

Keep these deterministic — timing-sensitive assertions belong in the
real perf tests under tests/perf_tests/, not in the unit layer.
"""
from __future__ import annotations

import json

import pytest

from llm_code.perf import (
    BaselineStore,
    ComparisonResult,
    MeasureResult,
    PerfHarness,
    iqr_filter,
)


# ---------- iqr_filter ----------


class TestIqrFilter:
    def test_removes_high_outlier(self) -> None:
        samples = [1.0, 1.1, 1.05, 1.02, 10.0]  # 10.0 is an outlier
        filtered = iqr_filter(samples)
        assert 10.0 not in filtered
        assert 1.0 in filtered

    def test_removes_low_outlier(self) -> None:
        samples = [1.0, 1.1, 1.05, 1.02, 0.01]  # 0.01 is an outlier
        filtered = iqr_filter(samples)
        assert 0.01 not in filtered

    def test_preserves_ordering(self) -> None:
        samples = [3.0, 1.0, 2.0, 1.5, 2.5]
        filtered = iqr_filter(samples)
        # Input was unordered; filter must not reorder (caller may rely on it).
        assert list(filtered) == [s for s in samples if s in filtered]

    def test_small_sample_passthrough(self) -> None:
        """With fewer than 4 samples IQR is not meaningful — return as-is."""
        assert iqr_filter([1.0, 2.0, 3.0]) == (1.0, 2.0, 3.0)

    def test_configurable_k(self) -> None:
        samples = [1.0, 1.5, 2.0, 2.5, 3.0]
        # tighter k filters more
        tight = iqr_filter(samples, k=0.1)
        loose = iqr_filter(samples, k=5.0)
        assert len(tight) <= len(loose)


# ---------- PerfHarness.measure ----------


class TestMeasure:
    def test_runs_warmup_and_samples(self) -> None:
        calls = {"n": 0}

        def fn() -> None:
            calls["n"] += 1

        h = PerfHarness()
        h.measure(fn, samples=5, warmup=2)
        # 2 warmup + 5 measured = 7 total
        assert calls["n"] == 7

    def test_measure_returns_measure_result(self) -> None:
        h = PerfHarness()
        result = h.measure(lambda: None, samples=5, warmup=1)
        assert isinstance(result, MeasureResult)
        assert len(result.raw) == 5
        assert result.median >= 0.0
        # filtered is a subset of raw, preserves order
        assert all(v in result.raw for v in result.filtered)

    def test_invalid_samples_raise(self) -> None:
        h = PerfHarness()
        with pytest.raises(ValueError):
            h.measure(lambda: None, samples=0)
        with pytest.raises(ValueError):
            h.measure(lambda: None, samples=3, warmup=-1)


# ---------- compare_to_baseline ----------


class TestCompareToBaseline:
    def test_within_tolerance_passes(self) -> None:
        h = PerfHarness()
        cmp = h.compare_to_baseline(
            measurement=1.10,
            baseline=1.00,
            allowed_delta_pct=15.0,
        )
        assert cmp.passed is True
        assert abs(cmp.delta_pct - 10.0) < 1e-9

    def test_above_tolerance_fails(self) -> None:
        h = PerfHarness()
        cmp = h.compare_to_baseline(
            measurement=1.30,
            baseline=1.00,
            allowed_delta_pct=15.0,
        )
        assert cmp.passed is False
        assert cmp.delta_pct == pytest.approx(30.0)

    def test_below_tolerance_passes_is_false_when_slower(self) -> None:
        """Slower is a regression — must fail even if well below tolerance
        when tolerance is -5% (interpreted as 'no slowdown allowed')."""
        h = PerfHarness()
        cmp = h.compare_to_baseline(
            measurement=1.20,
            baseline=1.00,
            allowed_delta_pct=5.0,
        )
        assert cmp.passed is False

    def test_improvements_always_pass(self) -> None:
        """Any speedup passes regardless of tolerance — faster is fine."""
        h = PerfHarness()
        cmp = h.compare_to_baseline(
            measurement=0.50,
            baseline=1.00,
            allowed_delta_pct=15.0,
        )
        assert cmp.passed is True
        assert cmp.delta_pct == pytest.approx(-50.0)

    def test_comparison_result_shape(self) -> None:
        h = PerfHarness()
        cmp = h.compare_to_baseline(
            measurement=1.10, baseline=1.00, allowed_delta_pct=15.0,
        )
        assert isinstance(cmp, ComparisonResult)
        assert cmp.measurement == 1.10
        assert cmp.baseline == 1.00
        assert cmp.allowed_delta_pct == 15.0


# ---------- BaselineStore ----------


class TestBaselineStore:
    def test_read_missing_file_returns_empty(self, tmp_path) -> None:
        store = BaselineStore(tmp_path / "b.json")
        assert store.load() == {}

    def test_round_trip(self, tmp_path) -> None:
        path = tmp_path / "b.json"
        store = BaselineStore(path)
        store.write({"cold_start_s": 0.42, "profile_resolve_us": 0.001})
        # New store reads back the same values
        other = BaselineStore(path)
        data = other.load()
        assert data["cold_start_s"] == 0.42
        assert data["profile_resolve_us"] == 0.001

    def test_update_merges_into_existing(self, tmp_path) -> None:
        path = tmp_path / "b.json"
        store = BaselineStore(path)
        store.write({"a": 1.0, "b": 2.0})
        store.update("b", 2.5)
        store.update("c", 3.0)
        assert store.load() == {"a": 1.0, "b": 2.5, "c": 3.0}

    def test_write_creates_parent_dir(self, tmp_path) -> None:
        path = tmp_path / "nested" / "perf" / "baselines.json"
        BaselineStore(path).write({"x": 1.0})
        assert path.is_file()

    def test_file_format_is_json(self, tmp_path) -> None:
        """baselines.json must stay readable by humans + CI tooling."""
        path = tmp_path / "b.json"
        BaselineStore(path).write({"latency_ms": 12.5})
        text = path.read_text()
        parsed = json.loads(text)
        assert parsed == {"latency_ms": 12.5}
        # Pretty-printed so diffs stay reviewable.
        assert "\n" in text


# ---------- Update-mode wrapper ----------


class TestCompareOrUpdate:
    def test_update_mode_writes_baseline(self, tmp_path, monkeypatch) -> None:
        """When UPDATE_BASELINES=1 the harness writes the measurement
        as the new baseline and passes."""
        monkeypatch.setenv("UPDATE_BASELINES", "1")
        path = tmp_path / "b.json"
        h = PerfHarness(baseline_store=BaselineStore(path))
        cmp = h.compare_or_update(
            label="cold_start_s",
            measurement=0.42,
            allowed_delta_pct=15.0,
        )
        assert cmp.passed is True
        assert cmp.updated_baseline is True
        assert BaselineStore(path).load()["cold_start_s"] == 0.42

    def test_no_baseline_in_strict_mode_fails(self, tmp_path) -> None:
        """Without UPDATE_BASELINES, a missing baseline must fail so CI
        notices before metrics silently drift."""
        path = tmp_path / "b.json"
        h = PerfHarness(baseline_store=BaselineStore(path))
        cmp = h.compare_or_update(
            label="never_seen_metric",
            measurement=0.1,
            allowed_delta_pct=15.0,
        )
        assert cmp.passed is False
        assert "no baseline" in cmp.reason.lower()

    def test_compare_mode_uses_stored_baseline(self, tmp_path) -> None:
        path = tmp_path / "b.json"
        BaselineStore(path).write({"label1": 1.0})
        h = PerfHarness(baseline_store=BaselineStore(path))
        cmp = h.compare_or_update(
            label="label1",
            measurement=1.05,
            allowed_delta_pct=15.0,
        )
        assert cmp.passed is True
        assert cmp.updated_baseline is False

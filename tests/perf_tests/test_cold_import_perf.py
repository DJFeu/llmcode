"""Perf baseline: model_profile module cold-import time.

Runs the import in a fresh Python subprocess so pytest's module cache
doesn't shadow the measurement. Regressions here usually point at a
new top-level import that should be moved inside a function.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

from llm_code.perf import BaselineStore, PerfHarness

_BASELINES = Path(__file__).with_name("baselines.json")
_ALLOWED_DELTA = 50.0


def _subprocess_import(mod: str) -> float:
    """Return wall-clock seconds a child Python needs to import ``mod``."""
    script = textwrap.dedent(f"""
        import time
        t0 = time.perf_counter()
        import {mod}  # noqa: F401
        print(time.perf_counter() - t0)
    """)
    t0 = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    wall = time.perf_counter() - t0
    # Prefer the child-reported import time; fall back to wall-clock
    # so test stability doesn't hinge on stdout shape.
    try:
        return float(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return wall


def test_cold_import_model_profile() -> None:
    def bench() -> None:
        _subprocess_import("llm_code.runtime.model_profile")

    # Subprocess spawns are expensive — keep the sample count modest.
    harness = PerfHarness(baseline_store=BaselineStore(_BASELINES))
    result = harness.measure(bench, samples=4, warmup=1)
    cmp = harness.compare_or_update(
        label="cold_import_model_profile_median_s",
        measurement=result.median,
        allowed_delta_pct=_ALLOWED_DELTA,
    )
    assert cmp.passed, cmp.reason

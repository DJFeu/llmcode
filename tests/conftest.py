"""Top-level test configuration.

Exists so that ``tests`` is treated as a package root by pytest's
rootdir discovery and shared fixtures under ``tests/fixtures/`` can be
imported by absolute module path (``from tests.fixtures.runtime import
make_conv_runtime``) from every test file.

Also owns session-finish cleanup for OpenTelemetry global providers so
pytest doesn't emit noisy ``Transient error ... /v1/metrics ...
Connection refused`` lines after the run completes.
"""
from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Default-skip ``@pytest.mark.perf`` and ``@pytest.mark.slow`` tests.

    Perf benchmarks, soak tests, and memory profiles are timing-
    sensitive and prone to flake on shared CI runners. They are
    therefore opt-in: set ``LLMCODE_PERF=1`` to collect perf marks and
    ``LLMCODE_SLOW=1`` to collect slow marks. The nightly CI flips
    both flags; the default local + PR run stays fast + deterministic.
    """
    perf_enabled = os.environ.get("LLMCODE_PERF") == "1"
    slow_enabled = os.environ.get("LLMCODE_SLOW") == "1" or perf_enabled
    skip_perf = pytest.mark.skip(
        reason="perf tests disabled (set LLMCODE_PERF=1 to enable)"
    )
    skip_slow = pytest.mark.skip(
        reason="slow tests disabled (set LLMCODE_SLOW=1 or LLMCODE_PERF=1 to enable)"
    )
    for item in items:
        if "perf" in item.keywords and not perf_enabled:
            item.add_marker(skip_perf)
            continue
        if "slow" in item.keywords and not slow_enabled:
            item.add_marker(skip_slow)


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    """Shut down any live OTel TracerProvider/MeterProvider.

    A subset of telemetry tests (``tests/test_runtime/test_telemetry*``)
    installs a real ``TracerProvider`` with an OTLP exporter that targets
    ``localhost:4318``. No collector is running in CI, so the batch
    exporter's daemon thread keeps retrying after pytest has reported
    all tests green. Forcing a graceful shutdown here silences the
    retry log and prevents the daemon thread from outliving the process.
    """
    try:
        from opentelemetry import trace as _trace
    except Exception:  # pragma: no cover - OTel absent
        return

    try:
        provider = _trace.get_tracer_provider()
    except Exception:
        provider = None
    shutdown = getattr(provider, "shutdown", None)
    if callable(shutdown):
        try:
            shutdown()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass

    try:
        from opentelemetry import metrics as _metrics
    except Exception:  # pragma: no cover
        return

    try:
        meter_provider = _metrics.get_meter_provider()
    except Exception:
        meter_provider = None
    shutdown = getattr(meter_provider, "shutdown", None)
    if callable(shutdown):
        try:
            shutdown()
        except Exception:  # pragma: no cover
            pass

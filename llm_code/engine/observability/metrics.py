"""Prometheus metrics for the llmcode engine.

Six canonical metrics cover the engine's runtime behaviour:

* ``engine_pipeline_runs_total{outcome}``
* ``engine_pipeline_duration_seconds``
* ``engine_component_duration_seconds{component}``
* ``engine_agent_iterations_total{mode,exit_reason}``
* ``engine_tool_invocations_total{tool,status}``
* ``engine_api_tokens_total{direction,model}``

The ``prometheus_client`` dependency is **optional** — when it is not
installed (e.g. the core install without the ``[observability]`` extra)
this module transparently swaps in a no-op shim that preserves the
``Counter.labels(...).inc()`` / ``Histogram.observe(...)`` call surface
so engine call sites don't need to guard every recording.

When the dep *is* installed the shim is replaced by real
``prometheus_client.Counter`` / ``Histogram`` instances registered on a
dedicated :class:`~prometheus_client.CollectorRegistry`. That registry
is exposed as :data:`registry` so Hayhooks ``/metrics`` can serialise it
via :func:`prometheus_client.generate_latest`.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

try:
    from prometheus_client import (
        CollectorRegistry,
        Counter as _Counter,
        Histogram as _Histogram,
    )

    _PROM_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dep
    _PROM_AVAILABLE = False
    CollectorRegistry = None  # type: ignore[assignment,misc]
    _Counter = None  # type: ignore[assignment,misc]
    _Histogram = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# No-op shims (used when prometheus_client is unavailable)
# ---------------------------------------------------------------------------
class _NoopLabeledMetric:
    """Returned from ``_NoopMetric.labels(...)``; ignores inc/observe."""

    __slots__ = ()

    def inc(self, amount: float = 1.0) -> None:  # noqa: D401, ARG002
        return None

    def observe(self, value: float) -> None:  # noqa: D401, ARG002
        return None


class _NoopMetric:
    """Behavioural stub for Counter / Histogram when prometheus_client
    is missing. Keeps the engine call surface uniform."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def labels(self, **_kwargs: object) -> _NoopLabeledMetric:
        return _NoopLabeledMetric()

    def inc(self, amount: float = 1.0) -> None:  # noqa: ARG002
        return None

    def observe(self, value: float) -> None:  # noqa: ARG002
        return None


# ---------------------------------------------------------------------------
# Registry + canonical metrics
# ---------------------------------------------------------------------------
_NOOP_REGISTRY = None  # exposed for tests / introspection

if _PROM_AVAILABLE:
    registry: object = CollectorRegistry()

    pipeline_runs_total: object = _Counter(
        "engine_pipeline_runs",
        "Total pipeline runs by outcome",
        labelnames=("outcome",),
        registry=registry,
    )

    pipeline_duration_seconds: object = _Histogram(
        "engine_pipeline_duration_seconds",
        "Pipeline run duration in seconds",
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
        registry=registry,
    )

    component_duration_seconds: object = _Histogram(
        "engine_component_duration_seconds",
        "Per-component run duration in seconds",
        labelnames=("component",),
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
        registry=registry,
    )

    agent_iterations_total: object = _Counter(
        "engine_agent_iterations",
        "Agent iterations by mode and exit reason",
        labelnames=("mode", "exit_reason"),
        registry=registry,
    )

    tool_invocations_total: object = _Counter(
        "engine_tool_invocations",
        "Tool invocations by tool and status",
        labelnames=("tool", "status"),
        registry=registry,
    )

    api_tokens_total: object = _Counter(
        "engine_api_tokens",
        "API token counts by direction and model",
        labelnames=("direction", "model"),
        registry=registry,
    )
else:  # pragma: no cover - optional dep fallback
    registry = None
    pipeline_runs_total = _NoopMetric("engine_pipeline_runs")
    pipeline_duration_seconds = _NoopMetric("engine_pipeline_duration_seconds")
    component_duration_seconds = _NoopMetric("engine_component_duration_seconds")
    agent_iterations_total = _NoopMetric("engine_agent_iterations")
    tool_invocations_total = _NoopMetric("engine_tool_invocations")
    api_tokens_total = _NoopMetric("engine_api_tokens")


# ---------------------------------------------------------------------------
# Recording helpers (context managers)
# ---------------------------------------------------------------------------
@contextmanager
def record_pipeline_run() -> Iterator[None]:
    """Wrap a ``Pipeline.run`` call.

    * Observes ``engine_pipeline_duration_seconds`` with the elapsed time.
    * Increments ``engine_pipeline_runs_total`` with ``outcome="success"``
      if the wrapped block returns normally, ``outcome="error"`` if it
      raises.
    """
    start = time.perf_counter()
    outcome = "success"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        elapsed = time.perf_counter() - start
        pipeline_duration_seconds.observe(elapsed)
        pipeline_runs_total.labels(outcome=outcome).inc()


@contextmanager
def record_component(name: str) -> Iterator[None]:
    """Observe ``engine_component_duration_seconds{component=name}`` with
    the elapsed time of the wrapped block."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        component_duration_seconds.labels(component=name).observe(elapsed)


@contextmanager
def record_tool_invocation(tool: str, status: str) -> Iterator[None]:
    """Increment ``engine_tool_invocations_total`` after the wrapped
    block. If the block raises, the counter is still incremented using
    the caller-supplied ``status`` (typically ``"error"``)."""
    try:
        yield
    finally:
        tool_invocations_total.labels(tool=tool, status=status).inc()

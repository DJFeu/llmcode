"""Public tracing API for llmcode's engine.

This module is the single import surface for everything tracing-related:

* :func:`trace_init` — wires up the OTel ``TracerProvider`` + exporter +
  redaction filter, typically called once at engine boot.
* :func:`traced_component` / :func:`traced_pipeline` — class decorators
  that wrap ``run`` / ``run_async`` in a span.
* :func:`agent_span`, :func:`tool_call_span`, :func:`pipeline_span`,
  :func:`api_span` — scoped context managers used inside engine
  hotspots where a decorator is insufficient (nested iterations, ad hoc
  sub-spans, etc.).

**OpenTelemetry is an optional dependency.** When neither the
``opentelemetry-api`` nor ``opentelemetry-sdk`` package is installed —
for example, the core install without the ``[observability]`` extra —
every symbol here degrades to a no-op:

* ``trace_init`` returns immediately.
* Class decorators behave as the identity function.
* Context managers yield ``None`` and simply execute their body.

Call sites can therefore use the tracing API unconditionally without
having to guard every invocation with ``if OTEL_INSTALLED:``.
"""
from __future__ import annotations

import functools
import logging
from contextlib import contextmanager
from typing import Any, Iterator

from llm_code.engine.observability.attributes import (
    AGENT_ITERATION,
    AGENT_MODE,
    COMPONENT_NAME,
    MODEL_NAME,
    PIPELINE_NAME,
    TOOL_ARGS_HASH,
    TOOL_NAME,
)
from llm_code.engine.observability.redaction import RedactingFilter, Redactor

try:  # pragma: no cover - optional dep probe
    from opentelemetry import trace as _otel_trace  # type: ignore[import-not-found]

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dep fallback
    _otel_trace = None
    _OTEL_AVAILABLE = False


_TRACER_NAMESPACE = "llmcode"


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------
def trace_init(config: Any) -> None:
    """Configure the global tracer + exporter + log redaction.

    ``config`` is an :class:`llm_code.runtime.config.ObservabilityConfig`.
    The function is idempotent — repeat calls override the provider in
    place, matching the OTel SDK's own semantics.

    When :data:`_OTEL_AVAILABLE` is ``False`` the function returns
    without touching any global state (the redaction filter is still
    registered because it's pure Python and useful on its own).
    """
    # Redaction is cheap and has no heavy deps — install first so a
    # later exporter-setup exception doesn't skip it.
    if getattr(config, "redact_log_records", False):
        _install_redacting_filter()

    if not getattr(config, "enabled", True):
        return None

    exporter_kind = getattr(config, "exporter", "off")
    if exporter_kind == "off":
        return None

    if not _OTEL_AVAILABLE:
        # OTel missing — tracing is a structural no-op. We still
        # installed the redaction filter above.
        return None

    # OTel is available — a future task (6.7) ships real exporters;
    # for now we only wire the provider so spans created under
    # ``tracer.start_as_current_span`` participate in context
    # propagation correctly. Exporter construction is guarded so an
    # import failure from an exporter extra (langfuse, otlp-exporter)
    # does not break engine boot.
    try:  # pragma: no cover - exercised only when OTel is installed
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider

        resource = Resource.create({
            "service.name": getattr(config, "service_name", "llmcode"),
            "service.version": getattr(config, "service_version", "") or "dev",
        })
        provider = TracerProvider(resource=resource)
        _otel_trace.set_tracer_provider(provider)
    except Exception:  # pragma: no cover - defensive
        # Never let tracing bring the engine down.
        logging.getLogger(__name__).warning(
            "tracing provider init failed; continuing without tracing",
            exc_info=True,
        )

    return None


def _install_redacting_filter() -> None:
    """Attach a :class:`RedactingFilter` to the root logger if not
    already present. Safe to call multiple times."""
    root = logging.getLogger()
    for existing in root.filters:
        if isinstance(existing, RedactingFilter):
            return
    root.addFilter(RedactingFilter(Redactor()))


# ---------------------------------------------------------------------------
# Class decorators
# ---------------------------------------------------------------------------
def traced_component(cls: type) -> type:
    """Wrap ``cls.run`` and ``cls.run_async`` in a span that records
    :data:`COMPONENT_NAME`. Identity when OTel is missing.
    """
    return _wrap_runnable_class(cls, span_prefix="component", attr_key=COMPONENT_NAME)


def traced_pipeline(cls: type) -> type:
    """Wrap ``cls.run`` and ``cls.run_async`` in a pipeline-scoped span
    that records :data:`PIPELINE_NAME`. Identity when OTel is missing.
    """
    return _wrap_runnable_class(cls, span_prefix="pipeline", attr_key=PIPELINE_NAME)


def _wrap_runnable_class(cls: type, *, span_prefix: str, attr_key: str) -> type:
    """Shared impl for ``traced_component`` / ``traced_pipeline``."""
    if not _OTEL_AVAILABLE:
        return cls

    tracer = _otel_trace.get_tracer(_TRACER_NAMESPACE)

    orig_run = getattr(cls, "run", None)
    if callable(orig_run):
        @functools.wraps(orig_run)
        def wrapped_run(self: Any, *args: Any, **kwargs: Any) -> Any:
            name = type(self).__name__
            with tracer.start_as_current_span(
                f"{span_prefix}.{name}",
                attributes={attr_key: name},
            ):
                return orig_run(self, *args, **kwargs)

        cls.run = wrapped_run  # type: ignore[method-assign]

    orig_run_async = getattr(cls, "run_async", None)
    if callable(orig_run_async):
        @functools.wraps(orig_run_async)
        async def wrapped_run_async(self: Any, *args: Any, **kwargs: Any) -> Any:
            name = type(self).__name__
            with tracer.start_as_current_span(
                f"{span_prefix}.{name}",
                attributes={attr_key: name},
            ):
                return await orig_run_async(self, *args, **kwargs)

        cls.run_async = wrapped_run_async  # type: ignore[method-assign]

    return cls


# ---------------------------------------------------------------------------
# Context managers
# ---------------------------------------------------------------------------
@contextmanager
def pipeline_span(pipeline_name: str) -> Iterator[Any]:
    """Open a pipeline-scoped span. Yields the span (or ``None`` when
    OTel is missing) so callers can attach extra attributes."""
    if not _OTEL_AVAILABLE:
        yield None
        return
    tracer = _otel_trace.get_tracer(_TRACER_NAMESPACE)
    with tracer.start_as_current_span(
        f"pipeline.{pipeline_name}",
        attributes={PIPELINE_NAME: pipeline_name},
    ) as span:
        yield span


@contextmanager
def agent_span(iteration: int, mode: str) -> Iterator[Any]:
    """Open a span for a single agent iteration."""
    if not _OTEL_AVAILABLE:
        yield None
        return
    tracer = _otel_trace.get_tracer(_TRACER_NAMESPACE)
    with tracer.start_as_current_span(
        f"agent.iteration.{iteration}",
        attributes={AGENT_ITERATION: iteration, AGENT_MODE: mode},
    ) as span:
        yield span


@contextmanager
def tool_call_span(tool_name: str, args_hash: str) -> Iterator[Any]:
    """Open a span for a single tool invocation. ``args_hash`` must be
    the truncated SHA-256 produced by
    :func:`llm_code.engine.observability.attributes.args_hash` —
    raw arguments must **never** go on a span attribute (PII leak)."""
    if not _OTEL_AVAILABLE:
        yield None
        return
    tracer = _otel_trace.get_tracer(_TRACER_NAMESPACE)
    with tracer.start_as_current_span(
        f"tool.{tool_name}",
        attributes={TOOL_NAME: tool_name, TOOL_ARGS_HASH: args_hash},
    ) as span:
        yield span


@contextmanager
def api_span(model: str) -> Iterator[Any]:
    """Open a span for a single API / model call. Token usage should
    be attached as span events (not attributes) because events carry
    timestamps suitable for streaming chunks."""
    if not _OTEL_AVAILABLE:
        yield None
        return
    tracer = _otel_trace.get_tracer(_TRACER_NAMESPACE)
    with tracer.start_as_current_span(
        "api.stream",
        attributes={MODEL_NAME: model},
    ) as span:
        yield span

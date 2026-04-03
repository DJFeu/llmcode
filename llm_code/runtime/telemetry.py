"""OpenTelemetry observability for llm-code.

All OpenTelemetry imports are lazy — the module works as a no-op when the
``opentelemetry-*`` packages are not installed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TelemetryConfig:
    enabled: bool = False
    endpoint: str = "http://localhost:4318"  # OTLP HTTP default
    service_name: str = "llm-code"


# ---------------------------------------------------------------------------
# Telemetry class
# ---------------------------------------------------------------------------

class Telemetry:
    """Thin wrapper around OpenTelemetry tracing and metrics.

    When ``enabled=False`` or the ``opentelemetry-*`` packages are not
    installed every method is a no-op so callers need no guard clauses.
    """

    def __init__(self, config: TelemetryConfig) -> None:
        self._enabled = config.enabled
        self._tracer: Any = None
        self._cost_counter: Any = None
        self._error_counter: Any = None
        # Status/kind enums stored at setup so methods never re-import
        self._SpanKind: Any = None
        self._StatusCode: Any = None
        self._otel_available = False

        if not self._enabled:
            return

        try:
            self._setup(config)
            self._otel_available = True
        except Exception:
            # If setup fails for any reason (e.g., ImportError), degrade gracefully
            self._enabled = False

    # ------------------------------------------------------------------
    # Setup (only called when enabled and packages are present)
    # ------------------------------------------------------------------

    def _setup(self, config: TelemetryConfig) -> None:
        from opentelemetry import trace, metrics
        from opentelemetry.trace import SpanKind, StatusCode
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        # Store enums so methods don't need to re-import
        self._SpanKind = SpanKind
        self._StatusCode = StatusCode

        resource = Resource.create({"service.name": config.service_name})

        # Tracer
        span_exporter = OTLPSpanExporter(endpoint=f"{config.endpoint}/v1/traces")
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(tracer_provider)
        self._tracer = trace.get_tracer(config.service_name)

        # Meter
        metric_exporter = OTLPMetricExporter(endpoint=f"{config.endpoint}/v1/metrics")
        reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=60_000)
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(meter_provider)
        meter = metrics.get_meter(config.service_name)

        self._cost_counter = meter.create_counter(
            name="llm.cost.usd",
            unit="USD",
            description="Cumulative LLM cost in US dollars",
        )
        self._error_counter = meter.create_counter(
            name="llm.errors",
            description="Count of errors by type",
        )

    # ------------------------------------------------------------------
    # Public API — all methods are safe to call unconditionally
    # ------------------------------------------------------------------

    def trace_turn(
        self,
        session_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: float,
    ) -> None:
        """Record a completed LLM conversation turn as an OTel span."""
        if not self._enabled or self._tracer is None:
            return
        try:
            with self._tracer.start_as_current_span(
                "llm.turn",
                kind=self._SpanKind.CLIENT,
            ) as span:
                span.set_attribute("session.id", session_id)
                span.set_attribute("llm.model", model)
                span.set_attribute("llm.tokens.input", input_tokens)
                span.set_attribute("llm.tokens.output", output_tokens)
                span.set_attribute("llm.duration_ms", duration_ms)
                span.set_status(self._StatusCode.OK)
        except Exception:
            pass

    def trace_tool(
        self,
        tool_name: str,
        duration_ms: float,
        is_error: bool = False,
    ) -> None:
        """Record a tool execution as an OTel span."""
        if not self._enabled or self._tracer is None:
            return
        try:
            with self._tracer.start_as_current_span(
                f"tool.{tool_name}",
                kind=self._SpanKind.INTERNAL,
            ) as span:
                span.set_attribute("tool.name", tool_name)
                span.set_attribute("tool.duration_ms", duration_ms)
                span.set_attribute("tool.is_error", is_error)
                if is_error:
                    span.set_status(self._StatusCode.ERROR)
                else:
                    span.set_status(self._StatusCode.OK)
        except Exception:
            pass

    def record_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        """Record LLM cost as an OTel metric counter."""
        if not self._enabled or self._cost_counter is None:
            return
        try:
            self._cost_counter.add(
                cost_usd,
                attributes={
                    "llm.model": model,
                    "llm.tokens.input": input_tokens,
                    "llm.tokens.output": output_tokens,
                },
            )
        except Exception:
            pass

    def record_error(self, error_type: str, message: str) -> None:
        """Record an error event as an OTel counter increment."""
        if not self._enabled or self._error_counter is None:
            return
        try:
            self._error_counter.add(
                1,
                attributes={"error.type": error_type, "error.message": message[:256]},
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------

_NOOP = Telemetry(TelemetryConfig(enabled=False))


def get_noop_telemetry() -> Telemetry:
    """Return the shared no-op Telemetry instance."""
    return _NOOP

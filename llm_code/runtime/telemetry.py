"""OpenTelemetry observability for llm-code.

All OpenTelemetry imports are lazy — the module works as a no-op when the
``opentelemetry-*`` packages are not installed.
"""
from __future__ import annotations

from contextlib import contextmanager
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
    # Langfuse export (optional). When public_key + secret_key are set,
    # spans are also forwarded to a Langfuse instance via its OTel processor.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"


def _truncate_for_attribute(text: str, max_chars: int = 4096) -> str:
    """Truncate text for span attribute payloads."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncated]"


def _coerce_attr_value(value):
    """Coerce a Python value into something OTel attribute API accepts."""
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [str(v)[:1024] for v in value]
    return str(value)[:2048]


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

        # Optional Langfuse processor — installed alongside OTLP so spans
        # are exported to both when langfuse keys are set.
        if config.langfuse_public_key and config.langfuse_secret_key:
            try:
                from langfuse.otel import LangfuseSpanProcessor
                langfuse_processor = LangfuseSpanProcessor(
                    public_key=config.langfuse_public_key,
                    secret_key=config.langfuse_secret_key,
                    host=config.langfuse_host,
                )
                tracer_provider.add_span_processor(langfuse_processor)
            except ImportError:
                import logging
                logging.getLogger(__name__).warning(
                    "Telemetry: langfuse keys are set but the 'langfuse' "
                    "package is not installed. Continuing with OTLP only. "
                    "Install with: pip install 'llm-code[telemetry]'"
                )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "Telemetry: failed to register LangfuseSpanProcessor: %s", exc
                )

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
    # Canonical span context manager (use this for new instrumentation)
    # ------------------------------------------------------------------

    @contextmanager
    def span(self, name: str, **attributes):
        """Open a span as the current context manager.

        Yields the underlying OTel span (or ``None`` when telemetry is
        disabled). Nested ``with telemetry.span(...)`` calls form a tree
        because OTel uses an in-process context var to track the current
        span.
        """
        if not self._enabled or self._tracer is None:
            yield None
            return

        try:
            cm = self._tracer.start_as_current_span(name)
        except Exception:
            yield None
            return

        # Enter the underlying OTel CM. Failures here must NOT propagate to
        # the caller — degrade to a no-op span instead so the contract
        # "telemetry must never break the caller" is preserved.
        try:
            otel_span = cm.__enter__()
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "telemetry span %r failed on enter; ignoring", name
            )
            yield None
            return

        # From here on, we MUST call cm.__exit__ exactly once.
        _exited = False
        try:
            try:
                for key, value in attributes.items():
                    if value is None:
                        continue
                    otel_span.set_attribute(key, _coerce_attr_value(value))
            except Exception:
                pass

            try:
                yield otel_span
            except Exception as exc:
                # Caller raised — mark span error, then propagate.
                try:
                    otel_span.set_status(self._StatusCode.ERROR)
                    otel_span.record_exception(exc)
                except Exception:
                    pass
                # Hand the exception to cm.__exit__; if exit suppresses it
                # (returns truthy) we honor that, otherwise re-raise.
                exc_type, exc_val, exc_tb = type(exc), exc, exc.__traceback__
                try:
                    suppressed = cm.__exit__(exc_type, exc_val, exc_tb)
                except Exception:
                    suppressed = False
                _exited = True
                if not suppressed:
                    raise
                return
            else:
                try:
                    otel_span.set_status(self._StatusCode.OK)
                except Exception:
                    pass
        finally:
            # Normal-exit path: close the CM, swallowing any OTel-layer error.
            if not _exited:
                try:
                    cm.__exit__(None, None, None)
                except Exception:
                    import logging
                    logging.getLogger(__name__).debug(
                        "telemetry span %r failed on exit; ignoring", name
                    )

    @contextmanager
    def trace_llm_completion(
        self,
        *,
        session_id: str,
        model: str,
        prompt_preview: str = "",
        completion_preview: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        provider: str = "",
        finish_reason: str = "",
    ):
        """Open a span for one LLM completion call inside the current turn."""
        attrs = {
            "session.id": session_id,
            "llm.model": model,
            "llm.provider": provider,
            "llm.tokens.input": input_tokens,
            "llm.tokens.output": output_tokens,
            "llm.tokens.total": input_tokens + output_tokens,
            "llm.prompt.preview": _truncate_for_attribute(prompt_preview),
            "llm.completion.preview": _truncate_for_attribute(completion_preview),
            "llm.finish_reason": finish_reason,
        }
        with self.span("llm.completion", **attrs) as s:
            yield s

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

    def record_fallback(
        self,
        *,
        from_model: str,
        to_model: str,
        reason: str,
    ) -> None:
        """Record a model-fallback switch as an OTel span.

        Wave2-3: emitted by ``ConversationRuntime`` when ``FallbackChain``
        walks one step down because the active model exhausted its
        retry budget. Complements the ``http_fallback`` hook — the hook
        is for in-process observers (e.g. the TUI status bar), this
        span is for external tracing backends (Jaeger, Honeycomb, etc.)
        so operators can chart "how often am I falling back from Claude
        to Haiku this week" without parsing logs.

        ``reason`` is one of the FallbackChain kinds (``consecutive_
        failures``, ``xml_mode``, …) and is kept as a plain string so
        downstream attribute backends can facet on it.
        """
        if not self._enabled or self._tracer is None:
            return
        try:
            with self._tracer.start_as_current_span(
                "llm.fallback",
                kind=self._SpanKind.INTERNAL,
            ) as span:
                span.set_attribute("llm.fallback.from", from_model)
                span.set_attribute("llm.fallback.to", to_model)
                span.set_attribute("llm.fallback.reason", reason)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------

_NOOP = Telemetry(TelemetryConfig(enabled=False))


def get_noop_telemetry() -> Telemetry:
    """Return the shared no-op Telemetry instance."""
    return _NOOP
